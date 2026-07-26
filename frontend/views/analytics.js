/* Analytics -- the long view.
 *
 * Practice answers "did I show up today". This answers "what have I actually been
 * playing", over a year: which keys, which chords, which hours of the day, and
 * whether the dynamics are opening up. It is a read-only mirror of the log, built
 * once in mount() from a single request. Nothing here ticks, polls or animates,
 * because none of these numbers change while you are looking at them.
 *
 * Every panel draws its own empty state on purpose. A log with nothing in it should
 * look like an instrument that has not been played yet, not like a broken page --
 * the blank year grid and the cold 88-key map ARE the reading. */

import { $, api, h, hms, humanMinutes, mod, noteName, stat } from '../ui.js';

let data = null;

export default {
  async mount(root) {
    root.append(h('div.grid', { id: 'an-grid' },
      h('div.col-12', null, h('div.empty', null, 'reading a year of practice...'))));

    try {
      data = await api.get('/api/analytics?days=365');
    } catch (err) {
      $('#an-grid').replaceChildren(h('div.col-12', null,
        h('div.empty', null, 'could not load analytics: ' + err.message)));
      return;
    }
    render(data);
  },

  /* The only thing on this page that can go stale while you read it is today.
     One text node, mutated in place -- no panel is rebuilt. */
  status(s) {
    const el = $('#an-today');
    if (!el || !s.practice) return;
    const p = s.practice;
    el.textContent = p.today_seconds
      ? `Today so far: ${hms(p.today_seconds)} over ${p.today_sessions || 1} session(s).`
      : 'Nothing logged today yet.';
  },

  unmount() { data = null; },
};

/* ── layout ───────────────────────────────────────────────────────────────── */
function render(d) {
  const streak = d.streak || {};
  const totals = d.totals || {};
  const seconds = totals.active_seconds ?? streak.total_active_seconds ?? 0;
  const days = totals.days_practiced ?? streak.total_days ?? 0;

  $('#an-grid').replaceChildren(
    h('div.col-12', null, mod('All time', d.range_days ? `last ${d.range_days} days` : null,
      h('div.stats', null,
        stat(humanMinutes(seconds), 'Total practice', `${count(totals.sessions)} sessions`,
             'stat__value--amber'),
        stat(streak.current ?? 0, 'Day streak',
             `longest ${streak.longest ?? 0}`,
             streak.practiced_today ? 'stat__value--amber' : ''),
        stat(days, 'Days played', since(totals.first_at)),
        stat(count(totals.note_count), 'Notes', `${count(totals.chords)} chords`,
             'stat__value--cyan')),
      h('div.note', { id: 'an-today', style: { marginTop: '12px' } },
        'Nothing logged today yet.'))),

    h('div.col-12', null, mod('Activity', `${days} days with a note on them`,
      calendar(d.calendar))),

    h('div.col-12', null, mod('Keys you have played', 'every note, all time',
      pianoMap(d.note_heatmap, d.range),
      rangeLine(d.range))),

    h('div.col-7', null, mod('What key you play in', null,
      keyList(d.keys))),

    h('div.col-5', null, mod('Chromatic circle', 'notes by pitch class',
      wheel(d.pitch_classes))),

    h('div.col-6', null, mod('Chords', 'most played',
      chordList(d.top_chords))),

    h('div.col-6', null, mod('Chord qualities', null,
      qualities(d.chord_qualities))),

    h('div.col-12', null, mod('Intervals', 'between consecutive notes',
      intervals(d.intervals))),

    h('div.col-6', null, mod('When you practise', 'hour of the day',
      hours(d.hours))),

    h('div.col-6', null, mod('Which day', null,
      weekdays(d.weekdays))),

    h('div.col-6', null, mod('Velocity', 'mean, with the range you covered',
      velocity(d.velocity_by_day),
      h('div.note', { style: { marginTop: '10px' } },
        'The band is the softest and loudest note of the day. Its widening is what ',
        'dynamics developing actually looks like; a flat line means fixed touch.'))),

    h('div.col-6', null, mod('Notes per minute', 'while actually playing',
      speed(d.notes_per_minute),
      h('div.note', { style: { marginTop: '10px' } },
        'Idle time is already subtracted, so this is density, not diligence. ',
        'It goes up with faster pieces and down with harder ones.'))),

    h('div.col-4', null, mod('Session length', null,
      sessionLengths(d.session_lengths))),

    h('div.col-4', null, mod('Sounds you reach for', null,
      presets(d.presets))),

    h('div.col-4', null, mod('Sight reading', 'all attempts',
      sightread(d.sightread))),
  );
}

