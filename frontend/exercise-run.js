/* One exercise run: setup form, staff, live feedback, graded result.
 *
 * Split out of the Practice view because that view is a shelf -- it lists what exists
 * and hands off. Nothing in here knows which exercise it is running: the setup form is
 * generated from the Param schema the server sends, so a new generator on the backend
 * costs zero JavaScript. The day this file grows an `if (ex.id === ...)` the design has
 * failed and the fix belongs in backend/exercises, not here.
 *
 * The failure that looks like a backend bug: feedback arrives on the 60 Hz websocket
 * frame as `f.ex`, and app.js only calls frame() on the mounted VIEW. If practice.js
 * does not forward it here, the staff renders, the run starts, and nothing ever
 * advances.
 */

import { api, field, h, mod, noteName, slider, stat, toast, toggle } from './ui.js';
import { markStep, renderStaff } from './staff.js';

/**
 * @param ex       one entry from GET /api/exercises -- {id, name, blurb, timed_default, params}
 * @param ctx      the shared app context (state, kb, toast)
 * @param onExit   called when the user wants the shelf back
 * @param initial  a run state to adopt, for the case where a run is already in flight
 */
export function createRunner(ex, ctx, onExit, initial = null) {
  const values = {};
  for (const p of ex.params || []) values[p.id] = p.default;
  Object.assign(values, initial?.params || {});

  let plan = null;        // last run state from the server
  let staffSvg = null;
  let result = null;
  let stopping = false;
  let tally = blankTally();

  const titleEl = h('span', null, ex.blurb);
  const staffHost = h('div');
  const statsHost = h('div.stats');
  const resultHost = h('div');
  const formHost = h('div.params', null, (ex.params || []).map(paramControl));

  // A 12-wide nested grid: the host in practice.js is itself a grid, and the runner's
  // panels lay out inside it exactly as the shelf's do.
  const el = h('div.grid.col-12', null,
    h('div.col-12', null, mod(ex.name, titleEl,
      staffHost,
      h('div.btnrow', { style: { marginTop: '12px' } },
        h('button.btn.btn--lg', { onclick: start }, 'Start'),
        h('button.btn', { onclick: () => finish() }, 'Stop'),
        h('button.btn', { onclick: exit }, 'Back to the shelf')))),

    h('div.col-5', null, mod('Setup',
      ex.timed_default ? 'timed against the click' : 'untimed', formHost)),

    h('div.col-7', null, mod('This run', null, statsHost, resultHost)));

  paintStaff();
  paintStats();
  if (initial && initial.running) adopt(initial);

  return {
    el,
    frame,
    destroy() { clearKeys(); },
  };

  /* ── the schema-driven form ─────────────────────────────────────────────── */
  function paramControl(p) {
    const set = (v) => { values[p.id] = v; };

    if (p.kind === 'bool') {
      const t = toggle(p.label, !!values[p.id], set);
      return p.help ? h('div.field', null, t, help(p)) : t;
    }

    // A key parameter is a choice whose options come from the server's key list rather
    // than from the Param itself -- the only kind whose choices are not self-contained.
    const choices = p.kind === 'key'
      ? (ctx.state.keys || ['C']).map((k) => ({ value: k, label: k }))
      : (p.choices || []);

    if (choices.length) {
      // Options have to exist before .value takes, so it is set after construction and
      // read back by index -- which also preserves non-string choice values.
      const sel = h('select', { onchange: (e) => set(choices[e.target.selectedIndex].value) },
        choices.map((c) => h('option', { value: c.value }, c.label)));
      sel.value = String(values[p.id]);
      return withHelp(field(p.label, null, sel), p);
    }

    if (p.hi > p.lo) {
      const show = (v) => (p.kind === 'note' ? noteName(v) : String(v));
      const readout = h('span.field__value', null, show(values[p.id]));
      return withHelp(field(p.label, readout, slider({
        min: p.lo, max: p.hi, value: values[p.id],
        oninput: (v) => { readout.textContent = show(v); set(v); },
      })), p);
    }

    return withHelp(field(p.label, null, h('input', {
      type: 'text', value: String(values[p.id] ?? ''),
      onchange: (e) => set(e.target.value),
    })), p);
  }

  function withHelp(node, p) {
    if (p.help) node.append(help(p));
    return node;
  }

  function help(p) { return h('div.field__help', null, p.help); }

  /* ── run lifecycle ──────────────────────────────────────────────────────── */
  async function start() {
    try {
      adopt(await api.post(`/api/exercises/${ex.id}/start`, values));
    } catch (err) {
      toast(err.message, 'bad');
    }
  }

  function adopt(state) {
    plan = state;
    result = null;
    tally = blankTally();
    for (const r of plan.records || []) {
      if (r.skipped) continue;
      tally.scored += 1;
      if (r.correct) tally.correct += 1;
    }
    titleEl.textContent = plan.title || ex.blurb;
    resultHost.replaceChildren();
    paintStaff();
    paintKeys();
    paintStats();
  }

  async function finish() {
    if (stopping) return;
    stopping = true;
    try {
      // {running, result}. result is null when nothing was running -- Stop pressed
      // twice, or before Start -- and there is no grade to show for that.
      result = (await api.post('/api/exercises/stop')).result;
    } catch (err) {
      toast(err.message, 'bad');
      stopping = false;
      return;
    }
    stopping = false;
    if (plan) plan.running = false;
    clearKeys();
    paintResult();
  }

  function exit() {
    clearKeys();
    onExit();
  }

  /* ── frames ─────────────────────────────────────────────────────────────── */
  function frame(f) {
    const fb = f.ex;
    if (!fb || !plan) return;

    // Mark the head now rather than on the next status frame -- a tenth of a second
    // between the key going down and the staff acknowledging it reads as lag.
    markStep(staffSvg, fb.index, fb.correct ? 'is-done' : 'is-wrong');
    if (fb.scored) {
      tally.scored += 1;
      if (fb.correct) tally.correct += 1;
    }
    if (fb.reaction_ms != null) tally.reaction = fb.reaction_ms;

    if (fb.next != null) {
      plan.index = fb.next;
      markStep(staffSvg, plan.index, 'is-target');
      paintKeys();
    }
    paintStats();
    if (fb.complete) finish();
  }

  /* ── painting ───────────────────────────────────────────────────────────── */
  function paintStaff() {
    if (!plan) {
      staffHost.replaceChildren(h('div.empty', null, 'set it up, then press Start'));
      return;
    }
    if (plan.staff === 'none') {
      staffSvg = null;
      staffHost.replaceChildren(h('div.empty', null, 'this one is played by ear -- watch the keyboard'));
      return;
    }
    staffSvg = renderStaff({
      // The generator says "grand"; the renderer says "both". Same thing.
      clefs: plan.staff === 'grand' ? 'both' : plan.staff,
      keySignature: plan.key_signature || null,
      steps: plan.steps || [],
      cursor: plan.index ?? 0,
      active: !!plan.running,
      showFingers: !!plan.show_fingers,
      empty: 'nothing to play',
    });
    staffHost.replaceChildren(staffSvg);
    for (const r of plan.records || []) {
      if (!r.correct && !r.skipped) markStep(staffSvg, r.idx, 'is-wrong');
    }
  }

  function paintStats() {
    if (!plan) {
      statsHost.replaceChildren(h('div.empty', null, 'not started'));
      return;
    }
    const total = (plan.steps || []).length;
    statsHost.replaceChildren(
      stat(`${Math.min(plan.index ?? 0, total)}/${total}`, 'Step',
           plan.timed ? `${Math.round(plan.bpm)} bpm, ${plan.key}` : `untimed, ${plan.key}`,
           'stat__value--amber'),
      stat(tally.scored ? Math.round((tally.correct / tally.scored) * 100) + '%' : '--',
           'Right so far', `${tally.correct}/${tally.scored}`),
      stat(tally.reaction != null ? Math.round(tally.reaction) + 'ms' : '--',
           'Last reaction', 'to find the key', 'stat__value--cyan'));
  }

  function paintResult() {
    if (!result) return;
    const r = result;
    const cards = [
      stat(r.accuracy != null ? Math.round(r.accuracy * 100) + '%' : '--', 'Accuracy',
           `${r.correct ?? 0}/${r.steps ?? 0} steps`, 'stat__value--amber'),
      stat(r.evenness || '--', 'Evenness',
           r.evenness_cv != null ? `cv ${r.evenness_cv.toFixed(3)}` : 'not enough notes'),
    ];
    if (r.tempo_bpm != null) {
      cards.push(stat(Math.round(r.tempo_bpm), 'Tempo', 'what you actually played at',
                      'stat__value--cyan'));
    }
    // Both of these only exist when the exercise asked for them -- hands-together steps
    // and generator-flagged crossings. Absent is not zero, so they are simply omitted.
    if (r.sync_ms != null) {
      cards.push(stat(r.sync_ms.toFixed(1) + 'ms', 'Hands together',
                      'spread between the hands'));
    }
    if (r.crossing_ms != null) {
      cards.push(stat((r.crossing_ms > 0 ? '+' : '') + r.crossing_ms.toFixed(1) + 'ms',
                      'Thumb crossing', 'versus the gaps either side'));
    }
    resultHost.replaceChildren(
      h('div.stats', { style: { marginTop: '16px' } }, cards),
      h('div.note', { style: { marginTop: '12px' } },
        'Measurements, not a verdict. The number worth watching is evenness across ',
        'days -- accuracy on one run mostly tells you how fast you took it.'));
  }

  /* ── the dock keyboard ──────────────────────────────────────────────────── */
  function paintKeys() {
    const step = plan?.steps?.[plan.index];
    if (!plan || !plan.show_keyboard || !plan.running || !step) { clearKeys(); return; }
    ctx.kb.setGhost(step.midi || []);
    ctx.kb.clearLabels();
    if (!plan.show_fingers) return;
    (step.midi || []).forEach((midi, i) => {
      const finger = step.fingers?.[i];
      if (finger) ctx.kb.setKeyLabel(midi, String(finger));
    });
  }

  function clearKeys() {
    ctx.kb.setGhost([]);
    ctx.kb.clearLabels();
  }
}

function blankTally() {
  return { correct: 0, scored: 0, reaction: null };
}
