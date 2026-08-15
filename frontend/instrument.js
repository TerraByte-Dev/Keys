/* "How many keys do you have?" -- asked once on first launch, changeable forever after.
 *
 * ONE picker, mounted in two places: the second card of the first-run tutorial, and the
 * Sound tab. That is deliberate rather than tidy-minded. The first-run question and the
 * settings control are the same question, and two implementations of it would drift --
 * the tutorial's would keep the list of sizes it shipped with while Settings grew a new
 * one, and the answer to "where do I change this?" would depend on which one you found.
 *
 * Why this exists at all: Keys was built on an 88-key P-71B and assumed one everywhere.
 * On a 61-key controller that assumption is not cosmetic. It draws twenty-seven keys you
 * do not have, sets scale exercises down in them, sight-reads into them, and marks none
 * of it as unreachable -- so the app is confidently wrong about the instrument in front
 * of you, and every wrong answer looks like your mistake.
 *
 * DETECT is here because most people do not know their controller's MIDI range, and the
 * honest way to find out is to press the two end keys. It reads the same raw note frames
 * the dock keyboard lights from, so it reports what the hardware actually sends -- which
 * is the only number that matters and is occasionally not the number on the box.
 */

import { instrument, listenNotes, setDetecting } from './app.js';
import { api, fill, h, mod, noteName, toast } from './ui.js';

/* The sizes people actually own, with the ranges they actually ship with. A 61 is
   C2..C7 and a 76 is E1..G7 -- those are conventions, not arithmetic, which is why they
   are written down rather than derived from a key count. Custom is the escape hatch for
   everything else, and Detect is for when you do not know. */
export const SIZES = [
  { keys: 25, low: 48, high: 72, note: 'two octaves' },
  { keys: 32, low: 41, high: 72, note: 'compact controller' },
  { keys: 37, low: 36, high: 72, note: 'three octaves' },
  { keys: 49, low: 36, high: 84, note: 'four octaves' },
  { keys: 61, low: 36, high: 96, note: 'five octaves' },
  { keys: 73, low: 28, high: 100, note: 'stage piano' },
  { keys: 76, low: 28, high: 103, note: 'stage piano' },
  { keys: 88, low: 21, high: 108, note: 'full piano' },
];

const sizeFor = (low, high) =>
  SIZES.find((s) => s.low === low && s.high === high) || null;

const span = (low, high) => `${noteName(low)} to ${noteName(high)}`;

/**
 * The picker, as a finished element.
 *
 * `onApply` fires after the range has been saved, so a caller that wants to move on
 * (the tutorial's Next) can. Everything else -- persisting, re-drawing the dock, the
 * toast -- happens here, because the two callers should not each have to know how.
 */