/* ── small shared bits ────────────────────────────────────────────────────── */
const nothing = (msg) => h('div.empty', null, msg);
const count = (n) => Number(n || 0).toLocaleString();
const pct = (f) => `${Math.round((f || 0) * 100)}%`;

function since(epoch) {
  if (!epoch) return '';
  return 'since ' + new Date(epoch * 1000)
    .toLocaleDateString(undefined, { month: 'short', year: 'numeric' });
}

const SVG_NS = 'http://www.w3.org/2000/svg';

function svg(tag, attrs, text) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
  if (text !== undefined) node.textContent = text;
  return node;
}

/* A column chart with its own tick row. Bars and ticks are separate flex rows so a
   bar's height is a clean percentage of the plot and never fights its label. */
function barChart(values, labels, opts = {}) {
  const max = Math.max(1, ...values);
  return h('div.chart', null,
    h('div.chart__bars', null, values.map((v, i) => h(
      'div.chart__bar' + (v ? (opts.cyan ? '.chart__bar--cyan' : '') : '.chart__bar--off'), {
        style: { height: v ? `${Math.max(3, (v / max) * 100)}%` : '2%' },
        title: opts.title ? opts.title(i, v) : `${labels[i]}: ${count(v)}`,
      }))),
    h('div.chart__ticks', null, labels.map((t, i) => h('div.chart__tick', null,
      opts.every && i % opts.every ? '' : t))));
}

/* ── activity calendar ────────────────────────────────────────────────────── */
const WEEKS = 53;
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

/* 53 columns of 7 days, Monday on top. The empty cells are the whole reason this
   chart exists -- a habit is legible in its gaps, not its good weeks. */
function calendar(rows) {
  const byDate = new Map((rows || []).map((r) => [r.date, r]));
  // Midday, so no daylight-saving jump can push a day into its neighbour.
  const today = new Date();
  today.setHours(12, 0, 0, 0);
  const todayISO = iso(today);

  const cursor = new Date(today);
  // Back to this week's Monday (getDay is 0=Sunday), then back 52 more weeks.
  cursor.setDate(cursor.getDate() - ((cursor.getDay() + 6) % 7) - (WEEKS - 1) * 7);

  const months = [];
  const cells = [];
  let lastMonth = -1;

  for (let w = 0; w < WEEKS; w++) {
    if (cursor.getMonth() !== lastMonth) {
      lastMonth = cursor.getMonth();
      // No label in the final column; there is nowhere for the text to go.
      if (w < WEEKS - 1) {
        months.push(h('div.calx__month', { style: { gridColumn: String(w + 1) } },
          MONTHS[lastMonth]));
      }
    }
    // Column-major append order, because .cal flows down each column first.
    for (let dow = 0; dow < 7; dow++) {
      const day = iso(cursor);
      if (day > todayISO) {
        cells.push(h('div.cal__day.cal__day--void'));
      } else {
        const row = byDate.get(day);
        const secs = row ? row.active_seconds || 0 : 0;
        const notes = row ? row.note_count || 0 : 0;
        cells.push(h('div.cal__day', {
          'data-l': secs === 0 ? 0 : secs < 300 ? 1 : secs < 900 ? 2 : secs < 1800 ? 3 : 4,
          title: secs
            ? `${day} -- ${humanMinutes(secs)}, ${count(notes)} notes`
            : `${day} -- nothing`,
        }));
      }
      cursor.setDate(cursor.getDate() + 1);
    }
  }

  return h('div.calx', null,
    h('div.calx__scroll', null,
      h('div.calx__months', { style: { gridTemplateColumns: `repeat(${WEEKS}, 11px)` } },
        months),
      h('div.calx__rows', null,
        h('div.calx__wd', null,
          ['Mon', '', 'Wed', '', 'Fri', '', ''].map((t) => h('span', null, t))),
        h('div.cal', null, cells))),
    h('div.cal__legend', null, 'Less',
      [0, 1, 2, 3, 4].map((l) => h('div.cal__day', { 'data-l': l })),
      'More'));
}

