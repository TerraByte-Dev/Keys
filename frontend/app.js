/* Keys -- app shell.
 *
 * Owns three things and delegates everything else:
 *   1. the websocket (realtime frames in, nothing out -- control is REST),
 *   2. the keyboard in the dock and the readout strip above it, which are global and
 *      stay live no matter which view is showing,
 *   3. a hash router that mounts one view module at a time -- or, on a page you built
 *      yourself (pages.js), up to four of them side by side, each in its own pane.
 *
 * Frames arrive about 60 times a second. Nothing in the frame path allocates a
 * subtree or re-renders a view; it toggles classes and sets text on nodes that
 * already exist. Views that want frames declare frame(); most only want status().
 */

import { createKeyboard } from './keyboard.js';
import { createRoll } from './roll.js';
import { createGhost } from './ghost.js';
import { createSongs } from './songs.js';
import { attachLayout, detachLayout, primeLayout } from './layout.js';
import { MAX_PAGES, fullWidth, pageBuilder, pageShell, pickPanels, savePages,
  validPages } from './pages.js';
import { $, api, h, hms, noteName, paint as paintSlider, toast } from './ui.js';
import { startTour, tourOpen } from './tour.js';
import { keyLabel } from './prefs.js';
import { closeSettings, openSettings, settingsOpen } from './settings-overlay.js';
import { initTitlebar } from './titlebar.js';

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
const ALIASES = { zones: 'layers', metronome: 'play', analytics: 'stats' };

/* ── shared context handed to every view ──────────────────────────────────── */
export const ctx = {
  state: {},          // last full /api/state
  status: null,       // last 1 Hz status frame
  kb: null,           // keyboard controller
  api,
  toast,
  refresh,            // re-pull /api/state and re-render the current view
  // Focus: one practice panel and the keys, full screen. `el` is the panel asking;
  // without one the view's own focusTarget() is used. See toggleFocus.
  toggleFocus: (on, el) => toggleFocus(on, { target: el }),
  isFocusing: () => focusing,
  keySignature: () => ctx.state?.settings?.ui?.key_signature || 'C',
  // Live touch tracking. The P-71B ships with Touch Sensitivity on Fixed, which sends
  // velocity 64 for every note however hard you hit it, and the only way to know
  // whether the fix took is to watch the spread as you play.
  touch: { last: 0, min: 128, max: 0, count: 0, seen: new Set(), hist: new Array(16).fill(0) },
};

/* Every theme is a block of CSS variables in style.css; midnight is :root itself and
   therefore needs no attribute. Applied before the first paint and again the moment
   you pick one, so nothing has to reload. "dark" is what shipped before the picker
   existed and still means midnight. */
export const THEMES = ['midnight', 'blueprint', 'phosphor', 'paper',
  'ultraviolet', 'synthwave', 'crimson', 'tangerine', 'ice', 'gold', 'slate'];

export function applyTheme(name) {
  const t = THEMES.includes(name) ? name : 'midnight';
  if (t === 'midnight') delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = t;
  return t;
}

export function resetTouch() {
  ctx.touch.min = 128;
  ctx.touch.max = 0;
  ctx.touch.count = 0;
  ctx.touch.last = 0;
  ctx.touch.seen.clear();
  ctx.touch.hist.fill(0);
}

/* What is up: one entry for a tab, one per pane on a page. Each has its own ctx whose
   refresh() remounts only that view, so loading a preset in a page's Play pane does
   not tear down the Jam beside it (and stop the band with it). */
let mounted = [];       // [{ id, view, root, ctx, layoutKey, panels }]
let currentId = null;   // the route: a view id, or 'page/<id>' / 'page/<id>/edit'
let routeSeq = 0;
let pages = [];         // ui.pages, validated
let lastRoute = 'play'; // where Cancel on a new page goes back to
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

/* ── the instrument you actually own ──────────────────────────────────────── */
/* One copy of "which keys exist and where they are sounding", kept here because the
   dock keyboard is here and everything else that needs it is a view that comes and
   goes. Views read it through instrument(); nothing else keeps its own answer.

   Seeded with the 88 of a piano so the first paint has something to draw, then
   corrected from /api/state before the socket is even open, and re-checked on every
   status heartbeat -- so a range changed in another tab lands here within a second. */
let inst = { low: 21, high: 108, keys: 88, octave: 0, min_keys: 12, max_octave: 4 };

export const instrument = () => inst;

function applyRange(r) {
  if (!r) return;
  const redraw = r.low !== inst.low || r.high !== inst.high;
  const shifted = r.octave !== inst.octave;
  if (!redraw && !shifted) return;
  inst = { ...inst, ...r };

  if (redraw) {
    ctx.kb.setRange(inst.low, inst.high);
    // The roll takes its columns off the dock's actual keys, so it is measuring a
    // keyboard that no longer exists until it looks again.
    requestAnimationFrame(() => roll?.remeasure());
  }
  // Either one changes which keys do nothing when pressed: a narrower board has fewer
  // keys to judge, and a shift can push a key's output off the end of MIDI.
  lastZoneSig = null;
  paintOctave();
}

function paintOctave() {
  const out = $('#oct-value');
  if (out) out.textContent = inst.octave > 0 ? `+${inst.octave}` : String(inst.octave);
  $('#oct')?.classList.toggle('is-shifted', inst.octave !== 0);
  const down = $('#oct-down');
  const up = $('#oct-up');
  if (down) down.disabled = inst.octave <= -inst.max_octave;
  if (up) up.disabled = inst.octave >= inst.max_octave;
  // The roll bar says what the shift means for a piece you have open; repaint it too,
  // or it goes stale the moment you press OCT mid-play-along.
  if (ghostModel) paintGhost();
}

/* Nudge the whole instrument by whole octaves. The engine bakes the shift into its
   routing table, so a key already down is still released at the pitch it was struck
   at -- pressing this mid-chord is safe, and deliberately so. */
export async function shiftOctave(by) {
  try {
    const res = await api.post('/api/octave', { by });
    applyRange(res.range);
    toast(res.octave ? `Octave ${res.octave > 0 ? '+' : ''}${res.octave}` : 'Octave normal',
          'good', 1400);
  } catch (err) { toast(err.message, 'bad'); }
}

/* Anyone who needs to see the raw keys as they are struck, wherever they are mounted.
   The router hands frames to the views that are up (`m.view.frame`), which is right
   for a panel and wrong for anything that outlives a view -- the tutorial card and the
   gear overlay both float above whatever is routed. One Set, called from applyFrame,
   rather than a second frame channel per floating thing. */
const noteListeners = new Set();

export function listenNotes(fn) {
  noteListeners.add(fn);
  return () => noteListeners.delete(fn);
}

/* Things that make sound the BACKEND cannot reach. Today that is the backing-track
   video, which is a YouTube iframe running in someone else's process -- /api/panic can
   silence every synth voice in the building and that video plays merrily on.
   Panic is supposed to mean silence, so it has to be able to ask.
   A silencer returns true if it actually stopped something. */
const silencers = new Set();

export function onPanic(fn) {
  silencers.add(fn);
  return () => silencers.delete(fn);
}

/* The one door. Both the button and Esc come here, so they cannot drift apart. */
export async function panic() {
  const quieted = [];
  for (const fn of silencers) {
    // One broken silencer must not stop the rest of the room going quiet.
    try { if (fn()) quieted.push('video'); } catch (err) { /* not panic's problem */ }
  }
  try {
    const res = await api.post('/api/panic');
    const all = [...(res.stopped || []), ...quieted];
    // Naming what stopped is the point: when you cannot tell where a sound is coming
    // from, "stopped metronome, loop" answers the question, and "nothing was playing"
    // tells you to look outside Keys -- which is just as useful and used to be silence.
    toast(all.length ? `Stopped ${all.join(', ')}` : 'Nothing was playing', 'good');
  } catch (err) {
    toast(err.message, 'bad');
  }
}

/* A key you pressed that the drawn keyboard has no room for.
 *
 * It SOUNDS -- the engine routes every note regardless of what you declared -- but the
 * widget has no element for it, so nothing lights, the roll has no column, and the
 * whole app reads as "my keyboard stopped working". That is the worst way to be wrong:
 * silently, about the thing you just did.
 *
 * It almost always means the size chip you picked does not match what your controller
 * actually sends. "61 keys" is not one range: plenty of them sit somewhere other than
 * C2-C7, and many have their own octave buttons that move where they start. Pressing
 * your own two end keys is the only answer that cannot be wrong, so the notice says so.
 *
 * Throttled hard, and never while the Detect flow is armed -- there, pressing a key
 * outside the current range is exactly what you were asked to do. */
let strayNoticeAt = 0;

function noticeOutOfRange(on) {
  if (detectArmed) return;
  const now = performance.now();
  if (now - strayNoticeAt < 25000) return;
  let stray = null;
  for (const [n] of on) {
    if (n < inst.low || n > inst.high) { stray = n; break; }
  }
  if (stray === null) return;
  strayNoticeAt = now;
  toast(`${noteName(stray)} is outside the ${inst.keys} keys you told Keys you have, `
      + 'so it sounds but does not light up. Sound → Your keyboard → '
      + '"Press my keys instead" sets it from your actual keyboard.', 'warn', 11000);
}

