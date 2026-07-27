/* Keys -- app shell.
 *
 * Owns three things and delegates everything else:
 *   1. the websocket (realtime frames in, nothing out -- control is REST),
 *   2. the keyboard in the dock and the readout strip above it, which are global and
 *      stay live no matter which view is showing,
 *   3. a hash router that mounts one view module at a time.
 *
 * Frames arrive about 60 times a second. Nothing in the frame path allocates a
 * subtree or re-renders a view; it toggles classes and sets text on nodes that
 * already exist. Views that want frames declare frame(); most only want status().
 */

import { createKeyboard } from './keyboard.js';
import { attachLayout, primeLayout } from './layout.js';
import { $, api, hms, toast } from './ui.js';
import { startTour, tourOpen } from './tour.js';

import playView from './views/play.js';
import practiceView from './views/practice.js';
import layersView from './views/layers.js';
import toolsView from './views/tools.js';
import statsView from './views/stats.js';
import settingsView from './views/settings.js';
import readView from './views/read.js';

const VIEWS = {
  play: playView,
  practice: practiceView,
  layers: layersView,
  tools: toolsView,
  stats: statsView,
  settings: settingsView,
  // Routable but not in the nav: sight reading is on its way into Practice as one
  // exercise among several. Keeping it reachable means it is never orphaned mid-move.
  read: readView,
};

/* Old hashes, so a bookmark or a stale tab lands somewhere sensible instead of
   silently falling through to Play with no nav item lit. Deletable once nobody has
   an old URL. */
const ALIASES = { zones: 'layers', metronome: 'tools', analytics: 'stats' };

/* ── shared context handed to every view ──────────────────────────────────── */
export const ctx = {
  state: {},          // last full /api/state
  status: null,       // last 1 Hz status frame
  kb: null,           // keyboard controller
  api,
  toast,
  refresh,            // re-pull /api/state and re-render the current view
  keySignature: () => ctx.state?.settings?.ui?.key_signature || 'C',
  // Live touch tracking. The P-71B ships with Touch Sensitivity on Fixed, which sends
  // velocity 64 for every note however hard you hit it, and the only way to know
  // whether the fix took is to watch the spread as you play.
  touch: { last: 0, min: 128, max: 0, count: 0, seen: new Set(), hist: new Array(16).fill(0) },
};

export function resetTouch() {
  ctx.touch.min = 128;
  ctx.touch.max = 0;
  ctx.touch.count = 0;
  ctx.touch.last = 0;
  ctx.touch.seen.clear();
  ctx.touch.hist.fill(0);
}

let current = null;
let currentId = null;
let lastZoneSig = null;

/* ── keyboard + readout (global, always live) ─────────────────────────────── */
const dock = $('#dock');
const els = {
  chord: $('#chord-symbol'),
  chordName: $('#chord-name'),
  notes: $('#note-list'),
  vel: $('#last-vel'),
  pedal: $('#pedal'),
  todayClock: $('#today-clock'),
  playingDot: $('#playing-dot'),
};

ctx.kb = createKeyboard(dock, {
  labels: 'c-only',
  onKeyDown: (midi, velocity) => {
    // Clicking a key auditions it through the real engine, so what you hear is the
    // preset you actually have loaded, not a synthetic preview voice.
    api.post('/api/preview', { notes: [midi], velocity, ms: 900 }).catch(() => {});
  },
});

function applyFrame(f) {
  if (f.on) for (const [n, v] of f.on) ctx.kb.noteOn(n, v);
  if (f.off) for (const n of f.off) ctx.kb.noteOff(n);
  if (f.held) ctx.kb.setHeld(f.held);
  if (typeof f.sus === 'boolean') {
    ctx.kb.setSustain(f.sus);
    els.pedal.classList.toggle('is-on', f.sus);
  }
  if (f.on && f.on.length) {
    const t = ctx.touch;
    for (const [, v] of f.on) {
      t.last = v;
      if (v < t.min) t.min = v;
      if (v > t.max) t.max = v;
      t.seen.add(v);
      t.hist[Math.min(15, (v - 1) >> 3)]++;
      t.count++;
    }
    els.vel.textContent = t.count > 1 && t.max > t.min
      ? `${t.last} (${t.min}-${t.max})`
      : String(t.last);
    els.vel.classList.toggle('is-fixed', t.count >= 8 && t.seen.size === 1);
  }
  if ('chord' in f) {
    const c = f.chord;
    els.chord.textContent = c ? c.symbol : '—';
    els.chord.classList.toggle('is-empty', !c);
    els.chordName.textContent = c ? c.name : '';
  }
  if (f.names) els.notes.textContent = f.names.join('   ');
  current?.frame?.(f, ctx);
}