/* ── the 88-key map ───────────────────────────────────────────────────────── */
/* Geometry copied deliberately from keyboard.js rather than imported: that module
   exports the live instrument and nothing else, and this is a static picture with no
   pointer handling. The numbers below must stay in step with it, so the derivation is
   reproduced instead of the results -- black keys are NOT centred on the joint between
   two whites, and eyeballing the offsets is what makes a drawn keyboard look wrong. */
const LOW = 21;
const HIGH = 108;
const WHITE_INDEX = [0, -1, 1, -1, 2, 3, -1, 4, -1, 5, -1, 6];
const WHITE_W = 24;
const WHITE_H = 148;
const B = 0.60;                        // black key width, in white-key widths
const BLACK_W = WHITE_W * B;
const BLACK_H = WHITE_H * 0.62;
const CDE_BACK = (3 - 2 * B) / 3;      // the narrow back of a white key, C-D-E group
const FGAB_BACK = (4 - 3 * B) / 4;     // ... and in the F-G-A-B group
const BLACK_LEFT = {
  1: CDE_BACK,                         // C#
  3: 2 * CDE_BACK + B,                 // D#
  6: 3 + FGAB_BACK,                    // F#
  8: 3 + 2 * FGAB_BACK + B,            // G#
  10: 3 + 3 * FGAB_BACK + 2 * B,       // A#
};

const whiteOrdinal = (m) => Math.floor(m / 12) * 7 + WHITE_INDEX[m % 12];

// Only the bottom corners are rounded; rx would round all four and read as a toy.
function keyPath(x, w, hgt, r) {
  return `M${x} 0h${w}v${hgt - r}a${r} ${r} 0 0 1 ${-r} ${r}h${-(w - 2 * r)}a${r} ${r} 0 0 1 ${-r} ${-r}z`;
}

function pianoMap(heat, range) {
  const counts = new Array(129).fill(0);
  let max = 0;
  for (const [k, v] of Object.entries(heat || {})) {
    const n = Number(k);
    const c = Number(v) || 0;
    if (n >= LOW && n <= HIGH && c > 0) {
      counts[n] = c;
      if (c > max) max = c;
    }
  }

  const base = whiteOrdinal(LOW);
  const width = (whiteOrdinal(HIGH) - base + 1) * WHITE_W;
  const root = svg('svg', {
    class: 'pianomap', viewBox: `0 0 ${width} ${WHITE_H}`,
    preserveAspectRatio: 'xMidYMid meet', role: 'img',
    'aria-label': 'how often each of the 88 keys has been played',
  });
  // Two groups, whites first: SVG paints in document order, so blacks land on top.
  const whites = svg('g');
  const blacks = svg('g');
  root.append(whites, blacks);

  for (let n = LOW; n <= HIGH; n++) {
    const pcl = n % 12;
    const isWhite = WHITE_INDEX[pcl] >= 0;
    const x = isWhite
      ? (whiteOrdinal(n) - base) * WHITE_W
      : (Math.floor(n / 12) * 7 - base + BLACK_LEFT[pcl]) * WHITE_W;
    const key = svg('path', {
      class: 'pkey' + (isWhite ? '' : ' pkey--black'),
      d: keyPath(x, isWhite ? WHITE_W : BLACK_W, isWhite ? WHITE_H : BLACK_H, isWhite ? 3 : 2),
    });
    key.style.fill = heatFill(counts[n], max, isWhite);
    key.append(svg('title', null, `${noteName(n)} -- ${count(counts[n])} plays`));
    (isWhite ? whites : blacks).append(key);
  }
  return root;
}