/* Set while the keyboard picker is listening for your two end keys. */
let detectArmed = false;
export function setDetecting(on) { detectArmed = !!on; }

function applyFrame(f) {
  if (f.on) noticeOutOfRange(f.on);
  if (f.on && noteListeners.size) {
    for (const fn of noteListeners) {
      // One bad listener must not stop the keyboard painting.
      try { fn(f.on); } catch (err) { /* not the frame path's problem */ }
    }
  }
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
  roll?.frame(f);
  ghostModel?.frame(f);
  for (let i = 0; i < mounted.length; i++) {
    const m = mounted[i];
    m.view.frame?.(f, m.ctx);
  }
}

/* The dot on the gear. Painted from the heartbeat rather than once at boot, because
   the launch check runs on a thread and lands a second or two after the page does --
   the badge has to be able to arrive late. */
function paintUpdateBadge(u) {
  const want = !!u?.available;
  document.body.classList.toggle('has-update', want);
  const gear = $('#gear');
  if (gear) {
    gear.title = want
      ? `Settings — ${u.latest} is available`
      : 'Settings — themes, shortcuts, updates';
  }
  // The overlay is rebuilt on open, so its own dot is set there; this only has to
  // reach it while it happens to be open.
  const nav = document.querySelector('.prefs__item[data-id="about"]');
  nav?.classList.toggle('has-update', want);
}

function applyStatus(s) {
  ctx.status = s;
  paintUpdateBadge(s.update);
  // Before the held set: setRange rebuilds the SVG and drops every layer with it, so
  // pushing held keys into the old drawing first would paint them and throw them away.
  applyRange(s.range);
  ctx.kb.setHeld(s.held || []);
  // The engine's own held set, once a second. In wait mode this is what un-sticks a
  // gate the frame path got wrong -- and a stuck gate looks exactly like a hang.
  if (ghostModel) {
    ghostModel.resync(s.held || []);
    // The scrub's fill rides the heartbeat; the bar readout and the sheet also
    // repaint at each bar line, because the model announces those. The roll is
    // the real-time display; neither of these is.
    paintGhost();
  }
  lamp('lamp-midi', s.midi?.connected ? 'on' : 'bad',
       s.midi?.connected ? (s.midi.pinned || 'all inputs').slice(0, 14) : 'none');
  // buffer_ms is null in shared mode on purpose -- Windows owns the period there, so
  // quoting a number we do not control would be a lie.
  const buf = s.engine?.buffer_ms;
  lamp('lamp-audio', s.engine?.started ? 'on' : 'bad',
       !s.engine?.started ? 'off' : buf == null ? 'shared' : `${buf}ms`);
  lamp('lamp-voices', (s.engine?.voices || 0) > 0 ? 'warn' : '', String(s.engine?.voices ?? 0));

  // Keys that do nothing when pressed. Marking them is the whole answer to "where can
  // I actually play" -- for a split, for any zone set you build yourself that leaves a
  // hole, and now for an octave shift that has pushed the top of the board off the end
  // of MIDI. Recomputed only when one of its inputs changes.
  const zoneSig = JSON.stringify([inst.low, inst.high, inst.octave,
    (s.engine?.zones || []).filter((z) => z.enabled).map((z) => [z.lo, z.hi, z.transpose])]);
  if (zoneSig !== lastZoneSig) {
    lastZoneSig = zoneSig;
    // The roll colours a bar by the zone that owns the note, so a split built
    // while it is open has to reach it too.
    roll?.setZones(s.engine?.zones || []);
    // The same test the engine's routing table makes, and it has to stay the same one:
    // a key is live if a zone covers it AND what that zone would play is a real pitch.
    const shift = 12 * (inst.octave || 0);
    const live = new Set();
    for (const z of s.engine?.zones || []) {
      if (!z.enabled) continue;
      for (let n = z.lo; n <= z.hi; n++) {
        const out = n + shift + (z.transpose || 0);
        if (out >= 0 && out <= 127) live.add(n);
      }
    }
    const dead = [];
    for (let n = inst.low; n <= inst.high; n++) if (!live.has(n)) dead.push(n);
    ctx.kb.setDead(dead);
  }

  const p = s.practice || {};
  els.todayClock.textContent = hms(p.today_seconds || 0);
  els.playingDot.classList.toggle('is-live', p.session_active && !p.idle);
  for (const m of mounted) m.view.status?.(s, m.ctx);
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
  if (currentId === id) return;

  // A page, its builder, or a view. An unknown page goes to Play rather than a blank.
  const pm = /^page\/([a-z0-9-]+)(\/edit)?$/.exec(id);
  const isNew = pm?.[1] === 'new';
  const page = pm && !isNew ? pages.find((p) => p.id === pm[1]) : null;
  if (pm && !page && (!isNew || pages.length >= MAX_PAGES)) { location.hash = 'play'; return; }

  // Before unmount: the view is about to empty the stage its marked panel lives in.
  toggleFocus(false);
  unmountAll();
  ctx.kb.clearHighlights();
  ctx.kb.setGhost([]);
  ctx.kb.clearAllLabels();
  stage.replaceChildren();
  const seq = ++routeSeq;
  currentId = id;
  if (!pm || (page && !pm[2])) lastRoute = id;
  document.documentElement.dataset.view = pm ? 'page' : id;
  paintNav(id);

  if (isNew || pm?.[2]) { openBuilder(page); return; }
  if (page) { await mountPage(page, seq); return; }
  const known = VIEWS[id] ? id : 'play';
  await mountView(known, VIEWS[known], stage, id, seq);
}

/* `layoutKey` is what layout.js saves the arrangement under: the view id on its own
   tab, `page:<id>:<view>` in a page pane, so a page never rearranges the real tab. */
async function mountView(id, view, root, layoutKey, seq, panels = null) {
  const m = { id, view, root, layoutKey, panels, ctx: viewCtx(id, panels) };
  mounted.push(m);
  await view.mount?.(root, m.ctx);
  // Routed away (or remounted) while mount() was awaiting: the pane is not ours now.
  if (seq !== routeSeq || !mounted.includes(m)) return;
  if (panels) fullWidth(root);
  // After mount, so it operates on the DOM the view actually built. No view knows
  // this exists; a new panel is rearrangeable without being registered anywhere.
  for (const grid of root.querySelectorAll('.grid')) attachLayout(grid, layoutKey);
  if (panels) pickPanels(root, panels);
  if (ctx.status) view.status?.(ctx.status, m.ctx);
}

/* Each pane's view mounts in pane order, exactly as it would on its own tab, into the
   pane instead of the stage. A route away mid-way stops at the next pane. */
async function mountPage(page, seq) {
  const shell = pageShell(page, { onEdit: () => { location.hash = `page/${page.id}/edit`; } });
  stage.append(shell.el);
  for (const pane of shell.panes) {
    if (seq !== routeSeq) return;
    await mountView(pane.view, VIEWS[pane.view], pane.el, `page:${page.id}:${pane.view}`, seq,
                    pane.panels);
  }
}

function unmountAll() {
  const was = mounted;
  mounted = [];
  for (const m of was) unmountOne(m);
}

/* The view's own teardown, then layout.js's: its observers and window listener would
   otherwise keep the thrown-away grids (and the views around them) alive. */
function unmountOne(m) {
  m.view.unmount?.(m.ctx);
  for (const grid of m.root.querySelectorAll('.grid')) detachLayout(grid);
}

/* `panels` is what the page picked from this view (null on its own tab), so a view can
   leave alone what is not on screen -- Practice does not resume a run into a hidden shelf. */
function viewCtx(id, panels = null) {
  return Object.create(ctx, {
    refresh: { value: () => refresh(id) },
    panels: { value: panels },
  });
}

/* Re-pull /api/state and rebuild. From a view on a page, only that view's pane. */
async function refresh(viewId = null) {
  ctx.state = await api.get('/api/state');
  // The pages came with it. Held only in memory between saves, they could otherwise
  // drift from the file (a settings reset) and a later save would put back what went.
  pages = validPages(ctx.state.settings?.ui?.pages);
  paintNav();
  const onPage = pages.some((p) => currentId === `page/${p.id}`);
  const m = viewId && onPage ? mounted.find((x) => x.id === viewId) : null;
  if (!m) {
    currentId = null;          // force a rebuild against the new state
    await route();
    return;
  }
  if (focusing && m.root.querySelector('[data-focus="on"]')) toggleFocus(false);
  mounted = mounted.filter((x) => x !== m);
  unmountOne(m);
  m.root.replaceChildren();
  await mountView(m.id, m.view, m.root, m.layoutKey, routeSeq, m.panels);
}

/* ── custom pages ─────────────────────────────────────────────────────────── */
const navPages = $('#nav-pages');
const navNew = $('#nav-new-page');

