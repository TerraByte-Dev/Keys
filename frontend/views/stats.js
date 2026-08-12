/* Stats -- the long view, and the only home for analytics.
 *
 * Practice is for doing; every number that describes your playing over time lives
 * here. This answers "what have I actually been playing", over a year: which keys,
 * which chords, which hours of the day, and whether the dynamics are opening up.
 *
 * **It keeps up with you while you read it.** Sitting on this page and playing used
 * to show a frozen snapshot until you navigated away and back. Now the page refreshes
 * itself -- but only when notes have actually been played, never on a blind timer.
 * `practice.today_notes` on the 1 Hz status frame is the change signal, so an idle
 * Stats page costs exactly nothing and a busy one costs one 18 ms query every few
 * seconds.
 *
 * Two things a rebuild would otherwise ruin, and how they are handled: the year
 * calendar's horizontal scroll is saved and restored across the swap, and a refresh
 * is deferred while the pointer is inside the grid, because every chart here reads
 * through a native `title` tooltip and rebuilding the node under the cursor makes it
 * vanish mid-read.
 *
 * Every panel draws its own empty state on purpose. A log with nothing in it should
 * look like an instrument that has not been played yet, not like a broken page --
 * the blank year grid and the cold 88-key map ARE the reading. */

import { instrument } from '../app.js';
import { $, api, h, hms, humanMinutes, mod, noteName, stat } from '../ui.js';

let data = null;

/* Live-refresh state. */
const REFRESH_MS = 5000;      // floor between queries while you are playing
let lastNotes = null;         // practice.today_notes at the last refresh
let lastFetch = 0;
let inFlight = false;
let hovering = false;         // pointer inside the grid -- a tooltip may be open
let stale = false;            // notes arrived while hovering; refresh on the way out
let live = false;

/* Which year the Activity chart is showing, and which years there is anything to show.
   Module-scoped so paging back survives the refresh that a note triggers. */
let calYear = 0;
let calYears = [];
let calData = null;

export default {
  async mount(root) {
    const grid = h('div.grid', { id: 'an-grid' },
      h('div.col-12', null, h('div.empty', null, 'reading a year of practice...')));
    // Defer a rebuild while the pointer is in here. Every chart is read through a
    // native title tooltip, and replacing the node under the cursor closes it.
    grid.addEventListener('mouseenter', () => { hovering = true; });
    grid.addEventListener('mouseleave', () => {
      hovering = false;
      if (stale) refresh();
    });
    root.append(grid);

    lastNotes = null;
    lastFetch = 0;
    inFlight = false;
    hovering = false;
    stale = false;
    live = false;

    try {
      // 53 weeks, not 365 days: the calendar below draws WEEKS * 7 cells back to a
      // Monday, so a 365-day window leaves its first column with no rows behind it
      // and those days render as "nothing" whether or not they were played.
      data = await api.get(`/api/analytics?days=${WEEKS * 7}`);
    } catch (err) {
      $('#an-grid').replaceChildren(h('div.col-12', null,
        h('div.empty', null, 'could not load analytics: ' + err.message)));
      return;
    }
    lastFetch = performance.now();
    render(data);
    await loadCalendar(calYear);
  },

  /* Two jobs, once a second. Today's line is mutated in place because it changes
     every second and re-querying for it would be absurd; everything else is driven
     off the note counter, which is the only honest signal that any of these numbers
     could have moved. */
  status(s) {
    const p = s.practice;
    if (!p) return;
    live = !!p.session_active && !p.idle;

    const el = $('#an-today');
    if (el) {
      el.textContent = (p.today_seconds
        ? `Today so far: ${hms(p.today_seconds)} over ${p.today_sessions || 1} session(s).`
        : 'Nothing logged today yet.')
        + (live ? '  Updating as you play.' : '');
    }

    const notes = p.today_notes;
    if (notes == null) return;
    if (lastNotes === null) { lastNotes = notes; return; }
    // Not a timer. No notes, no query -- a Stats page left open overnight is free.
    if (notes === lastNotes) return;
    if (hovering) { stale = true; return; }
    if (performance.now() - lastFetch < REFRESH_MS) return;
    // Claimed here, not in refresh(): the counter we are acting on is this frame's,
    // and /api/analytics has no equivalent field to read it back from.
    lastNotes = notes;
    refresh();
  },

  unmount() {
    data = null;
    calData = null;
    calYear = 0;
    lastNotes = null;
    inFlight = false;
    stale = false;
  },
};

/* The calendar is fetched separately from everything else on this page. Paging back a
   year must not change the chord counts or the key inference -- those answer over a
   rolling window and have nothing to do with which year you are looking at. */
async function loadCalendar(year) {
  try {
    const res = await api.get(`/api/calendar?year=${year || 0}`);
    calYear = res.year;
    calYears = res.years || [calYear];
    calData = res;
    paintCalendar();
  } catch {
    const host = $('#cal-host');
    if (host) host.replaceChildren(h('div.empty', null, 'could not load the calendar'));
  }
}