/* sqrt, not linear: one runaway note (middle C, always) otherwise compresses every
   other key back to the unplayed colour and the map says nothing. Black keys get a
   darker floor or the keyboard loses its shape when nothing has been played. */
function heatFill(c, max, isWhite) {
  const floor = isWhite ? 'var(--panel-4)' : 'var(--panel-2)';
  if (!c || !max) return floor;
  const t = Math.sqrt(c / max);
  return `color-mix(in oklab, var(--amber) ${(12 + t * 88).toFixed(0)}%, ${floor})`;
}

function rangeLine(range) {
  if (!range || !range.span) return h('div.note', { style: { marginTop: '12px' } },
    'No notes logged yet -- play anything and the keys light up here.');
  return h('div.stats', { style: { marginTop: '14px' } },
    stat(`${range.low_name || '--'}-${range.high_name || '--'}`, 'Range used',
         `${range.span} semitones`, 'stat__value--amber'),
    stat(pct(range.coverage), 'Of the 88', 'keys touched at least once',
         'stat__value--cyan'));
}

/* ── keys and pitch classes ───────────────────────────────────────────────── */
function keyList(keys) {
  if (!keys || !keys.length) return nothing('not enough notes to guess a key yet');
  const top = Math.max(...keys.map((k) => k.share || 0), 0.0001);
  return h('div.list', null, keys.slice(0, 8).map((k) => h('div.list__row', null,
    h('span', { style: { minWidth: '96px' } }, k.name || `${k.key} ${k.mode}`),
    h('div.bar', { style: { flex: '1' } },
      h('div.bar__fill', { style: { width: pct((k.share || 0) / top) } })),
    h('span.mono', null, pct(k.share)),
    h('span.tag' + (k.score >= 0.75 ? '.tag--amber' : ''), null,
      `fit ${(k.score ?? 0).toFixed(2)}`))));
}

const PC_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];

/* The chromatic circle, C at twelve o'clock. A bar chart would order the pitch
   classes arbitrarily; on a circle the shape you see is the shape of the key. */
function wheel(values) {
  const pcs = Array.isArray(values) && values.length === 12 ? values.map(Number) : null;
  if (!pcs || !pcs.some((v) => v > 0)) return nothing('no notes yet');

  const max = Math.max(...pcs);
  const R = 100, INNER = 26, OUTER = 82, LABEL = 93;
  const root = svg('svg', {
    class: 'wheel', viewBox: '0 0 200 200', role: 'img',
    'aria-label': 'how often each pitch class was played',
  });
  root.append(svg('circle', { class: 'wheel__ring', cx: R, cy: R, r: OUTER }));

  pcs.forEach((v, i) => {
    const a = (i * 30 - 90) * Math.PI / 180;
    const end = INNER + (max ? (v / max) * (OUTER - INNER) : 0);
    const spoke = svg('line', {
      class: 'wheel__spoke' + (v ? '' : ' is-off'),
      x1: (R + Math.cos(a) * INNER).toFixed(2), y1: (R + Math.sin(a) * INNER).toFixed(2),
      x2: (R + Math.cos(a) * Math.max(end, INNER + 1)).toFixed(2),
      y2: (R + Math.sin(a) * Math.max(end, INNER + 1)).toFixed(2),
    });
    spoke.append(svg('title', null, `${PC_NAMES[i]} -- ${count(v)} notes`));
    root.append(spoke);
    root.append(svg('text', {
      class: 'wheel__label',
      x: (R + Math.cos(a) * LABEL).toFixed(2),
      y: (R + Math.sin(a) * LABEL).toFixed(2),
    }, PC_NAMES[i]));
  });
  return root;
}