function paintNav(id = currentId) {
  const pageId = /^page\/([a-z0-9-]+)/.exec(id || '')?.[1];
  navPages?.replaceChildren(...pages.map((p, i) => h('a.nav__item.nav__item--page', {
    href: `#page/${p.id}`, 'data-page': p.id, title: p.name,
  },
    h('span.nav__dot'),
    i < 3 && binds[`page:${i + 1}`] ? h('span.nav__key', null, keyLabel(binds[`page:${i + 1}`])) : null,
    h('span.nav__label', null, p.name))));
  for (const a of document.querySelectorAll('.nav__item')) {
    a.classList.toggle('is-active', pageId ? a.dataset.page === pageId : a.dataset.view === id);
  }
  navNew?.toggleAttribute('hidden', pages.length >= MAX_PAGES);
}

function openBuilder(page) {
  stage.append(pageBuilder(page, {
    count: pages.length,
    onCancel: () => { location.hash = page ? `page/${page.id}` : lastRoute; },
    // Each edits the list as it is on disk now, not as it was when the builder opened.
    onSave: async (next) => {
      const now = await pagesOnDisk();
      if (!now) return;
      const list = now.some((p) => p.id === next.id)
        ? now.map((p) => (p.id === next.id ? next : p)) : [...now, next];
      if (await storePages(list)) location.hash = `page/${next.id}`;
    },
    onDelete: async () => {
      const now = await pagesOnDisk();
      if (now && await storePages(now.filter((p) => p.id !== page.id))) location.hash = 'play';
    },
  }));
}

async function pagesOnDisk() {
  try {
    return validPages((await api.get('/api/settings'))?.ui?.pages);
  } catch (err) {
    toast(err.message, 'bad');
    return null;
  }
}

async function storePages(list) {
  try {
    const settings = await savePages(list);
    ctx.state.settings = settings;
    pages = validPages(settings?.ui?.pages);
    paintNav();
    return true;
  } catch (err) {
    toast(err.message, 'bad');
    return false;
  }
}

function goPage(n) {
  const p = pages[n - 1];
  if (p) location.hash = `page/${p.id}`;
  else toast(`No page ${n} yet. + New page, under the tabs, makes one.`, '', 2600);
}

/* ── keyboard shortcuts ───────────────────────────────────────────────────── */
// The nav order, and the only definition of it -- the number hotkeys are derived from
// this array's length below, so adding or removing a tab is a one-line change here plus
// the matching anchor in index.html.
const ORDER = ['play', 'practice', 'layers', 'tools', 'stats', 'settings'];

/* ── the note roll ────────────────────────────────────────────────────────── */
/* THE ROLL IS FULL SCREEN OR IT IS NOT OPEN. There used to be a 150px strip as well,
   and it was the worst of both: at 100 px/s it holds about a second and a half, which
   is too little to read a song coming and too little to read one you just played, and
   it cost the stage a third of its height to say so. One mode, and it is the good one. */
let roll = null;
let rollOpen = false;
let rollPx = 100;

export const rollSpeed = () => rollPx;

export function setRollSpeed(px) {
  rollPx = Math.max(40, Math.min(240, Number(px) || 100));
  roll?.setSpeed(rollPx);
  const live = $('#roll-speed-live');
  if (live) live.textContent = String(rollPx);
  const sl = $('#roll-speed');
  if (sl && document.activeElement !== sl) {
    sl.value = String(rollPx);
    paintSlider(sl);
  }
  if (ghostModel) paintGhost();          // the lookahead sentence just changed
  return rollPx;
}

/* Immersive: the roll fills the window, the keyboard stays, everything else goes.
   Real fullscreen is requested too when the browser allows it -- in the packaged
   window that is the difference between "a big panel" and "the room went dark". */
let immersive = false;
let stirTimer = 0;

export function toggleImmersive(on) {
  const want = on === undefined ? !immersive : !!on;
  // One full-window mode at a time. Fullscreen is kept for the roll to inherit: an exit
  // that lands after `immersive` is set would read as "fullscreen ended" and close it.
  if (want && focusing) toggleFocus(false, { keepFullscreen: true });
  if (want && !rollOpen) openRoll(true);
  // Leaving full screen ends a play-along, by every route out -- Escape, F11, the
  // window chrome. There is no smaller roll for it to fall back into.
  if (!want && ghostModel) stopGhost();
  if (!want) songs?.toggle(false);
  immersive = want;
  document.body.classList.toggle('is-immersive', immersive);
  roll?.setImmersive(immersive);
  $('#rollbar')?.toggleAttribute('hidden', !immersive);
  // The grid tracks change, so the dock keyboard is a different size and every
  // column the roll aligns to has moved.
  requestAnimationFrame(() => roll?.remeasure());

  if (immersive) {
    fullWindow(true);
    setRollSpeed(rollPx);          // paint the speed slider now the bar is visible
  } else {
    openRoll(false);
    fullWindow(false);
  }
  return immersive;
}

/* What the roll and focus share: real fullscreen when the browser allows it, and the
   controls shown for a moment on the way in. Settings closes because the gear it was
   opened from is about to vanish. */
function fullWindow(on, { keepFullscreen = false } = {}) {
  if (on) {
    closeSettings();
    document.documentElement.requestFullscreen?.().catch(() => {});
    stir();
  } else if (!keepFullscreen && document.fullscreenElement) {
    document.exitFullscreen?.().catch(() => {});
  }
}

/* Focus: one practice panel and the keys, nothing else. The panel is not moved -- it
   and its ancestors are marked and CSS hides the rest, so layout.js's observers have
   nothing to react to and leaving is removing a class. Leaving does not stop the band:
   unlike the roll, Jam has a smaller view to fall back into. */
let focusing = false;
let focusScrolls = [];  // [element, scrollTop] for each scroller focus collapses
let focusLeftAt = -1e9;

export function toggleFocus(on, { target = null, keepFullscreen = false } = {}) {
  const want = on === undefined ? !focusing : !!on;
  if (want === focusing) return focusing;
  if (want) {
    if (immersive) return false;
    const el = target || focusTarget();
    if (!el || !stage.contains(el)) return false;
    // On a page the pane scrolls, and with more than one row so does the panes area;
    // those are the positions to come back to.
    focusScrolls = [el.closest('.page__pane'), el.closest('.page__panes'), stage]
      .filter(Boolean).map((n) => [n, n.scrollTop]);
    el.dataset.focus = 'on';
    for (let n = el.parentElement; n && n !== stage; n = n.parentElement) n.dataset.focus = 'path';
    focusing = true;
    document.body.classList.add('is-focusing');
    stage.scrollTop = 0;
    // A focused "Focus" button and the next Space would toggle straight back out.
    document.activeElement?.blur?.();
    fullWindow(true);
  } else {
    for (const n of stage.querySelectorAll('[data-focus]')) delete n.dataset.focus;
    focusing = false;
    focusLeftAt = performance.now();
    document.body.classList.remove('is-focusing');
    fullWindow(false, { keepFullscreen });
  }
  // route() leaves focus and then mounts another view before this frame runs: the
  // scroll and the marks belong to the view that was focused, not to that one.
  const seq = routeSeq;
  requestAnimationFrame(() => {
    if (routeSeq !== seq) return;
    // Hidden panels come back from display:none. layout.js's ResizeObserver repacks
    // them, but kick it once so the restore does not hang on observer timing.
    if (!focusing) {
      for (const g of stage.querySelectorAll('.grid')) g.repack?.();
      for (const [n, top] of focusScrolls) n.scrollTop = top;
    }
    // The dock just changed size: put the marks back now, not on the next status frame.
    for (const m of mounted) m.view.refocus?.();
  });
  return focusing;
}

/* The first mounted view that has something to focus, and that something is on screen:
   on a page Practice can be up with Jam not picked, and focusing a hidden panel is a
   blank window. */
function focusTarget() {
  for (const m of mounted) {
    const el = m.view.focusTarget?.();
    if (el && !el.closest('.is-unpicked')) return el;
  }
  return null;
}

/* Show the way out for a moment whenever the mouse moves, then let it fade. */
function stir() {
  document.body.classList.add('is-stirring');
  clearTimeout(stirTimer);
  stirTimer = setTimeout(() => document.body.classList.remove('is-stirring'), 2400);
}
document.addEventListener('mousemove', () => { if (immersive || focusing) stir(); });

// Leaving fullscreen by any route -- F11, the window button, Esc handled by the
// browser -- has to bring the layout back with it, or the app is left pretending.
document.addEventListener('fullscreenchange', () => {
  if (document.fullscreenElement) return;
  if (immersive && cancelRollField()) return;
  if (immersive) toggleImmersive(false);
  else if (focusing) toggleFocus(false);
});

/* Build the roll and give it the panel. Internal: nothing opens the roll without
   also going full screen, so this is never the whole of what a caller wants. */
function openRoll(on) {
  rollOpen = !!on;
  document.body.classList.toggle('is-rolling', rollOpen);
  $('#roll-toggle')?.setAttribute('aria-pressed', String(rollOpen));
  $('#roll')?.setAttribute('aria-hidden', String(!rollOpen));

  if (rollOpen) {
    if (!roll) roll = createRoll($('#roll'));
    roll.setSpeed(rollPx);
    roll.setZones(ctx.state?.engine?.zones || []);
    // The panel has only just been given height, and the dock keyboard it aligns
    // to has only just been squeezed -- both are wrong until layout settles.
    requestAnimationFrame(() => roll?.remeasure());
  } else {
    roll?.clear();
  }
  return rollOpen;
}

