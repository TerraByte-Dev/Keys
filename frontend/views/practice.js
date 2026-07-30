/* Practice -- the shelf.
 *
 * This tab used to be the analytics dashboard. It is not any more: the calendar,
 * totals, velocity and key-usage panels all live in Stats, where looking at them is a
 * deliberate act rather than the first thing between you and the piano.
 *
 * What is left is a workspace. Tiles saying what is happening right now, and a shelf
 * of things to work on. Both are cheap: the tiles are painted from the 1 Hz status
 * frame with no fetch at all, and the shelf is one GET on mount.
 *
 * Timing stayed. It reads the last ~96 notes you played, so it is a "now" number and
 * not a long-view one -- and it is the only place in the app that shows what
 * backend/timing.py computes every second. Stats is built once from one request and
 * has nowhere to put a figure that moves while you watch it.
 *
 * The one line that is not obvious is frame(). Exercise feedback rides the 60 Hz
 * websocket frame as `f.ex`, and app.js only calls frame() on the mounted VIEW -- so
 * this module has to hand it down to the open run. Without that forwarder the staff
 * renders, the run starts, and nothing ever advances, which looks exactly like a
 * backend that stopped grading.
 */

import { createSheet } from '../sheet.js';
import { $, api, fill, h, hms, mod, stat, toast } from '../ui.js';
import { createRunner } from '../exercise-run.js';

let shelf = null;      // last GET /api/exercises
let current = null;    // the open runner, or null when the shelf is showing
let sheet = null;      // the score library panel

export default {
  async mount(root, ctx) {
    sheet = createSheet(ctx);
    root.append(h('div.grid', null,
      h('div.col-6', null, mod('Now', null,
        h('div.stats', { id: 'practice-hud' },
          h('div.empty', null, 'waiting for the first note')),
        h('div.btnrow', { style: { marginTop: '14px' } },
          h('button.btn', { onclick: () => endSession() }, 'End session')))),
      h('div.col-6', null, mod('Timing', 'from your last ~96 notes',
        h('div.stats', { id: 'practice-timing' },
          h('div.empty', null, 'play something')),
        h('div.note', { style: { marginTop: '12px' } },
          'Drift is the number to watch, not per-beat error. Players hold even spacing ',
          'while the whole tempo slides -- that is the failure mode the research found.'))),
      h('div.grid.col-12', { id: 'ex-host' },
        h('div.col-12', null, h('div.empty', null, 'loading exercises...'))),
      h('div.col-12', null, sheet.el)));

    await load(ctx);
    await sheet.init();
  },

  frame(f, ctx) { current?.frame?.(f, ctx); },

  status(s, ctx) {
    sheet?.status?.(s);
    paintHud(s.practice || {});
    paintTiming(s.timing);
    current?.status?.(s, ctx);
  },

  unmount() {
    current?.destroy?.();
    current = null;
    sheet?.destroy?.();
    sheet = null;
  },
};

/* ── the header HUD ───────────────────────────────────────────────────────── */
// Amber means the clock is running, the same way amber means sounding everywhere else.
// It goes out the moment the idle gap opens, because a paused clock that still looks
// live is the one thing that would make the session number a lie.
function paintHud(p) {
  const host = $('#practice-hud');
  if (!host) return;
  const live = p.session_active && !p.idle;
  const where = !p.session_active ? 'not started' : p.idle ? 'idle -- clock paused' : 'playing';
  host.replaceChildren(
    stat(hms(p.session_seconds), 'This session',
         `${where} -- ${p.session_notes ?? 0} notes`, live ? 'stat__value--amber' : ''),
    stat(hms(p.today_seconds), 'Today', `${p.today_sessions || 0} session(s)`),
    stat(p.streak?.current ?? 0, 'Day streak', `longest ${p.streak?.longest ?? 0}`,
         'stat__value--cyan'));
}