/* ── chords ───────────────────────────────────────────────────────────────── */
function chordList(chords) {
  if (!chords || !chords.length) return nothing('no chords detected yet');
  const max = Math.max(...chords.map((c) => c.count || 0), 1);
  return h('div.list', null, chords.slice(0, 12).map((c) => h('div.list__row', null,
    h('span.mono', { style: { minWidth: '74px' } }, c.symbol || '--'),
    h('div.bar', { style: { flex: '1' } },
      h('div.bar__fill', { style: { width: pct((c.count || 0) / max) } })),
    h('span.mono', null, count(c.count)))));
}

// Only tokens -- the palette is the constraint, so five qualities is the honest
// maximum before the segments stop being distinguishable.
const QUALITY_COLOURS = ['var(--amber)', 'var(--cyan)', 'var(--amber-deep)',
                         'var(--green)', 'var(--ink-faint)'];

function qualities(rows) {
  if (!rows || !rows.length) return nothing('no chords detected yet');
  const shown = rows.slice(0, QUALITY_COLOURS.length);
  const total = rows.reduce((a, r) => a + (r.count || 0), 0) || 1;
  return h('div', null,
    h('div.stack', null, shown.map((r, i) => h('div.stack__seg', {
      style: { width: pct((r.count || 0) / total), background: QUALITY_COLOURS[i] },
      title: `${r.quality}: ${count(r.count)}`,
    }))),
    h('div.legend', null, shown.map((r, i) => h('div.legend__item', null,
      h('div.legend__swatch', { style: { background: QUALITY_COLOURS[i] } }),
      `${r.quality} ${pct((r.count || 0) / total)}`))));
}

/* ── intervals, clock, calendar of the week ───────────────────────────────── */
function intervals(rows) {
  if (!rows || !rows.length) return nothing('not enough notes yet');
  // Sorted by size, never by count: an interval axis that is not in order of
  // semitones is unreadable, however tall the bars are.
  const sorted = [...rows].sort((a, b) => (a.semitones || 0) - (b.semitones || 0));
  return barChart(
    sorted.map((r) => r.count || 0),
    sorted.map((r) => r.name || String(r.semitones)),
    { title: (i, v) => `${sorted[i].name || sorted[i].semitones} -- ${count(v)}` });
}

function hours(rows) {
  const v = Array.isArray(rows) && rows.length === 24 ? rows.map(Number) : null;
  if (!v || !v.some((n) => n > 0)) return nothing('no sessions yet');
  return barChart(v, v.map((_, i) => String(i)), {
    every: 3,
    title: (i, n) => `${String(i).padStart(2, '0')}:00 -- ${count(n)} notes`,
  });
}

const DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

function weekdays(rows) {
  const v = Array.isArray(rows) && rows.length === 7 ? rows.map(Number) : null;
  if (!v || !v.some((n) => n > 0)) return nothing('no sessions yet');
  return barChart(v, DAY_NAMES, { cyan: true });
}

/* ── trends ───────────────────────────────────────────────────────────────── */
/* One sparkline. preserveAspectRatio="none" lets it fill whatever width the panel
   has; every stroke is non-scaling so that stretch does not smear the line weight. */