/* The ROLL button and V. One door, and it leads to full screen. */
export function toggleRoll(on) {
  return toggleImmersive(on);
}

/* ── output volume ────────────────────────────────────────────────────────── */
/* synth.gain, which is the one audio setting FluidSynth takes live -- sample rate and
   buffer renegotiate the stream and need a restart, which is why they stay behind the
   gear and this does not. Stored as a percentage in the UI because 0.6 means nothing
   to anyone; the engine gets the fraction. */
let volTimer = 0;

export function setVolume(percent, { persist = true } = {}) {
  const pct = Math.max(0, Math.min(150, Math.round(Number(percent))));
  const sl = $('#vol');
  if (sl && document.activeElement !== sl) { sl.value = String(pct); }
  if (sl) paintSlider(sl);
  const out = $('#vol-value');
  if (out) out.textContent = String(pct);
  document.querySelector('.vol')?.classList.toggle('is-muted', pct === 0);
  if (!persist) return pct;
  // Coalesced: dragging a slider fires input on every pixel, and each one of those is
  // a settings write to disk.
  clearTimeout(volTimer);
  volTimer = setTimeout(() => {
    api.post('/api/settings', { audio: { gain: pct / 100 } }).catch(() => {});
    if (ctx.state?.settings?.audio) ctx.state.settings.audio.gain = pct / 100;
  }, 120);
  return pct;
}

/* ── ghost mode ───────────────────────────────────────────────────────────── */
/* The piece falls, silently, and you supply the sound. Nothing here calls the
   backend: the timeline was fetched once when the piece was opened, and from then on
   this is arithmetic and pixels. That is what lets a play-along keep working with no
   SoundFont and no audio device. */
let ghostModel = null;
let loopDirty = false;        // the loop-bars fields hold something typed, not yet applied

export function ghostArmed() { return !!ghostModel; }

/**
 * Start a play-along. `payload` is the body of /api/scores/{id}/notes.
 *
 * `sheet: true` opens on the engraved page instead of the falling notes. Same screen,
 * same clock, same silence -- the two are modes of one player, not two players, which
 * is why the Sheet button on a song row lands here rather than anywhere else.
 */
export function startGhost(payload, meta = {}, { sheet = false } = {}) {
  if (!payload || !(payload.notes || []).length) {
    toast('that piece has no notes to play along with', 'bad');
    return null;
  }
  stopGhost();
  ghostModel = createGhost(payload, {
    title: meta.title || payload.title,
    wait: ctx.state?.settings?.ui?.ghost_wait !== false,
    // The piece is fitted to the keys you actually have. A 61-key player opening a
    // piano piece gets it moved into reach rather than getting a play-along with its
    // bass line quietly deleted.
    low: inst.low,
    high: inst.high,
    onChange: paintGhost,
  });
  ghostModel.setTempo(meta.tempo || payload.tempo || 100);
  ghostModel.setHands(ctx.state?.settings?.ui?.ghost_hands || 'both');
  // Said out loud, once, because the app has just changed the piece on your behalf.
  // Silently moving someone's music is the kind of help that reads as a bug.
  if (ghostModel.shift) {
    toast(`Moved ${Math.abs(ghostModel.shift)} octave`
        + `${Math.abs(ghostModel.shift) === 1 ? '' : 's'} `
        + `${ghostModel.shift < 0 ? 'down' : 'up'} to fit your keyboard`
        + ` — change it on the bar`, 'good', 5200);
  }

  document.body.classList.add('is-ghosting');
  for (const id of ['#ghost-song', '#ghost-close', '#ghost-scrub', '#ghost-goto']) {
    $(id)?.removeAttribute('hidden');
  }
  songs?.setPlaying(meta.id || '');
  // Which piece the Sheet button would engrave. Set after stopGhost() above cleared
  // it, and only ever from the library metadata -- the timeline payload has no id.
  sheetScoreId = meta.id || '';
  $('#roll-mode')?.toggleAttribute('hidden', !sheetScoreId);
  paintSheetControls();
  // After sheetScoreId is set, because setSheetMode refuses without it. The 7 MB
  // engraver is still only fetched on the press that actually asks for a page.
  if (sheet && sheetScoreId) setSheetMode(true);
  $('#ghost-title').textContent = ghostModel.title;
  if ((ghostModel.warnings || []).length) {
    $('#ghost-title').title = ghostModel.warnings.join('\n');
    $('#ghost-title').classList.add('is-warn');
  } else {
    $('#ghost-title').removeAttribute('title');
    $('#ghost-title').classList.remove('is-warn');
  }
  // One staff means there is no second hand to separate, and offering the control
  // anyway would be pretending.
  const solo = ghostModel.staves.length < 2;
  $('#ghost-hands')?.classList.toggle('is-off', solo);
  for (const b of document.querySelectorAll('#ghost-hands .btn')) b.disabled = solo;
  if (solo) ghostModel.setHands('both');

  // Full screen, and not as a preference: see the comment in toggleImmersive.
  toggleImmersive(true);
  roll?.setGhost(ghostModel);
  paintGhost();
  return ghostModel;
}

/* ── the same piece, printed ──────────────────────────────────────────────── */
/* Every score is stored as MusicXML whatever it arrived as -- a .mid is converted on
   the way in -- so both readings exist for everything in the library, and swapping
   between them is a toggle rather than a conversion.
 *
 * The roll's clock keeps running under the paper, and that is the design rather than
 * an oversight. There is ONE clock here: the ghost model's. The score transport in
 * Practice is a different thing that makes sound through FluidSynth, and wiring it to
 * this screen would give you two playheads at two tempos with one of them audible --
 * see the standing note at the top of ghost.js. In the roll the sheet is a rendering,
 * not a player. Wait mode still waits, the bar count still counts, and switching back
 * lands you where the piece actually got to.
 *
 * The engraver is 7 MB of WebAssembly and is fetched on the first press of Sheet,
 * never on opening a song. Someone who only plays along never downloads it. */
let sheetMode = false;
let sheetScoreId = '';
let sheetPage = 1;
let sheetPages = 1;

/* Measure-level following, and only measure-level -- see ARCHITECTURE.md on why there
   is no note cursor. `sheetBars` is engrave.js's measure map with `bar` added: the
   ghost model's index for each printed measure, so the page and the clock agree on
   bars by construction rather than by timing. `sheetPageOf[bar]` is where it is
   printed. */
let sheetBars = [];
let sheetPageOf = [];
let engraver = null;          // engrave.js, once Sheet has been pressed -- never before
let sheetPaintKey = '';       // the bar + section last painted on the page
let sheetTurnBar = -1;        // the bar the page last followed
let sheetFollow = false;      // a deliberate move: follow it even while paused
let sheetPlaying = false;     // g.playing at the last paint, so a start can be seen

/* Which ghost bar each printed measure is. When the two lists are the same length they
   are the same measures in the same order -- both are the first part's measures in
   document order -- and position is exact. They differ only when score.py folded a
   repeated number into one bar, and then the written number is the key. */
function mapSheetBars(list) {
  const g = ghostModel;
  const same = list.length === g.measures.length;
  sheetBars = list.map((m, k) => ({ ...m, bar: same ? k : g.barAt(parseInt(m.n, 10)) }));
  sheetPageOf = [];
  for (const m of sheetBars) {
    if (m.bar >= 0 && sheetPageOf[m.bar] == null) sheetPageOf[m.bar] = m.page;
  }
}

/* Give each printed measure on the page its bar and a box to tint. The box is a rect
   at the back of the measure's own group, so it scales with the page and sits under
   the ink; everything after this is class toggles on those groups.
   Sized from the staff LINES, not the whole group: a group's box takes in every ledger
   note and stem, so a system's bars would come out all different heights. The lines are
   the same for every bar in a system, and half a staff above and below gives the notes
   just off it room. */
const SVGNS = 'http://www.w3.org/2000/svg';
function staffBox(el) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const line of el.querySelectorAll(':scope > g.staff > path')) {
    const b = line.getBBox();
    x0 = Math.min(x0, b.x); y0 = Math.min(y0, b.y);
    x1 = Math.max(x1, b.x + b.width); y1 = Math.max(y1, b.y + b.height);
  }
  if (!Number.isFinite(x0)) return el.getBBox();
  const pad = (y1 - y0) / 2;
  return { x: x0, y: y0 - pad, width: x1 - x0, height: y1 - y0 + 2 * pad };
}
function decorateSheet(paper) {
  const byId = new Map(sheetBars.map((m) => [m.id, m]));
  for (const el of paper.querySelectorAll('g.measure')) {
    const m = byId.get(el.dataset.id);
    if (!m || m.bar < 0) continue;
    el.dataset.bar = String(m.bar);
    let box;
    try { box = staffBox(el); } catch { continue; }
    const r = document.createElementNS(SVGNS, 'rect');
    r.setAttribute('class', 'rollpaper__bar');
    r.setAttribute('x', String(box.x));
    r.setAttribute('y', String(box.y));
    r.setAttribute('width', String(box.width));
    r.setAttribute('height', String(box.height));
    el.insertBefore(r, el.firstChild);
  }
  sheetPaintKey = '';
  paintSheet();
}

