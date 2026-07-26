/* Practice -- what actually happened, not what you meant to do.
 *
 * The headline number is time with a note under your hands, idle-gapped. Everything
 * else exists to answer one of two questions: did I show up, and is my playing
 * getting steadier. */

import { $, api, h, hms, humanMinutes, mod, noteName, stat, toast } from '../ui.js';

let stats = null;

export default {
  async mount(root, ctx) {
    root.append(h('div.grid', { id: 'practice-grid' },
      h('div.col-12', null, h('div.empty', null, 'loading practice history...'))));
    await load(ctx);
  },

  status(s) {
    const live = $('#live-session');
    if (!live || !s.practice) return;
    const p = s.practice;
    live.replaceChildren(
      stat(hms(p.session_seconds), 'This session',
           p.session_active ? (p.idle ? 'idle -- clock paused' : 'playing') : 'not started',
           p.idle ? '' : 'stat__value--amber'),
      stat(p.session_notes ?? 0, 'Notes this session'),
      stat(hms(p.today_seconds), 'Today', `${p.today_sessions || 0} session(s)`,
           'stat__value--amber'),
      stat(p.streak?.current ?? 0, 'Day streak',
           `longest ${p.streak?.longest ?? 0}`, 'stat__value--cyan'));

    const t = s.timing || {};
    const host = $('#timing-live');
    if (host) host.replaceChildren(...timingCards(t));
  },
};

async function load(ctx) {
  let data;
  try {
    data = await api.get('/api/stats?days=90');
  } catch (err) {
    $('#practice-grid').replaceChildren(h('div.col-12', null,
      h('div.empty', null, 'could not load stats: ' + err.message)));
    return;
  }
  stats = data;
  const fixedTouch = data.velocity_distinct_today === 1;

  $('#practice-grid').replaceChildren(
    // The one warning worth interrupting for: with Touch Sensitivity on Fixed, every
    // note is velocity 64 and no amount of software can give you dynamics.
    fixedTouch ? h('div.col-12', null, mod('Your piano is in fixed-velocity mode', 'fix this first',
      h('div.note.note--warn', null,
        'Every note today came in at the same velocity, which means the P-71B\'s Touch ',
        'Sensitivity is set to ', h('strong', null, 'Fixed'), '. Dynamics are impossible ',
        'until that changes, and velocity curves operate on a constant. ',
        h('strong', null, 'Hold [GRAND PIANO/FUNCTION] and press B2'),
        ' -- the white key immediately left of middle C -- to get Medium, the factory ',
        'default. Then play something loud and something soft and reload this page.'))) : null,

    h('div.col-12', null, mod('Now', null, h('div.stats', { id: 'live-session' },
      h('div.empty', null, 'waiting for the first note')))),

    h('div.col-8', null, mod('Last 90 days', `${data.history.filter((d) => d.active_seconds > 0).length} days played`,
      calendar(data.history),
      h('div.note', { style: { marginTop: '12px' } },
        'Daily frequency is the part with real evidence behind it. ',
        'The "30 minutes a day" figure is folklore -- showing up is what matters.'))),

    h('div.col-4', null, mod('Totals', null, h('div.stats', null,
      stat(humanMinutes(data.history.reduce((a, d) => a + d.active_seconds, 0)), '90-day total'),
      stat(data.history.reduce((a, d) => a + d.note_count, 0).toLocaleString(), 'Notes'),
      stat(data.streak.current, 'Streak', `longest ${data.streak.longest}`,
           'stat__value--amber')))),

    h('div.col-6', null, mod('Timing', 'from your last ~96 notes',
      h('div.stats', { id: 'timing-live' }, h('div.empty', null, 'play something')),
      h('div.note', { style: { marginTop: '12px' } },
        'Drift is the number to watch, not per-beat error. Players hold even spacing ',
        'while the whole tempo slides -- that is the failure mode the research found.'))),

    h('div.col-6', null, mod('Velocity', `${data.velocity_distinct_today} distinct today`,
      histogram(data.velocity_histogram),
      h('div.note', { style: { marginTop: '10px' } },
        fixedTouch ? 'One bar means fixed velocity.'
                   : 'A spread across the range means touch response is working.'))),

    h('div.col-12', null, mod('Which keys you actually use', 'last 90 days',
      keymap(data.heatmap))),

    h('div.col-12', null, mod('Recent sessions', null,
      data.sessions.length
        ? h('div.list', null, data.sessions.slice(0, 12).map(sessionRow))
        : h('div.empty', null, 'no sessions yet'))),
  );
}