function trend(series, opts = {}) {
  const pts = series.filter((p) => p.mid != null && Number.isFinite(p.mid));
  if (pts.length < 2) return nothing(opts.empty || 'not enough days yet');

  const W = 300, H = 80, PAD = 3;
  let lo = Infinity, hi = -Infinity;
  for (const p of pts) {
    lo = Math.min(lo, p.lo ?? p.mid);
    hi = Math.max(hi, p.hi ?? p.mid);
  }
  if (!(hi > lo)) hi = lo + 1;

  const x = (i) => ((i / (pts.length - 1)) * W).toFixed(2);
  const y = (v) => (H - PAD - ((v - lo) / (hi - lo)) * (H - 2 * PAD)).toFixed(2);

  const root = svg('svg', {
    class: 'trend', viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: 'none', role: 'img',
    'aria-label': `${opts.label || 'trend'} over ${pts.length} days`,
  });

  const line = pts.map((p, i) => `${i ? 'L' : 'M'}${x(i)} ${y(p.mid)}`).join('');

  if (opts.band && pts.some((p) => p.lo != null && p.hi != null)) {
    // Out along the daily maximum, back along the daily minimum, closed.
    let band = pts.map((p, i) => `${i ? 'L' : 'M'}${x(i)} ${y(p.hi ?? p.mid)}`).join('');
    for (let i = pts.length - 1; i >= 0; i--) band += `L${x(i)} ${y(pts[i].lo ?? pts[i].mid)}`;
    root.append(svg('path', { class: 'trend__band', d: band + 'Z' }));
  } else if (opts.area) {
    root.append(svg('path', {
      class: 'trend__area',
      d: `${line}L${x(pts.length - 1)} ${H}L${x(0)} ${H}Z`,
    }));
  }

  root.append(svg('path', {
    class: 'trend__line' + (opts.cyan ? ' trend__line--cyan' : ''),
    d: line,
  }));

  return h('div', null, root, h('div', {
    style: { display: 'flex', justifyContent: 'space-between', marginTop: '6px' },
  },
    h('span.stat__label', null, `${pts[0].date || ''} · ${opts.fmt(lo)}`),
    h('span.stat__label', null, `${pts[pts.length - 1].date || ''} · ${opts.fmt(hi)}`)));
}

function velocity(rows) {
  const series = (rows || []).map((r) => ({
    date: r.date, mid: r.mean, lo: r.min, hi: r.max,
  }));
  return trend(series, {
    band: true, label: 'velocity', empty: 'play on two different days to see this',
    fmt: (v) => `vel ${Math.round(v)}`,
  });
}

function speed(rows) {
  const series = (rows || []).map((r) => ({ date: r.date, mid: r.npm }));
  return trend(series, {
    area: true, cyan: true, label: 'notes per minute',
    empty: 'play on two different days to see this',
    fmt: (v) => `${Math.round(v)} npm`,
  });
}

/* ── sessions ─────────────────────────────────────────────────────────────── */
function sessionLengths(rows) {
  if (!rows || !rows.length) return nothing('no sessions yet');
  const sorted = [...rows].sort((a, b) => (a.minutes || 0) - (b.minutes || 0));
  return barChart(
    sorted.map((r) => r.count || 0),
    sorted.map((r) => `${r.minutes}m`),
    { title: (i, v) => `${sorted[i].minutes} min -- ${count(v)} sessions` });
}

function presets(rows) {
  if (!rows || !rows.length) return nothing('no sessions yet');
  const max = Math.max(...rows.map((r) => r.seconds || 0), 1);
  return h('div.list', null, rows.slice(0, 8).map((r) => h('div.list__row', null,
    h('span', { style: { minWidth: '92px' } }, r.preset || '--'),
    h('div.bar', { style: { flex: '1' } },
      h('div.bar__fill', { style: { width: pct((r.seconds || 0) / max) } })),
    h('span.tag.tag--amber', null, humanMinutes(r.seconds)))));
}

function sightread(s) {
  if (!s || !s.attempts) return nothing('no attempts yet');
  return h('div.stats', null,
    stat(pct(s.accuracy), 'Accuracy', `${count(s.correct)}/${count(s.attempts)}`,
         'stat__value--amber'),
    stat(s.mean_reaction_ms ? `${Math.round(s.mean_reaction_ms)}ms` : '--',
         'Reaction', 'to find the key', 'stat__value--cyan'));
}