/* The current bar and the section, on the page. Runs from paintGhost, which the model
   calls once per bar line rather than once per frame. The page follows the bar --
   turned when it is printed on another page, scrolled when it is out of view on this
   one -- on three occasions: a deliberate move, the moment playback starts, and each
   new bar while playing. A page you turned by hand mid-bar therefore stays put until
   the music moves on; but pressing Play, or asking for a bar, is the music moving on,
   even to the bar it was already in. Wait mode is why that matters: started on a page
   that does not show the bar, the clock waits for a chord you cannot see, forever.

   A turn the engraver has not got ahead of is a synchronous engrave of a whole page.
   During a scrub drag it waits for the pointer to come up rather than engraving every
   page the drag crosses -- and so does the look-ahead below; endScrub's repaint queues
   the pages after the landing one. The pages after the playhead's are engraved ahead, in
   idle time, only while the song is stopped: an engrave is ~200 ms on this thread, and
   one done while playing costs the song that frame wherever it lands. */
function paintSheet() {
  const g = ghostModel;
  const paper = $('#roll-paper');
  const started = !!g?.playing && !sheetPlaying;
  sheetPlaying = !!g?.playing;
  if (!sheetMode || !g || !paper || !sheetBars.length) { sheetFollow = false; return; }
  const cur = g.barIndex();
  const la = g.looping ? g.barIndex(g.loopA) : -1;
  const lb = g.looping ? g.barIndex(g.loopB - 1e-4) : -2;
  const key = `${cur}:${la}:${lb}`;
  if (key !== sheetPaintKey) {
    sheetPaintKey = key;
    for (const el of paper.querySelectorAll('g.measure[data-bar]')) {
      const b = +el.dataset.bar;
      el.classList.toggle('is-now', b === cur);
      el.classList.toggle('is-loop', b >= la && b <= lb);
    }
  }
  if (sheetFollow || started || (g.playing && cur !== sheetTurnBar)) {
    sheetTurnBar = cur;
    const want = sheetPageOf[cur];
    if (want && want !== sheetPage) {
      if (scrubDrag) sheetTurnBar = -1;      // still owed: endScrub pays it
      else showPage(want, cur);
    } else revealBar(paper, cur);
  }
  sheetFollow = false;
  const on = sheetPageOf[cur];
  if (!scrubDrag && on && on < sheetPages && !g.playing) {
    engraver?.prerenderAhead(sheetScoreId, on, () => !ghostModel?.playing && !scrubDrag);
  }
}

/* Scroll the paper just far enough that the bar is in view below the rollbar. */
function revealBar(paper, bar) {
  const el = paper.querySelector(`g.measure[data-bar="${bar}"]`);
  if (!el) return;
  const pr = paper.getBoundingClientRect();
  const r = el.getBoundingClientRect();
  const bar0 = parseFloat(getComputedStyle($('#roll')).getPropertyValue('--rollbar-h')) || 0;
  const top = pr.top + bar0;
  if (r.top < top || r.bottom > pr.bottom) paper.scrollTop += r.top - top - 12;
}

/* A move the player asked for -- a bar step, Go to, a scrub, Restart. The page follows
   it even while paused, and even when it lands in the bar the page last followed:
   "never while paused" is about not undoing a page you turned by hand, and asking for
   bar 40 is not that. A move that changed nothing (Bar >> on the last bar) repaints
   nothing, so the page is brought back here instead. */
function follow(move) {
  sheetFollow = true;
  try { return move(); } finally { if (sheetFollow) paintSheet(); }
}

function paintSheetControls() {
  for (const b of document.querySelectorAll('#roll-mode .btn')) {
    b.classList.toggle('is-on', (b.dataset.mode === 'sheet') === sheetMode);
  }
  $('#roll-pages')?.toggleAttribute('hidden', !sheetMode || sheetPages < 2);
  const n = $('#roll-page-n');
  if (n) n.textContent = `${sheetPage}/${sheetPages}`;
  const prev = $('#roll-page-prev');
  const next = $('#roll-page-next');
  if (prev) prev.disabled = sheetPage <= 1;
  if (next) next.disabled = sheetPage >= sheetPages;
}

async function setSheetMode(on) {
  if (!ghostModel || !sheetScoreId) return;
  sheetMode = !!on;
  const paper = $('#roll-paper');
  paper?.toggleAttribute('hidden', !sheetMode);
  // The bar fades with the mouse, which is right over falling notes -- the point of
  // that mode is that there is nothing on screen but the instrument. It is wrong over
  // a page: reading is not watching, the page turns live in that bar, and so does the
  // way back to the roll.
  document.body.classList.toggle('is-sheeting', sheetMode);
  paintSheetControls();
  if (!sheetMode || !paper) return;

  paper.replaceChildren(h('div.empty', null, 'engraving…'));
  const wanted = sheetScoreId;
  try {
    engraver = await import('./engrave.js');
    const { loadScore, renderPage, measureMap } = engraver;
    sheetPages = await loadScore(wanted);
    const list = await measureMap(wanted);
    // The song can have been closed, or swapped, while 7 MB was downloading.
    if (!sheetMode || sheetScoreId !== wanted || !ghostModel) return;
    mapSheetBars(list);
    // Open where the piece is, not on page 1: switching over from the roll halfway
    // through lands on the bar you were playing.
    sheetTurnBar = ghostModel.barIndex();
    sheetPage = Math.max(1, Math.min(sheetPages, sheetPageOf[sheetTurnBar] || 1));
    const svg = await renderPage(wanted, sheetPage);
    if (!sheetMode || sheetScoreId !== wanted) return;
    paper.innerHTML = svg;
    paintSheetControls();
    decorateSheet(paper);
    revealBar(paper, sheetTurnBar);
  } catch (err) {
    if (sheetMode && sheetScoreId === wanted) {
      paper.replaceChildren(h('div.empty', null, 'could not engrave that: ' + err.message));
    }
  }
}

async function turnPage(step) {
  const want = Math.max(1, Math.min(sheetPages, sheetPage + step));
  if (want === sheetPage || !sheetMode) return;
  return showPage(want);
}

async function showPage(want, bar = -1) {
  if (!sheetMode) return;
  sheetPage = want;
  paintSheetControls();
  const paper = $('#roll-paper');
  const wanted = sheetScoreId;
  // Engraved ahead (paintSheet): swapped in within this frame, with no await between.
  const svg = engraver?.cachedPage(wanted, want)
    ?? await (await import('./engrave.js')).renderPage(wanted, want);
  if (!paper || !sheetMode || sheetScoreId !== wanted || sheetPage !== want) return;
  paper.innerHTML = svg;
  paper.scrollTop = 0;      // a page turn starts at the top of the page
  decorateSheet(paper);
  if (bar >= 0) revealBar(paper, bar);
}

export function stopGhost() {
  if (!ghostModel) return;
  ghostModel.pause();
  ghostModel = null;
  roll?.setGhost(null);
  ctx.kb.setGhost([]);
  document.body.classList.remove('is-ghosting');
  for (const id of ['#ghost-song', '#ghost-close', '#ghost-scrub', '#ghost-goto']) {
    $(id)?.setAttribute('hidden', '');
  }
  closeGoto();
  loopDirty = false;
  for (const id of ['#ghost-loop-from', '#ghost-loop-to']) { const i = $(id); if (i) i.value = ''; }
  // The paper belongs to the song, not to the screen: closing one closes the other.
  sheetMode = false;
  sheetScoreId = '';
  sheetPage = sheetPages = 1;
  sheetBars = [];
  sheetPageOf = [];
  sheetPaintKey = '';
  sheetTurnBar = -1;
  sheetPlaying = false;
  document.body.classList.remove('is-sheeting');
  $('#roll-paper')?.setAttribute('hidden', '');
  $('#roll-paper')?.replaceChildren();
  paintSheetControls();
  songs?.setPlaying('');
  const title = $('#ghost-title');
  if (title) { title.textContent = ''; title.classList.remove('is-warn'); }
  const read = $('#ghost-read');
  if (read) read.textContent = '';
}