function timingCards(t) {
  if (!t || !t.tempo || t.tempo.bpm == null) {
    return [h('div.empty', null, 'not enough notes yet')];
  }
  const d = t.drift || {};
  const s = t.steadiness || {};
  const g = t.grid;
  const out = [
    stat(Math.round(t.tempo.bpm), 'Tempo', 'bpm, median IOI', 'stat__value--amber'),
    stat(s.rating || '--', 'Steadiness', s.cv != null ? `cv ${s.cv.toFixed(3)}` : ''),
  ];
  if (d.bpm_per_min != null) {
    out.push(stat(
      (d.bpm_per_min > 0 ? '+' : '') + d.bpm_per_min.toFixed(1),
      'Drift', d.steady ? 'holding tempo' : (d.bpm_per_min < 0 ? 'slowing down' : 'speeding up'),
      d.steady ? 'stat__value--cyan' : 'stat__value--amber'));
  }
  if (g && g.n) {
    out.push(stat(
      (g.mean_ms > 0 ? '+' : '') + g.mean_ms.toFixed(0),
      'Vs click', g.rushing ? 'rushing' : g.dragging ? 'dragging' : 'on the beat'));
  }
  return out;
}

/* GitHub-style calendar, 7 rows of weekdays. Empty days have to be drawn or the
   gaps in a practice habit become invisible, which is the whole point. */
function calendar(history) {
  const byDate = new Map(history.map((d) => [d.date, d]));
  const cells = [];
  const today = new Date();
  const start = new Date(today);
  start.setDate(start.getDate() - 89);
  start.setDate(start.getDate() - start.getDay()); // back to Sunday

  for (let d = new Date(start); d <= today; d.setDate(d.getDate() + 1)) {
    const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    const row = byDate.get(iso);
    const secs = row ? row.active_seconds : 0;
    const level = secs === 0 ? 0 : secs < 300 ? 1 : secs < 900 ? 2 : secs < 1800 ? 3 : 4;
    cells.push(h('div.cal__day', {
      'data-l': level,
      title: `${iso} -- ${secs ? humanMinutes(secs) : 'nothing'}`,
    }));
  }
  return h('div.cal', null, cells);
}

function histogram(buckets) {
  const max = Math.max(1, ...buckets);
  return h('div.spark', null, buckets.map((v, i) => h('div.spark__bar', {
    class: v > 0 ? 'is-hot' : '',
    style: { height: `${Math.max(2, (v / max) * 100)}%` },
    title: `velocity ${Math.round(i * 127 / buckets.length) + 1}-${Math.round((i + 1) * 127 / buckets.length)}: ${v}`,
  })));
}

function keymap(heat) {
  const max = Math.max(1, ...Object.values(heat).map(Number));
  const keys = [];
  for (let n = 21; n <= 108; n++) {
    const v = Number(heat[n] || heat[String(n)] || 0);
    keys.push(h('div.keymap__k', {
      style: {
        height: `${Math.max(3, (v / max) * 100)}%`,
        background: v ? `color-mix(in srgb, var(--amber) ${20 + (v / max) * 80}%, var(--panel-4))` : '',
      },
      title: `${noteName(n)}: ${v}`,
    }));
  }
  return h('div.keymap', null, keys);
}

function sessionRow(s) {
  const when = new Date((s.started_at || 0) * 1000);
  return h('div.list__row', null,
    h('span.mono', null, when.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })),
    h('span.mono', null, when.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })),
    h('span', null, s.preset || '--'),
    h('span.list__spacer'),
    h('span.mono', null, `${s.note_count || 0} notes`),
    h('span.tag.tag--amber', null, hms((s.active_ms || 0) / 1000)));
}