export function keyboardPicker(ctx, { onApply } = {}) {
  const inst = instrument();
  // Edited freely and only sent on Use these keys, so half-dragged sliders never reach
  // the engine. Detect writes here too, which is why it is a draft rather than the
  // live value.
  const draft = { low: inst.low, high: inst.high };
  let stopListening = null;
  let detecting = null;              // 'low' | 'high' | null

  /* Scoped to the picker's own subtree, NOT to the document.
     paint() runs once before the element has been appended anywhere -- and in the
     tutorial the card is rebuilt on every chapter change -- so a document-wide
     querySelector finds nothing the first time and, worse, could find the OTHER
     mounted copy's field once this is open in both the tour and the Sound tab. */
  const q = (sel) => el.querySelector(sel);

  const el = h('div.kbpick', null,
    h('div.kbpick__sizes', { id: 'kbpick-sizes' }),
    h('div.kbpick__custom', null,
      h('label.field', null,
        h('span.field__label', null, h('span', null, 'Lowest key'),
          h('span.field__value', { id: 'kbpick-lo-v' }, noteName(draft.low))),
        h('input', {
          type: 'range', id: 'kbpick-lo', min: 0, max: 127, step: 1, value: draft.low,
          oninput: (e) => nudge('low', Number(e.target.value)),
        })),
      h('label.field', null,
        h('span.field__label', null, h('span', null, 'Highest key'),
          h('span.field__value', { id: 'kbpick-hi-v' }, noteName(draft.high))),
        h('input', {
          type: 'range', id: 'kbpick-hi', min: 0, max: 127, step: 1, value: draft.high,
          oninput: (e) => nudge('high', Number(e.target.value)),
        }))),
    h('div.kbpick__foot', null,
      h('div.kbpick__read', { id: 'kbpick-read' }),
      h('div.btnrow', null,
        h('button.btn', { id: 'kbpick-detect', onclick: () => detect(ctx) },
          'Press my keys instead'),
        h('button.btn.btn--lg', { id: 'kbpick-apply', onclick: () => apply(ctx) },
          'Use these keys'))),
    h('div.note', { id: 'kbpick-note', style: { marginTop: '10px' } }));

  /* One slider must never cross the other, and the pair must stay wide enough to be an
     instrument -- the backend enforces both, but discovering that only after saving
     makes the sliders feel broken. */
  function nudge(which, value) {
    const min = inst.min_keys || 12;
    draft[which] = value;
    if (which === 'low' && draft.high - draft.low < min) draft.high = Math.min(127, draft.low + min);
    if (which === 'high' && draft.high - draft.low < min) draft.low = Math.max(0, draft.high - min);
    paint();
  }

  function paint() {
    const match = sizeFor(draft.low, draft.high);
    const count = draft.high - draft.low + 1;

    q('#kbpick-lo').value = String(draft.low);
    q('#kbpick-hi').value = String(draft.high);
    q('#kbpick-lo-v').textContent = noteName(draft.low);
    q('#kbpick-hi-v').textContent = noteName(draft.high);

    q('#kbpick-sizes').replaceChildren(...SIZES.map((s) => h(
      'button.kbpick__size' + (match && match.keys === s.keys ? '.is-on' : ''),
      {
        onclick: () => { draft.low = s.low; draft.high = s.high; paint(); },
        title: `${span(s.low, s.high)} — ${s.note}`,
      },
      h('span.kbpick__n', null, String(s.keys)),
      h('span.kbpick__k', null, 'keys'))));

    // fill(), not replaceChildren(): the native one stringifies a null child, so the
    // "custom" tag being absent would print the word "null" on the page. ui.js has the
    // whole story next to fill's definition.
    fill(q('#kbpick-read'),
      h('strong', null, `${count} key${count === 1 ? '' : 's'}`),
      h('span', null, `  ·  ${span(draft.low, draft.high)}`),
      match ? null : h('span.kbpick__tag', null, 'custom'));
  }

  /* Two presses, in the order the sentence reads. Modelled on the shortcut capture in
     prefs.js: armed state on screen, a way out that is always visible, and nothing
     saved until you say so. */
  function detect(ctx2) {
    if (detecting) { stopDetect('Cancelled'); return; }
    const connected = ctx2?.status?.midi?.connected ?? ctx2?.state?.midi?.connected;
    if (!connected) {
      // Refused up front rather than arming a listener that can never fire. A button
      // that waits forever is indistinguishable from a broken one.
      say('No MIDI keyboard is connected, so there are no keys to press. Open a port in '
        + 'MIDI input above, then try again.', true);
      return;
    }
    detecting = 'low';
    // While Detect is armed, pressing a key outside the current range is the whole
    // point -- the app must not scold you for doing what it just asked.
    setDetecting(true);
    q('#kbpick-detect').textContent = 'Cancel';
    q('#kbpick-detect').classList.add('is-capturing');
    say('Press the LOWEST key on your keyboard.');
    stopListening = listenNotes((on) => {
      const midi = on[0]?.[0];
      if (midi === undefined) return;
      if (detecting === 'low') {
        draft.low = midi;
        detecting = 'high';
        say(`Lowest is ${noteName(midi)}. Now press the HIGHEST key.`);
        return;
      }
      draft.high = midi;
      if (draft.low > draft.high) [draft.low, draft.high] = [draft.high, draft.low];
      const min = inst.min_keys || 12;
      if (draft.high - draft.low < min) {
        // Two keys close together is a mis-press, not a keyboard. Say so and stay put
        // rather than saving something that would redraw the dock as a stub.
        stopDetect(`That is only ${draft.high - draft.low + 1} keys apart — try again, `
                 + 'or set it with the sliders.', true);
        draft.low = instrument().low;
        draft.high = instrument().high;
        paint();
        return;
      }
      stopDetect(`${draft.high - draft.low + 1} keys, ${span(draft.low, draft.high)}. `
               + 'Press Use these keys to keep it.');
      paint();
    });
  }

  function stopDetect(message, warn = false) {
    stopListening?.();
    stopListening = null;
    detecting = null;
    setDetecting(false);
    const btn = q('#kbpick-detect');
    if (btn) {
      btn.textContent = 'Press my keys instead';
      btn.classList.remove('is-capturing');
    }
    if (message) say(message, warn);
  }

  function say(text, warn = false) {
    const note = q('#kbpick-note');
    if (!note) return;
    note.textContent = text;
    note.classList.toggle('note--warn', !!warn);
    note.classList.add('note');
  }

  async function apply(ctx2) {
    stopDetect('');
    const btn = q('#kbpick-apply');
    btn.disabled = true;
    try {
      // The backend clamps and hands back what it actually stored, so the panel shows
      // the truth rather than what it asked for.
      const res = await api.post('/api/settings',
                                 { instrument: { low: draft.low, high: draft.high } });
      const saved = res.instrument || draft;
      draft.low = saved.low;
      draft.high = saved.high;
      if (ctx2?.state?.settings) ctx2.state.settings.instrument = saved;
      // The status heartbeat redraws the dock within a second on its own; asking now
      // means it happens while you are still looking at the panel.
      if (ctx2?.state) ctx2.state.range = { ...(ctx2.state.range || {}), ...saved, keys: saved.high - saved.low + 1 };
      paint();
      toast(`${draft.high - draft.low + 1} keys, ${span(draft.low, draft.high)}`, 'good');
      onApply?.(saved);
    } catch (err) {
      toast(err.message, 'bad');
    } finally {
      btn.disabled = false;
    }
  }

  /* The listener outlives the element unless someone says otherwise, and both hosts
     tear their contents down without telling anyone. */
  el.destroy = () => stopDetect('');

  paint();
  say('Not sure? Press the lowest and highest keys and Keys will work it out.');
  return el;
}

/** The Sound tab's panel, which is the picker plus the sentence explaining why. */
export function instrumentPanel(ctx) {
  const picker = keyboardPicker(ctx);
  const el = h('div.col-12', null, mod('Your keyboard', 'how many keys you have',
    h('div.note', null,
      'Keys draws, generates and marks up exactly these keys. Set it once and the ',
      'docked keyboard, the scale and arpeggio exercises, sight reading, the pedal ',
      'range and the play-along all follow — a piece that does not fit gets moved into ',
      'reach rather than losing its bass line.'),
    h('div', { style: { marginTop: '12px' } }, picker)));
  el.destroy = () => picker.destroy?.();
  return el;
}