function paintGhost() {
  const g = ghostModel;
  if (!g || !$('#ghost-play')) return;

  $('#ghost-play').textContent = g.playing ? 'Pause' : 'Play';
  $('#ghost-play').classList.toggle('is-on', g.playing);

  // Where the piece ended up, in words rather than a signed number -- "-1" beside a
  // Tempo slider reads as a setting you got wrong, and this one is usually the app
  // helping. The count of what still cannot be reached rides along, because a piece
  // wider than your keyboard has notes nothing can bring into range and pretending
  // otherwise is how you learn a piece with holes in it and never find out why.
  const sv = $('#ghost-shift-v');
  if (sv) {
    const oct = g.shift === 0 ? 'as written'
      : `${Math.abs(g.shift)} octave${Math.abs(g.shift) === 1 ? '' : 's'} ${g.shift < 0 ? 'down' : 'up'}`;
    const bits = [oct];
    if (g.unreachable) bits.push(`${g.unreachable} out of reach`);
    /* The one place the two octave controls can be confused for each other. This one
       moves the MUSIC; OCT above the keys moves YOUR HANDS, and with it what every key
       sounds -- so with both engaged you are playing the lit keys and hearing something
       else. That is a legitimate thing to want and a bewildering thing to stumble into,
       so it is said rather than left to be discovered. */
    if (inst.octave) bits.push(`keys are OCT ${inst.octave > 0 ? '+' : ''}${inst.octave}`);
    sv.textContent = bits.join(' · ');
    sv.classList.toggle('is-warn', g.unreachable > 0 || inst.octave !== 0);
  }
  $('#ghost-shift')?.classList.toggle('is-shifted', g.shift !== 0);
  $('#ghost-wait').setAttribute('aria-pressed', String(g.wait));
  $('#ghost-wait').classList.toggle('is-on', g.wait);
  for (const b of document.querySelectorAll('#ghost-hands .btn')) {
    b.classList.toggle('is-on', b.dataset.hands === g.hands);
  }
  $('#ghost-fill').style.width =
    (g.total ? Math.max(0, Math.min(100, (g.nowQ / g.total) * 100)) : 0).toFixed(2) + '%';

  // The section, as a band across the same bar. Width 0 is how "no section" is drawn,
  // so there is nothing to hide and nothing to show.
  const band = $('#ghost-section');
  if (band) {
    const on = g.looping && g.total;
    band.style.left = (on ? (g.loopA / g.total) * 100 : 0).toFixed(2) + '%';
    band.style.width = (on ? ((g.loopB - g.loopA) / g.total) * 100 : 0).toFixed(2) + '%';
  }

  // Left alone while it has focus, or a repaint fights your hand mid-drag. Painted
  // unconditionally otherwise: the fill is a CSS variable this sets, and skipping it
  // when the value already matched left the track empty under a thumb sitting a third
  // of the way along -- which is the common case, because an unmarked score falls back
  // to the 100 the markup already ships.
  const sl = $('#ghost-bpm');
  if (sl && document.activeElement !== sl) {
    sl.value = String(g.bpm);
    paintSlider(sl);
  }
  $('#ghost-bpm-v').textContent = String(g.bpm);

  /* The sentence that explains both knobs at once. Tempo decides how fast the music
     goes; roll speed decides how much of it fits on the glass. Neither number means
     anything alone, and their product is the only one you actually read by. */
  const ahead = roll ? roll.secondsAhead() : 0;
  // A bar's length is beats x (4 / beat-type) QUARTER notes, and bpm counts quarters --
  // so dividing by `beats` alone reports 6/8 at half its true value and cut time at
  // double. Same formula the drawn grid uses, so the sentence and the bar lines agree.
  const m0 = g.measures[0];
  const barQ = m0 ? m0.beats * (4 / m0.beat_type) : 4;
  const barsAhead = ahead * (g.bpm / 60) / barQ;
  /* B is stamped ON a bar line, which is the line AFTER the last bar of the section --
     naming bar(loopB) would name a bar the loop never plays. A hair back from it is
     the last bar you actually hear, and it stays right when B is the end of the piece
     and lands mid-bar instead. */
  const loop = g.looping
    ? `  ·  looping bars ${g.bar(g.loopA)}-${g.bar(g.loopB - 1e-4)}`
    : '';
  // "bar N / M" is its own button now -- click it to go to a bar -- and the rest of the
  // sentence follows it.
  const goto = $('#ghost-bar');
  if (goto) goto.textContent = `bar ${g.bar()} / ${g.bars()}`;
  $('#ghost-read').textContent =
    `${ahead.toFixed(1)}s ahead`
    + `  ·  about ${barsAhead.toFixed(1)} bars at ${g.bpm}` + loop;

  // The loop-by-bars fields show the section that is set, unless you are part-way
  // through typing a different one -- a repaint on the heartbeat must not eat that.
  if (!loopDirty) {
    const from = $('#ghost-loop-from');
    const to = $('#ghost-loop-to');
    const a = g.looping ? String(g.bar(g.loopA)) : '';
    const b = g.looping ? String(g.bar(g.loopB - 1e-4)) : '';
    if (from && from.value !== a) from.value = a;
    if (to && to.value !== b) to.value = b;
    if (from) from.placeholder = String(g.bar());
    if (to) to.placeholder = String(g.bar());
  }

  /* The notes you are being asked for, lit on the real keys. Only while the clock is
     actually held -- a permanent highlight is wallpaper.

     Pushed every paint rather than only on change. The keyboard's ghost layer is a
     shared channel -- the router blanks it on any hash change, and three other views
     write it -- so a cache of "what we last sent" goes stale the moment something else
     stomps the layer, and the hints then never come back. keyboard.js already diffs
     this against the live set and reuses a spare, so re-sending an unchanged array
     costs a Set walk and touches no DOM. */
  ctx.kb.setGhost(g.pending());
  paintSheet();
}

/* The songs drawer. Built once, and it owns the import path -- a file dropped on the
   roll goes straight to startGhost rather than to the engraver. */
const songs = createSongs((payload, meta) => startGhost(payload, meta));

$('#gear')?.addEventListener('click', () => openSettings(ctx));
// The rail doubles as the window's title bar in the app window; a no-op in a browser.
initTitlebar();
$('#oct-down')?.addEventListener('click', () => shiftOctave(-1));
$('#oct-up')?.addEventListener('click', () => shiftOctave(1));
// Click the number itself to come back to centre. Two presses of the other button
// would do it, but "put it back" is one intention.
$('#oct-value')?.addEventListener('click', () => { if (inst.octave) shiftOctave(-inst.octave); });
$('#roll-toggle')?.addEventListener('click', () => toggleRoll());
$('#roll-exit')?.addEventListener('click', () => toggleImmersive(false));
$('#focus-exit')?.addEventListener('click', () => toggleFocus(false));

let speedTimer = 0;
$('#vol')?.addEventListener('input', (e) => setVolume(e.target.value));
$('#roll-speed')?.addEventListener('input', (e) => {
  setRollSpeed(e.target.value);
  clearTimeout(speedTimer);
  speedTimer = setTimeout(() => {
    api.post('/api/settings', { ui: { roll_speed: rollPx } }).catch(() => {});
    if (ctx.state?.settings?.ui) ctx.state.settings.ui.roll_speed = rollPx;
  }, 200);
});

/* Restart, and it plays. A piece that has run out leaves the playhead parked at the
   end, so a bare seek would put you at bar 1 needing a second click on Play -- and
   "play it again" is one intention, not two. With a section armed it restarts THAT:
   grinding four bars and pressing Restart means those four bars. */
function restartGhost() {
  if (!ghostModel) return;
  follow(() => ghostModel.restart());
  if (!ghostModel.playing) ghostModel.play();
}

/* Back and forward by a bar. The rules (half a beat, no step off the end) are the
   model's; this only makes the page follow. */
function stepBar(dir) {
  if (ghostModel) follow(() => ghostModel.stepBar(dir));
}

$('#ghost-play')?.addEventListener('click', () => ghostModel?.toggle());
$('#ghost-restart')?.addEventListener('click', restartGhost);
$('#ghost-bar-back')?.addEventListener('click', () => stepBar(-1));
$('#ghost-bar-fwd')?.addEventListener('click', () => stepBar(1));
$('#ghost-skip')?.addEventListener('click', () => ghostModel?.skip());
/* markLoop refuses a section shorter than its hang guard, which the bar-ceiling rule
   leaves only one way to reach: stamping on the last bar line when that line is also
   the end of the piece, where there is no later one for B to take. Swallowing the false
   it returns is what made two presses look like a dead button. */
function stampLoop(end) {
  if (!ghostModel) return;
  const armed = end === 'A' ? ghostModel.setLoopA() : ghostModel.setLoopB();
  if (armed) return;
  // A on its own leaves the section open, which is the normal half-way state and not a
  // failure -- but nothing is drawn until both ends exist, so it needs saying out loud
  // or the button reads as dead.
  if (end === 'A') toast(`section starts at bar ${ghostModel.bar(ghostModel.loopA)} — now Set B`, 'good', 2600);
  else toast('there is no bar left to loop there', 'bad');
}

$('#ghost-loop-a')?.addEventListener('click', () => { loopDirty = false; stampLoop('A'); });
$('#ghost-loop-b')?.addEventListener('click', () => { loopDirty = false; stampLoop('B'); });
$('#ghost-loop-clear')?.addEventListener('click', () => { loopDirty = false; ghostModel?.clearLoop(); });

/* A section that has just been set somewhere other than where you are. Played into from
   before A and played past from after B, by the model's rule -- right for a scrub, and
   wrong for a section you have just asked for by number, which you mean to hear now. */
function enterLoop() {
  const g = ghostModel;
  if (g?.looping && (g.nowQ < g.loopA || g.nowQ >= g.loopB)) follow(() => g.restart());
}