function paintCalendar() {
  const host = $('#cal-host');
  if (!host || !calData) return;
  host.replaceChildren(calendar(calData));
  const label = $('#cal-year');
  if (label) label.textContent = String(calYear);
  const aside = $('#cal-aside');
  if (aside) {
    aside.textContent = calData.days_played
      ? `${calData.days_played} days, ${humanMinutes(calData.active_seconds)}`
      : 'nothing yet this year';
  }
  const lo = Math.min(...calYears, calYear);
  const hi = Math.max(...calYears, calYear);
  const prev = $('#cal-prev');
  const next = $('#cal-next');
  if (prev) prev.disabled = calYear <= lo;
  if (next) next.disabled = calYear >= hi;
}

function stepYear(delta) {
  const want = calYear + delta;
  const lo = Math.min(...calYears, calYear);
  const hi = Math.max(...calYears, calYear);
  if (want < lo || want > hi) return;
  loadCalendar(want);
}

async function refresh() {
  const grid = $('#an-grid');
  if (inFlight || !grid) return;
  // Nothing in Stats takes focus today, but a range picker is the obvious next
  // addition here and replacing a focused control moves focus to <body> -- which also
  // un-gates the number hotkeys in app.js and starts navigating tabs out from under
  // whatever you were typing. Cheap insurance, one line.
  if (grid.contains(document.activeElement)) { stale = true; return; }
  inFlight = true;
  lastFetch = performance.now();
  stale = false;
  try {
    const fresh = await api.get(`/api/analytics?days=${WEEKS * 7}`);
    if (!$('#an-grid')) return;      // navigated away mid-flight
    data = fresh;
    render(fresh);
    await loadCalendar(calYear);
  } catch {
    // A failed refresh leaves the last good numbers on screen, which is strictly
    // better than replacing a year of history with an error because one poll lost.
  } finally {
    inFlight = false;
  }
}