function applyStatus(s) {
  ctx.status = s;
  ctx.kb.setHeld(s.held || []);
  lamp('lamp-midi', s.midi?.connected ? 'on' : 'bad',
       s.midi?.connected ? (s.midi.port_name || 'ok').slice(0, 14) : 'none');
  // buffer_ms is null in shared mode on purpose -- Windows owns the period there, so
  // quoting a number we do not control would be a lie.
  const buf = s.engine?.buffer_ms;
  lamp('lamp-audio', s.engine?.started ? 'on' : 'bad',
       !s.engine?.started ? 'off' : buf == null ? 'shared' : `${buf}ms`);
  lamp('lamp-voices', (s.engine?.voices || 0) > 0 ? 'warn' : '', String(s.engine?.voices ?? 0));

  // Keys no enabled zone covers do nothing when pressed. Marking them is the whole
  // answer to "where can I actually play" -- for a split, and for any zone set you
  // build yourself that leaves a hole. Recomputed only when the zones change.
  const zoneSig = JSON.stringify((s.engine?.zones || [])
    .filter((z) => z.enabled).map((z) => [z.lo, z.hi]));
  if (zoneSig !== lastZoneSig) {
    lastZoneSig = zoneSig;
    const live = new Set();
    for (const z of s.engine?.zones || []) {
      if (!z.enabled) continue;
      for (let n = z.lo; n <= z.hi; n++) live.add(n);
    }
    const dead = [];
    for (let n = 21; n <= 108; n++) if (!live.has(n)) dead.push(n);
    ctx.kb.setDead(dead);
  }

  const p = s.practice || {};
  els.todayClock.textContent = hms(p.today_seconds || 0);
  els.playingDot.classList.toggle('is-live', p.session_active && !p.idle);
  current?.status?.(s, ctx);
}

function lamp(id, cls, value) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.remove('is-on', 'is-warn', 'is-bad');
  if (cls) el.classList.add('is-' + cls);
  const v = document.getElementById(id + '-value');
  if (v) v.textContent = value;
}

/* ── websocket ────────────────────────────────────────────────────────────── */
let ws = null;
let retry = 0;
let keepalive = null;

function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);

  ws.onopen = () => {
    retry = 0;
    lamp('lamp-link', 'on', 'live');
    // The server only reads to notice we are gone; this is the heartbeat that
    // makes a dropped connection visible instead of silently frozen.
    keepalive = setInterval(() => ws.readyState === 1 && ws.send('.'), 5000);
  };

  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.t === 'f') applyFrame(msg);
    else if (msg.t === 's' || msg.t === 'hello') applyStatus(msg);
    else if (msg.t === 'err') toast(`${msg.where}: ${msg.message}`, 'bad');
  };

  ws.onclose = () => {
    clearInterval(keepalive);
    lamp('lamp-link', 'bad', 'lost');
    retry = Math.min(retry + 1, 12);
    setTimeout(connect, Math.min(250 * retry, 3000));
  };

  ws.onerror = () => ws.close();
}

/* ── router ───────────────────────────────────────────────────────────────── */
const stage = $('#stage');

async function route() {
  const id = (location.hash.replace('#', '') || 'play');
  if (ALIASES[id]) { location.hash = ALIASES[id]; return; }   // re-enters via hashchange
  const view = VIEWS[id] || VIEWS.play;
  if (currentId === id) return;

  current?.unmount?.();
  ctx.kb.setHighlight([]);
  ctx.kb.setGhost([]);
  ctx.kb.clearLabels();
  stage.replaceChildren();

  current = view;
  currentId = id;
  document.documentElement.dataset.view = id;
  for (const a of document.querySelectorAll('.nav__item')) {
    a.classList.toggle('is-active', a.dataset.view === id);
  }
  await view.mount?.(stage, ctx);
  // After mount, so it operates on the DOM the view actually built. No view knows
  // this exists; a new panel is rearrangeable without being registered anywhere.
  for (const grid of stage.querySelectorAll('.grid')) attachLayout(grid, id);
  if (ctx.status) view.status?.(ctx.status, ctx);
}

async function refresh() {
  ctx.state = await api.get('/api/state');
  currentId = null;          // force a rebuild against the new state
  await route();
}

/* ── keyboard shortcuts ───────────────────────────────────────────────────── */
// The nav order, and the only definition of it -- the number hotkeys are derived from
// this array's length below, so adding or removing a tab is a one-line change here plus
// the matching anchor in index.html.
const ORDER = ['play', 'practice', 'layers', 'tools', 'stats', 'settings'];

document.addEventListener('keydown', (e) => {
  // The tour owns the keyboard while it is up. Without this, Esc fires panic and
  // the number keys navigate the tab out from behind the card.
  if (tourOpen()) return;
  const typing = /^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement?.tagName || '');
  if (e.key === 'Escape') {
    api.post('/api/panic').then(() => toast('All notes off', 'good'));
    return;
  }
  if (typing || e.ctrlKey || e.altKey || e.metaKey) return;
  const n = Number(e.key);
  if (n >= 1 && n <= ORDER.length) {
    location.hash = ORDER[n - 1];
  } else if (e.key.toLowerCase() === 'm') {
    api.post('/api/metronome/toggle').catch(() => {});
  }
});

$('#panic').addEventListener('click', () => {
  api.post('/api/panic').then(() => toast('All notes off', 'good'));
});

window.addEventListener('hashchange', route);
window.addEventListener('beforeunload', () => { if (ws) { ws.onclose = null; ws.close(); } });

/* ── go ───────────────────────────────────────────────────────────────────── */
(async function boot() {
  try {
    ctx.state = await api.get('/api/state');
  } catch (err) {
    toast('Could not reach the app: ' + err.message, 'bad', 10000);
    ctx.state = {};
  }
  primeLayout(ctx.state);
  for (const problem of ctx.state.errors || []) toast(problem, 'bad', 12000);
  await route();
  connect();
  if (ctx.state?.settings?.ui && !ctx.state.settings.ui.tour_seen) startTour(ctx);
})();