/* Loop bars a to b, by the numbers printed on them. One field filled is one bar. */
function applyLoopBars() {
  const g = ghostModel;
  if (!g) return;
  const a = parseInt($('#ghost-loop-from')?.value, 10);
  const b = parseInt($('#ghost-loop-to')?.value, 10);
  const from = Number.isFinite(a) ? a : b;
  const to = Number.isFinite(b) ? b : a;
  loopDirty = false;
  if (!Number.isFinite(from)) { paintGhost(); return; }
  if (!g.setLoopBars(g.barAt(from), g.barAt(to))) {
    toast('there is no bar left to loop there', 'bad');
    return;
  }
  enterLoop();
}
$('#ghost-loop-apply')?.addEventListener('click', applyLoopBars);
$('#ghost-loop-bar')?.addEventListener('click', () => {
  loopDirty = false;
  if (ghostModel && !ghostModel.loopBar()) toast('there is no bar left to loop there', 'bad');
});
for (const id of ['#ghost-loop-from', '#ghost-loop-to']) {
  $(id)?.addEventListener('input', () => { loopDirty = true; });
  $(id)?.addEventListener('keydown', (e) => {
    // Out of the field once it has been applied, or the very next Space types into a
    // number box instead of starting the section you just set.
    if (e.key === 'Enter') { e.preventDefault(); applyLoopBars(); e.target.blur(); return; }
    if (e.key !== 'Escape') return;
    // Esc here means "never mind", not panic -- and in the roll, panic's first step is
    // leaving the roll, which would close the song you were setting a loop in.
    e.preventDefault();
    e.stopPropagation();
    fieldEscAt = performance.now();
    loopDirty = false;
    e.target.blur();
    paintGhost();
  });
}

/* Go to a bar: the readout's "bar N / M" turns into a number field. Enter goes, Esc
   (or clicking away) puts the readout back without moving anything. */
function openGoto() {
  const b = $('#ghost-bar');
  const i = $('#ghost-bar-in');
  if (!ghostModel || !b || !i) return;
  i.value = String(ghostModel.bar());
  i.placeholder = String(ghostModel.bar());
  b.hidden = true;
  i.hidden = false;
  i.focus();
  i.select();
}
function closeGoto() {
  const b = $('#ghost-bar');
  const i = $('#ghost-bar-in');
  if (b) b.hidden = false;
  if (i) i.hidden = true;
}
$('#ghost-bar')?.addEventListener('click', openGoto);

/* Esc in a roll field means "never mind" -- but in real fullscreen the browser keeps
   Esc for itself: the field never hears it, fullscreen ends, and leaving fullscreen
   ends the play-along. So the fullscreen exit asks this first. With a roll field
   focused, that exit WAS the field's Esc: the field is cancelled, the song and its
   loop are kept, and the roll stays full-window (EXIT or a second Esc still leave).
   A host that does hand the page its Esc can do so on either side of the exit, so a
   field that has only just eaten one counts too. */
function cancelRollField() {
  const el = document.activeElement;
  if (!ghostModel) return false;
  if (performance.now() - fieldEscAt < 250) return true;
  if (!['ghost-bar-in', 'ghost-loop-from', 'ghost-loop-to'].includes(el?.id)) return false;
  fieldEscAt = performance.now();
  if (el.id === 'ghost-bar-in') closeGoto();
  else { loopDirty = false; el.blur(); paintGhost(); }
  return true;
}
$('#ghost-bar-in')?.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    e.preventDefault();
    const n = parseInt(e.target.value, 10);
    if (ghostModel && Number.isFinite(n)) follow(() => ghostModel.seekBar(ghostModel.barAt(n)));
    closeGoto();
  } else if (e.key === 'Escape') {
    // Kept from the global Esc chain, for the same reason as the loop fields above.
    e.preventDefault();
    e.stopPropagation();
    fieldEscAt = performance.now();
    closeGoto();
  }
});
$('#ghost-bar-in')?.addEventListener('blur', closeGoto);

/* The song's own keys. Space plays and pauses; Left is a bar back; Right is the skip
   while Wait is holding you at a chord -- its old meaning, kept where it means
   something -- and a bar forward otherwise; Home back to the top.
   Not bound through ACTIONS: they only mean anything with a song loaded, and stealing
   keys app-wide to do nothing everywhere else is how a shortcut table rots. */
const SONG_KEYS = new Set(['ArrowLeft', 'ArrowRight', 'Home', ' ']);
let spaceTaken = false;       // the Space now down was a play/pause, not a button press
let fieldEscAt = -1e9;        // when a roll field last took an Esc, see cancelRollField

/* Whether the song may have this key where focus is. Not in a text field, and not in
   the songs drawer: its rows are buttons, and Space on one means "open this one". A
   slider keeps its arrows and Home, but has no use for Space, so a Tempo you just
   dragged does not switch the transport's own key off. */
function songKeyHere(key) {
  const el = document.activeElement;
  if (el?.closest?.('#songs')) return false;
  if (el?.tagName === 'INPUT' && el.type === 'range') return key === ' ';
  return !/^(INPUT|SELECT|TEXTAREA)$/.test(el?.tagName || '');
}

document.addEventListener('keydown', (e) => {
  // The Esc that fullscreen took from a roll field arrives after it, on whatever focus
  // fell back to -- and there it would read as "leave the roll". It already meant
  // "never mind"; one press is one meaning.
  if (e.key === 'Escape' && performance.now() - fieldEscAt < 250) {
    e.preventDefault();
    e.stopImmediatePropagation();
    return;
  }
  if (!ghostModel || !SONG_KEYS.has(e.key)) return;
  if (e.ctrlKey || e.altKey || e.metaKey || tourOpen() || settingsOpen()) return;
  if (!songKeyHere(e.key)) return;
  // On the paper Home already means something: the page scrolls, and a page is taller
  // than the panel. Restarting the piece is not worth taking that away.
  if (sheetMode && e.key === 'Home') return;
  // Also what stops Space pressing whichever rollbar button was clicked last, which is
  // what Space used to do here. And the key is the song's alone: a shortcut rebound to
  // Space or an arrow must not fire as well, so the ACTIONS table below never sees it.
  e.preventDefault();
  e.stopImmediatePropagation();
  if (e.key === 'Home') restartGhost();
  else if (e.key === ' ') { spaceTaken = true; if (!e.repeat) ghostModel.toggle(); }
  else if (e.key === 'ArrowLeft') stepBar(-1);
  else if (ghostModel.waiting) ghostModel.skip();
  else stepBar(1);
});
/* A button activates on Space's keyUP, so the keydown above is not the whole of it --
   but only the keyup of a Space that keydown actually took. Every other Space, in
   Settings, the tour or the drawer, has to keep pressing the button it is on. */
document.addEventListener('keyup', (e) => {
  if (e.key !== ' ' || !spaceTaken) return;
  spaceTaken = false;
  e.preventDefault();
});
// Closes the SONG, not the screen. You are usually done with a piece long before you
// are done with the roll.
$('#ghost-close')?.addEventListener('click', () => stopGhost());
$('#ghost-wait')?.addEventListener('click', () => {
  if (!ghostModel) return;
  const on = ghostModel.setWait(!ghostModel.wait);
  api.post('/api/settings', { ui: { ghost_wait: on } }).catch(() => {});
  if (ctx.state?.settings?.ui) ctx.state.settings.ui.ghost_wait = on;
});
$('#ghost-hands')?.addEventListener('click', (e) => {
  const which = e.target?.dataset?.hands;
  if (!which || !ghostModel) return;
  ghostModel.setHands(which);
  api.post('/api/settings', { ui: { ghost_hands: which } }).catch(() => {});
  if (ctx.state?.settings?.ui) ctx.state.settings.ui.ghost_hands = which;
});
$('#ghost-bpm')?.addEventListener('input', (e) => {
  paintSlider(e.target);
  ghostModel?.setTempo(e.target.value);
});
$('#ghost-shift-down')?.addEventListener('click',
  () => ghostModel?.setShift(ghostModel.shift - 1));
$('#ghost-shift-up')?.addEventListener('click',
  () => ghostModel?.setShift(ghostModel.shift + 1));
/* A scrub IS a seek here, and a seek is just a number -- there is no queue to
   rebuild, unlike the score transport. So it can follow the pointer the whole way
   along a drag. Pointer capture keeps the drag yours after the pointer leaves the
   strip, which a 12 px target needs. Shift-drag sweeps a section instead: the bars
   between where you pressed and where you are, stamped live so the band grows under
   the pointer. */