/* ── the timing readout ───────────────────────────────────────────────────── */
// Every card is omitted rather than zeroed when its input is missing: "no drift"
// and "not enough notes to say" are different readings, and only one of them is
// worth acting on.
function paintTiming(t) {
  const host = $('#practice-timing');
  if (!host) return;
  if (!t || !t.tempo || t.tempo.bpm == null) {
    host.replaceChildren(h('div.empty', null, 'not enough notes yet'));
    return;
  }
  const d = t.drift || {};
  const s = t.steadiness || {};
  const g = t.grid;
  const cards = [
    stat(Math.round(t.tempo.bpm), 'Tempo', 'bpm', 'stat__value--amber'),
    stat(s.rating || '--', 'Steadiness'),
  ];
  if (d.bpm_per_min != null) {
    cards.push(stat(
      (d.bpm_per_min > 0 ? '+' : '') + d.bpm_per_min.toFixed(1),
      'Drift', d.steady ? 'holding tempo' : (d.bpm_per_min < 0 ? 'slowing down' : 'speeding up'),
      d.steady ? 'stat__value--cyan' : 'stat__value--amber'));
  }
  if (g && g.n) {
    cards.push(stat(
      (g.mean_ms > 0 ? '+' : '') + g.mean_ms.toFixed(0),
      'Vs metronome', g.rushing ? 'rushing' : g.dragging ? 'dragging' : 'on the beat'));
  }
  host.replaceChildren(...cards);
}

async function endSession() {
  try {
    const res = await api.post('/api/practice/end');
    paintHud(res.practice || {});
    toast('Session ended', 'good');
  } catch (err) {
    toast(err.message, 'bad');
  }
}

/* ── the shelf ────────────────────────────────────────────────────────────── */
// Every paint re-finds the host instead of closing over it, and gives up when it is
// gone: mount() awaits two requests, and a tab change inside that window has already
// emptied the stage. Painting into a detached node is harmless; dereferencing null is
// an unhandled rejection nobody sees.
const host = () => $('#ex-host');

async function load(ctx) {
  try {
    shelf = await api.get('/api/exercises');
  } catch (err) {
    host()?.replaceChildren(h('div.col-12', null,
      mod('Exercises', null, h('div.empty', null, 'could not load exercises: ' + err.message))));
    return;
  }
  paintShelf(ctx);

  // A run survives a tab change, so pick one back up rather than stranding it.
  try {
    const st = await api.get('/api/exercises/state');
    const ex = st?.running && byId(st.exercise);
    if (ex) openRun(ex, ctx, st);
  } catch { /* no run in flight */ }
}

function byId(id) {
  return (shelf?.exercises || []).find((e) => e.id === id) || null;
}

function paintShelf(ctx) {
  const list = shelf.exercises || [];
  const recent = shelf.recent || [];

  fill(host(),
    recent.length ? h('div.col-12', null, mod('Pick up where you left off', null,
      h('div.list', null, recent.slice(0, 6).map((r) => recentRow(r, ctx))))) : null,

    h('div.col-12', null, mod('Exercises', `${list.length} to work on`,
      list.length
        ? h('div.builds', null, list.map((ex) => card(ex, ctx)))
        : h('div.empty', null, 'no exercise types registered'))));
}

function card(ex, ctx) {
  return h('div.build', null,
    h('div.build__title', null, ex.name),
    h('div.build__why', null, ex.blurb),
    h('button.btn.btn--wide', { onclick: () => openRun(ex, ctx) }, 'Open'));
}

/* The variant a run was recorded under is a slug, not a parameter set, so reopening
   goes to that exercise's setup rather than pretending to restore it exactly. */
function recentRow(r, ctx) {
  const ex = byId(r.exercise);
  const when = new Date((r.at || 0) * 1000);
  return h('div.list__row', null,
    h('span.mono', null, when.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })),
    h('span', null, r.title || r.variant || r.exercise),
    h('span.list__spacer'),
    r.accuracy != null
      ? h('span.tag.tag--amber', null, Math.round(r.accuracy * 100) + '%')
      : null,
    ex ? h('button.btn', { onclick: () => openRun(ex, ctx) }, 'Open') : null);
}

/* ── switching between the shelf and a run ────────────────────────────────── */
// Named openRun, not open: a bare `open` at module scope shadows window.open.
function openRun(ex, ctx, initial = null) {
  current?.destroy?.();
  current = createRunner(ex, ctx, () => back(ctx), initial);
  host()?.replaceChildren(current.el);
}

function back(ctx) {
  current?.destroy?.();
  current = null;
  paintShelf(ctx);
}