/* ── layout ───────────────────────────────────────────────────────────────── */
function render(d) {
  // Two scroll positions have to survive the swap, and the first one is the whole
  // difference between a live page and an unusable one.
  //
  // #stage is the page's vertical scroller (.stage { overflow-y: auto }) and Stats is
  // sixteen panels tall. replaceChildren() empties #an-grid before it refills it, so
  // scrollHeight collapses mid-call and the browser clamps scrollTop to 0 -- anyone
  // reading the velocity chart at the bottom gets thrown back to "All time" on every
  // refresh. Captured and restored in the same task, before paint.
  //
  // The second is the year calendar's own horizontal scroll: 53 columns of 11px is
  // wider than the panel, and a fresh node starts at scrollLeft 0, which is 52 weeks
  // ago. Losing it makes this month unreachable.
  const stage = $('#stage');
  const stageTop = stage ? stage.scrollTop : 0;

  const streak = d.streak || {};
  const totals = d.totals || {};
  const seconds = totals.active_seconds ?? streak.total_active_seconds ?? 0;
  const days = totals.days_practiced ?? streak.total_days ?? 0;

  $('#an-grid').replaceChildren(
    h('div.col-6', null, mod('All time', d.range_days ? `last ${d.range_days} days` : null,
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

    h('div.col-6', null, mod('Activity', h('span', { id: 'cal-aside' }, ''),
      h('div.calx__nav', null,
        h('button.btn', { id: 'cal-prev', onclick: () => stepYear(-1) }, '‹'),
        h('span.calx__year', { id: 'cal-year' }, String(calYear || new Date().getFullYear())),
        h('button.btn', { id: 'cal-next', onclick: () => stepYear(1) }, '›')),
      h('div.calx', { id: 'cal-host' }))),

    h('div.col-12', null, mod('Keys you have played', 'every note, all time',
      pianoMap(d.note_heatmap, d.range),
      rangeLine(d.range))),

    h('div.col-6', null, mod('What key you play in', null,
      keyList(d.keys))),

    h('div.col-6', null, mod('Note circle', 'every note, ignoring octave',
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

    h('div.col-6', null, mod('Session length', null,
      sessionLengths(d.session_lengths))),

    h('div.col-3', null, mod('Sounds you reach for', null,
      presets(d.presets))),

    h('div.col-3', null, mod('Sight reading', 'all attempts',
      sightread(d.sightread))),
  );

  if (stage && stageTop) stage.scrollTop = stageTop;
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
/* A calendar year, 53 columns of 7 days, Monday on top.
 *
 * Fixed to 1 January - 31 December rather than a rolling 53 weeks back from today,
 * because "how has this year gone" is the question the chart answers and a rolling
 * window's shape changes under you every morning. Days after today are drawn as voids:
 * the empty right-hand side IS the reading.
 *
 * Columns are fractions, not pixels. At 11px a column the grid was 739px wide, which
 * needed a horizontal scrollbar the moment the panel was anything less than full
 * width -- and the panel is a half now. Fractions mean the whole year fits whatever
 * width it is given, and making the panel wider is how you get bigger cells. */
function calendar(payload) {
  const rows = payload?.days || [];
  const byDate = new Map(rows.map((r) => [r.date, r]));
  const year = payload?.year || new Date().getFullYear();

  // Grid position of 1 January: Monday is row 0, and Jan 1 rarely lands on it.
  const jan1 = new Date(year, 0, 1);
  const lead = (jan1.getDay() + 6) % 7;

  const months = [];
  const cells = [];
  // Blanks so the first column starts on the right weekday.
  for (let i = 0; i < lead; i++) cells.push(h('div.cal__day.cal__day--void'));

  let lastMonth = -1;
  for (const row of rows) {
    const d = new Date(row.date + 'T12:00:00');
    if (d.getMonth() !== lastMonth) {
      lastMonth = d.getMonth();
      const col = Math.floor((lead + rows.indexOf(row)) / 7) + 1;
      if (col < WEEKS) {
        months.push(h('div.calx__month', { style: { gridColumn: String(col) } },
          MONTHS[lastMonth]));
      }
    }
    if (row.future) {
      cells.push(h('div.cal__day.cal__day--void'));
      continue;
    }
    const secs = row.active_seconds || 0;
    cells.push(h('div.cal__day', {
      'data-l': secs === 0 ? 0 : secs < 300 ? 1 : secs < 900 ? 2 : secs < 1800 ? 3 : 4,
      title: secs
        ? `${row.date} -- ${humanMinutes(secs)}, ${count(row.note_count)} notes`
        : `${row.date} -- nothing`,
    }));
  }

  return h('div.calx__fit', null,
    h('div.calx__months', { style: { gridTemplateColumns: `repeat(${WEEKS}, 1fr)` } },
      months),
    h('div.calx__rows', null,
      h('div.calx__wd', null,
        ['Mon', '', 'Wed', '', 'Fri', '', ''].map((t) => h('span', null, t))),
      h('div.cal', { style: { gridTemplateColumns: `repeat(${WEEKS}, 1fr)` } }, cells)),
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
/* The keys this map draws: the UNION of the keyboard you have and the keys you have
   actually played. Not just the configured range, and the difference matters -- someone
   who played a P-71B for a year and then declares a 61-key controller would otherwise
   watch a year of A0s disappear out of their own history, silently, in a chart. The
   store no longer filters that history either; between them, what you played stays
   played. Snapped out to whole octaves so the picture keeps a piano's shape. */
function mapRange(range) {
  const inst = instrument();
  let lo = inst.low;
  let hi = inst.high;
  if (range && range.low != null) {
    lo = Math.min(lo, range.low);
    hi = Math.max(hi, range.high);
  }
  while (WHITE_INDEX[lo % 12] < 0 && lo > 0) lo -= 1;
  while (WHITE_INDEX[hi % 12] < 0 && hi < 127) hi += 1;
  return [lo, hi];
}

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
  const [LOW, HIGH] = mapRange(range);
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
    'aria-label': `how often each of the ${HIGH - LOW + 1} keys has been played`,
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
  // Measured against the keyboard you have, so the label has to say which one -- "Of
  // the 88" on a 61-key board is the app talking about an instrument you do not own.
  const keys = range.of_high != null ? range.of_high - range.of_low + 1 : 88;
  return h('div.stats', { style: { marginTop: '14px' } },
    stat(`${range.low_name || '--'}-${range.high_name || '--'}`, 'Range used',
         `${range.span} semitones`, 'stat__value--amber'),
    // Capped at 100%: coverage divides a played span that history may have widened by
    // the span you currently declare, so a smaller keyboard than you once had really
    // can exceed it. True, but "143%" reads as a bug.
    stat(pct(Math.min(1, range.coverage)), `Of your ${keys}`,
         range.coverage > 1 ? 'you have played wider than this keyboard'
                            : 'keys touched at least once',
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
    // Active seconds, not notes -- store.hour_histogram weights by the practice clock.
    title: (i, n) => `${String(i).padStart(2, '0')}:00 -- ${humanMinutes(n)}`,
  });
}

const DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

function weekdays(rows) {
  const v = Array.isArray(rows) && rows.length === 7 ? rows.map(Number) : null;
  if (!v || !v.some((n) => n > 0)) return nothing('no sessions yet');
  // Same unit as hours(): active seconds, so the bare bar height needs a unit on it.
  return barChart(v, DAY_NAMES, {
    cyan: true, title: (i, n) => `${DAY_NAMES[i]} -- ${humanMinutes(n)}`,
  });
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