let scrubDrag = null;         // { loop, from } while a button is down on the strip
function scrubQ(e) {
  const r = $('#ghost-scrub').getBoundingClientRect();
  return Math.max(0, Math.min(1, (e.clientX - r.left) / (r.width || 1))) * ghostModel.total;
}
function scrubTo(q) {
  const g = ghostModel;
  if (scrubDrag.loop) {
    loopDirty = false;
    g.setLoopBars(g.barIndex(scrubDrag.from), g.barIndex(q));
  } else {
    follow(() => g.seek(q));
  }
}
function paintScrubTip(e, q) {
  const tip = $('#ghost-scrub-tip');
  const g = ghostModel;
  if (!tip || !g) return;
  const r = $('#ghost-scrub').getBoundingClientRect();
  const x = Math.max(36, Math.min(r.width - 36, e.clientX - r.left));
  tip.style.left = `${x}px`;
  tip.textContent = scrubDrag?.loop
    ? `loop bars ${g.bar(Math.min(scrubDrag.from, q))}-${g.bar(Math.max(scrubDrag.from, q))}`
    : `bar ${g.bar(q)}${scrubDrag ? '' : '  ·  shift-drag to loop'}`;
  tip.hidden = false;
}
const scrubEl = $('#ghost-scrub');
scrubEl?.addEventListener('pointerdown', (e) => {
  if (!ghostModel || e.button !== 0) return;
  e.preventDefault();
  scrubEl.setPointerCapture?.(e.pointerId);
  scrubEl.classList.add('is-dragging');
  const q = scrubQ(e);
  scrubDrag = { loop: e.shiftKey, from: q };
  scrubTo(q);
  paintScrubTip(e, q);
});
scrubEl?.addEventListener('pointermove', (e) => {
  if (!ghostModel) return;
  const q = scrubQ(e);
  if (scrubDrag) scrubTo(q);
  paintScrubTip(e, q);
});
function endScrub() {
  if (!scrubDrag) return;
  const wasLoop = scrubDrag.loop;
  scrubDrag = null;
  scrubEl?.classList.remove('is-dragging');
  // The one page turn the drag put off, for wherever it let go.
  if (wasLoop) enterLoop();
  if (ghostModel) follow(() => paintSheet());
}
scrubEl?.addEventListener('pointerup', endScrub);
scrubEl?.addEventListener('pointercancel', endScrub);
scrubEl?.addEventListener('lostpointercapture', endScrub);
scrubEl?.addEventListener('pointerleave', () => {
  if (!scrubDrag) $('#ghost-scrub-tip')?.setAttribute('hidden', '');
});

/* A printed measure is a place to go: click it and the playhead is on its downbeat. */
$('#roll-paper')?.addEventListener('click', (e) => {
  const el = e.target.closest?.('g.measure[data-bar]');
  if (!el || !ghostModel) return;
  follow(() => ghostModel.seekBar(+el.dataset.bar));
});

/* The paper starts below the rollbar, and the rollbar's height is not a constant --
   it wraps, and loading a song puts six more controls in it. Measured rather than
   assumed, because the case where it is tallest is exactly the case where the paper
   is on screen. */
if (window.ResizeObserver && $('#rollbar')) {
  new ResizeObserver(([entry]) => {
    const px = Math.round(entry.contentRect.height + 24);   // + the bar's own padding
    $('#roll')?.style.setProperty('--rollbar-h', `${px}px`);
  }).observe($('#rollbar'));
}

$('#roll-mode')?.addEventListener('click', (e) => {
  const want = e.target?.closest('.btn')?.dataset?.mode;
  if (want) setSheetMode(want === 'sheet');
});
$('#roll-page-prev')?.addEventListener('click', () => turnPage(-1));
$('#roll-page-next')?.addEventListener('click', () => turnPage(1));

/* Every shortcut in the app, in one table. An action is a label, a default key and
   the thing it does; the keys are rebindable and the defaults are what ships.
   `always` means the binding still fires while a text field has focus -- true only
   for panic, because the one moment you most need all-notes-off is the moment a
   stuck note is screaming and the cursor happens to be in a search box. */
export const ACTIONS = [
  ...ORDER.map((view, i) => ({
    id: `view:${view}`,
    label: view[0].toUpperCase() + view.slice(1),
    group: 'Go to',
    key: String(i + 1),
    run: () => { location.hash = view; },
  })),
  // Your first three pages, after the six tabs. Rebindable like everything else.
  ...[1, 2, 3].map((n) => ({
    id: `page:${n}`,
    label: `Page ${n} (your own)`,
    group: 'Go to',
    key: String(ORDER.length + n),
    run: () => goPage(n),
  })),
  {
    id: 'panic', label: 'Stop everything', group: 'Do', key: 'Escape', always: true,
    // The one exception to "panic always fires": in immersive and focus there is no
    // panic button on screen, and Esc is what everyone will press to leave. Panic keeps
    // its meaning everywhere the button is on screen.
    run: () => {
      // Peel one layer at a time. Escape with the drawer open means "close the
      // drawer", not "throw me out of the piece I am halfway through".
      if (songs.isOpen()) { songs.toggle(false); return; }
      // An open (i) closes itself on this same Esc; reading one is not an emergency.
      if (document.querySelector('.tip__pop:popover-open')) return;
      if (immersive) { toggleImmersive(false); return; }
      // Focus first: the band keeps playing, and the second Esc is panic. In real
      // fullscreen the browser takes Esc itself and fullscreenchange has already left
      // focus by the time this keydown arrives -- the same press, not a second one, so
      // it must not stop the band.
      if (focusing) { toggleFocus(false); return; }
      if (performance.now() - focusLeftAt < 250) return;
      panic();
    },
  },
  {
    id: 'metronome', label: 'Start / stop the metronome', group: 'Do', key: 'm',
    run: () => api.post('/api/metronome/toggle').catch(() => {}),
  },
  {
    id: 'roll', label: 'The roll — full screen, just the notes and the keys',
    group: 'Do', key: 'v',
    run: () => toggleImmersive(),
  },
  {
    // Kept as its own binding because F is what people already press for full screen,
    // and it has always meant this. V and F are two names for one door now.
    id: 'immersive', label: 'The roll (same as V)', group: 'Do', key: 'f',
    run: () => toggleImmersive(),
  },
  {
    // F and V already mean the roll, so focus has its own key. Off Practice there is
    // nothing to focus and it does nothing.
    id: 'focus', label: 'Focus — just this practice mode and the keys', group: 'Do', key: 'g',
    run: () => toggleFocus(),
  },
  {
    id: 'songs', label: 'Your songs — import and pick', group: 'Do', key: 's',
    run: () => { if (!immersive) toggleImmersive(true); songs.toggle(); },
  },
  {
    id: 'settings', label: 'Open settings', group: 'Do', key: ',',
    run: () => openSettings(ctx),
  },
  /* Z and X, the two keys a hardware controller puts OCT on, and next to each other
     under the left hand so you can reach them without looking. Safe to hit mid-phrase:
     the engine releases a key at the pitch it was struck at. */
  {
    id: 'octave-down', label: 'Down an octave', group: 'Do', key: 'z',
    run: () => shiftOctave(-1),
  },
  {
    id: 'octave-up', label: 'Up an octave', group: 'Do', key: 'x',
    run: () => shiftOctave(1),
  },
];

const DEFAULT_BINDS = Object.fromEntries(ACTIONS.map((a) => [a.id, a.key]));
let binds = { ...DEFAULT_BINDS };

/* A key is stored as e.key, lowercased when it is a single character, so "M" and
   "m" are one binding and "Escape" or "F2" keep their names. */
export const normalKey = (k) => (k && k.length === 1 ? k.toLowerCase() : k);

/* What the map names it keeps, '' being unset. An action it does not name gets its
   default unless the map gave that key to something else: saved wins. A 0.13 map has
   no page ids, and a metronome moved to 7 there must stay the metronome, not be
   shadowed by page 1 (the first match runs) -- page 1 starts unset instead. The same
   rule is what makes Settings' "that one is now unset" true for a key taken from an
   action whose default it was. */
export function setBinds(map) {
  binds = {};
  for (const [id, key] of Object.entries(map || {})) {
    if (id in DEFAULT_BINDS) binds[id] = key ? normalKey(key) : '';
  }
  const taken = new Set(Object.values(binds).filter(Boolean));
  for (const [id, key] of Object.entries(DEFAULT_BINDS)) {
    if (!(id in binds)) binds[id] = taken.has(key) ? '' : key;
  }
  paintNav();          // the page items print their key
  return { ...binds };
}

export const getBinds = () => ({ ...binds });
export const defaultBinds = () => ({ ...DEFAULT_BINDS });

document.addEventListener('keydown', (e) => {
  // The tour owns the keyboard while it is up. Without this, Esc fires panic and
  // the number keys navigate the tab out from behind the card.
  if (tourOpen() || settingsOpen()) return;
  const typing = /^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement?.tagName || '');
  const pressed = normalKey(e.key);
  for (const action of ACTIONS) {
    if (binds[action.id] !== pressed) continue;
    if (!action.always && (typing || e.ctrlKey || e.altKey || e.metaKey)) return;
    action.run();
    return;
  }
});

$('#panic').addEventListener('click', () => {
  panic();
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
  applyTheme(ctx.state?.settings?.ui?.theme);
  // Before the first route: a view that draws a range slider wants the real numbers,
  // not the 88 the dock was seeded with.
  applyRange(ctx.state?.range);
  paintUpdateBadge(ctx.state?.update);
  setBinds(ctx.state?.settings?.keys);
  setRollSpeed(ctx.state?.settings?.ui?.roll_speed ?? 100);
  setVolume((ctx.state?.settings?.audio?.gain ?? 0.6) * 100, { persist: false });
  // The roll is not restored on launch. It used to be a strip you could leave open;
  // it is now the whole window, and an app that opens into full screen because of
  // something you did last week is an app that has taken a decision off you.
  primeLayout(ctx.state);
  pages = validPages(ctx.state?.settings?.ui?.pages);
  paintNav('');
  for (const problem of ctx.state.errors || []) toast(problem, 'bad', 12000);
  await route();
  connect();
  if (ctx.state?.settings?.ui && !ctx.state.settings.ui.tour_seen) startTour(ctx);
})();
