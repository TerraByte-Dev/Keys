/* Tools -- things you run while you play.
 *
 * The rig you set up once and then stop thinking about: how the keys respond to your
 * hands, what the pedal does, the chord and scale reference, and the backing-track
 * shelf. Touch response and Pedal moved here from Play, which is where you go to
 * choose a sound rather than to calibrate the instrument.
 *
 * The metronome left for Play, where you are looking while you use it. */

import { createBacking } from '../backing.js';
import { createTheory } from '../theory.js';
import { $, api, fill, h, mod, noteName, paint, slider, stat, toast } from '../ui.js';
import { ctx as appCtx, instrument, resetTouch } from '../app.js';

/* Wire value -> what it means to someone with one pedal. The empty string is the
   damper, i.e. what the pedal is already for, and it is first because it is the
   default and the right answer almost always. */
const PEDAL_LABELS = [
  ['', 'Damper -- normal sustain'],
  ['zone', 'Sustain only some keys'],
  ['sostenuto', 'Sostenuto -- hold what is already down'],
  ['hold', 'Latch -- press on, press off'],
];

const PEDAL_HELP = {
  '': 'Standard piano sustain: everything rings until you lift your foot.',
  zone: 'The pedal only sustains the range below. Hold a bass note with your foot and '
      + 'play staccato on top without it smearing -- an acoustic piano physically '
      + 'cannot do this, because one set of dampers serves the whole instrument.',
  sostenuto: 'The middle pedal of a grand. It catches exactly the notes that are '
      + 'sounding at the instant you press, and nothing you play afterwards. Hold a '
      + 'chord, press, then play over the top of it cleanly.',
  hold: 'Press to sustain and take your foot off; press again to release. For a '
      + 'momentary pedal and a passage where holding your foot down is the awkward part.',
};

let backing = null;
let theory = null;

export default {
  async mount(root, ctx) {
    backing = createBacking();
    theory = createTheory();

    root.append(h('div.grid', null,
      h('div.col-3', null, mod('Touch response', 'play soft, then hard',
        h('div.touch', null,
          h('div.touch__bar', null, h('div.touch__fill', { id: 'touch-fill' })),
          h('div.touch__marks', { id: 'touch-marks' })),
        h('div.stats', { id: 'touch-stats', style: { marginTop: '12px' } }),
        h('div.btnrow', { style: { marginTop: '10px' } },
          h('button.btn', { onclick: () => { resetTouch(); paintTouch(); } }, 'Reset'),
          h('button.btn', {
            id: 'touch-show',
            onclick: (e) => {
              showTouchKeys = !showTouchKeys;
              e.target.classList.toggle('is-on', showTouchKeys);
              paintTouchKeys(ctx);
            },
          }, 'Show me the setting keys')),
        h('div.note', { id: 'touch-note', style: { marginTop: '10px' } }))),

      // With the instrument, because the instrument is what these change. They were two
      // panels in Settings, beside the MIDI ports and the buffer size, which is the rig
      // -- and two unsynced copies of one slider is a bug, so there is one copy now.
      h('div.col-6', null, mod('Pedal', 'you have one; a grand has three',
        h('div.note', null,
          'One pedal, spent on something a grand needs three for.'),
        h('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '10px', marginTop: '12px' } },
          h('label.field', null,
            h('span.field__label', null, h('span', null, 'What the pedal does')),
            h('select', { id: 'pedal-mode', onchange: () => pushPedal() },
              PEDAL_LABELS.map(([v, label]) => h('option', { value: v }, label)))),
          h('label.field', { id: 'pedal-range-lo' },
            h('span.field__label', null, h('span', null, 'Sustains from'),
              h('span.field__value', { id: 'pedal-lo-v' }, noteName(instrument().low))),
            slider({
              // Built wide open. setPedalRange narrows the track to fit whatever the
              // saved zone plus your keyboard actually needs -- doing it the other way
              // round lets the browser clamp the saved value before anyone reads it.
              min: 0, max: 127, step: 1, value: instrument().low,
              oninput: (v) => {
                $('#pedal-lo-v').textContent = noteName(v);
                previewRange(v, Number($('#pedal-range-hi input').value));
              },
              onchange: () => pushPedal(),
            })),
          h('label.field', { id: 'pedal-range-hi' },
            h('span.field__label', null, h('span', null, 'up to'),
              h('span.field__value', { id: 'pedal-hi-v' }, noteName(instrument().high))),
            slider({
              min: 0, max: 127, step: 1, value: instrument().high,
              oninput: (v) => {
                $('#pedal-hi-v').textContent = noteName(v);
                previewRange(Number($('#pedal-range-lo input').value), v);
              },
              onchange: () => pushPedal(),
            })),
          h('label.field', null,
            h('span.field__label', null, h('span', null, 'Let go after'),
              h('span.field__value', { id: 'pedal-decay-v' }, 'never')),
            slider({
              min: 0, max: 20, step: 0.5, value: 0,
              oninput: (v) => { $('#pedal-decay-v').textContent = v ? `${v}s` : 'never'; },
              onchange: () => pushPedal(),
            }))),
        h('div.btnrow', { style: { marginTop: '10px' } },
          /* "The bottom half of what you have", not "A0 to B3". On a 61-key board B3
             is a third of the way up, so the fixed note names described somebody
             else's keyboard. Halfway is the same intention wherever it lands. */
          h('button.btn', {
            id: 'pedal-split',
            onclick: () => {
              const mid = Math.round((instrument().low + instrument().high) / 2);
              setPedalRange(instrument().low, mid);
              pushPedal();
            },
          }, 'Left hand only'),
          h('button.btn', {
            onclick: () => {
              setPedalRange(instrument().low, instrument().high);
              pushPedal();
            },
          }, 'Whole keyboard')),
        h('div.note', { id: 'pedal-note', style: { marginTop: '10px' } }))),

      theory.el,
      theory.chordsEl,
      h('div.col-12', null, backing.el),
    ));

    // Seeded from whichever snapshot is already in hand -- the status feed is a
    // second away and an unpainted pedal panel reads as a broken one.
    paintPedal(ctx.state.pedal || ctx.status?.pedal, true);
    await theory.init(ctx);
    await backing.init();
  },

  frame() { paintTouch(); },

  status(s) {
    backing?.status(s);
    paintPedal(s.pedal);
  },

  unmount() {
    showTouchKeys = false;
    backing?.destroy();
    theory?.destroy();
    backing = null;
    theory = null;
  },
};

function pedalEls() {
  const mode = $('#pedal-mode');
  return mode ? { mode, lo: $('#pedal-range-lo input'), hi: $('#pedal-range-hi input') } : null;
}

/* Paint a key range onto the always-docked keyboard while you drag a slider setting it.
 *
 * "Sustains from A0 up to B3" is two note names you have to picture. The keyboard is
 * already on screen and already knows how to light keys, so showing the range there
 * turns reading into looking. Cleared on release so it never competes with what you
 * are actually playing. */
let ghostTimer = null;
function previewRange(lo, hi) {
  const kb = appCtx.kb;
  if (!kb) return;
  const keys = [];
  for (let n = Math.min(lo, hi); n <= Math.max(lo, hi); n++) keys.push(n);
  kb.setGhost(keys);
  clearTimeout(ghostTimer);
  // Time-based rather than on pointerup: sliders are also driven by the arrow keys and
  // by clicking the track, neither of which produces a drag to end.
  ghostTimer = setTimeout(() => kb.setGhost([]), 1400);
}

/* Widen the two tracks so they can HOLD the pair being shown, then set the values.
 *
 * Order is the whole point, and getting it wrong destroyed data. A range input clamps
 * any value assigned to it into [min,max]. With the tracks bounded by the declared
 * keyboard, a saved pedal zone of A0..B3 on a 61-key board was silently clamped to
 * C2 on arrival -- and pushPedal() then posts whatever the slider holds, so the next
 * unrelated nudge of the Decay slider wrote C2 back to disk and A0 was gone for good.
 * Widening back to 88 keys did not bring it back.
 *
 * engine.set_pedal deliberately keeps what you ASKED for and clamps only where the
 * pedal is used; this is the frontend keeping the same promise. */
function setPedalRange(lo, hi) {
  const els = pedalEls();
  if (!els) return;
  const inst = instrument();
  const min = Math.min(inst.low, lo, hi);
  const max = Math.max(inst.high, lo, hi);
  for (const el of [els.lo, els.hi]) {
    el.min = String(min);
    el.max = String(max);
  }
  els.lo.value = lo;
  els.hi.value = hi;
  paint(els.lo);
  paint(els.hi);
  $('#pedal-lo-v').textContent = noteName(lo);
  $('#pedal-hi-v').textContent = noteName(hi);
}

async function pushPedal() {
  const els = pedalEls();
  if (!els) return;
  const body = {
    mode: els.mode.value,
    lo: Number(els.lo.value),
    hi: Number(els.hi.value),
    decay: Number($('#pedal-decay-v').closest('.field').querySelector('input').value),
  };
  try {
    const res = await api.post('/api/pedal', body);
    paintPedal(res.pedal, true);
  } catch (err) { toast(err.message, 'bad'); }
}

/* `force` is set by pushPedal, which knows the values are new. Otherwise the selects
   and sliders are left alone -- writing to them once a second would fight a drag. */
function paintPedal(pedal, force = false) {
  if (!pedal) return;
  const els = pedalEls();
  if (!els) return;

  if (force || document.activeElement !== els.mode) els.mode.value = pedal.mode || '';
  const zoned = (pedal.mode || '') === 'zone';
  $('#pedal-range-lo').style.opacity = zoned ? '1' : '0.35';
  $('#pedal-range-hi').style.opacity = zoned ? '1' : '0.35';
  els.lo.disabled = !zoned;
  els.hi.disabled = !zoned;
  $('#pedal-split').disabled = !zoned;

  /* Painted whenever the sliders are not under your hand, not only on force. `force`
     alone meant the panel opened showing whatever the sliders were BUILT with rather
     than the zone you actually saved -- and pushPedal posts what the sliders hold, so
     the first unrelated nudge wrote that wrong pair to disk. Skipping only the focused
     slider keeps the original intent: never fight a drag. */
  if (force || (document.activeElement !== els.lo && document.activeElement !== els.hi)) {
    setPedalRange(pedal.lo, pedal.hi);
  }

  const note = $('#pedal-note');
  if (note) {
    const held = (pedal.holding || []).length;
    fill(note,
      h('span', null, PEDAL_HELP[pedal.mode || ''] || ''),
      pedal.mode
        ? h('span', null, '  ',
            h('strong', null, pedal.down ? 'Pedal down.' : 'Pedal up.'),
            held ? ` Ringing on the pedal: ${held} note${held > 1 ? 's' : ''}.` : '')
        : null,
      /* What it is actually doing, when that is not what the sliders say. The zone you
         set is kept exactly as you set it; the keys you own are what it can act on.
         Saying so beats moving your slider behind your back. */
      zoned && (pedal.eff_lo !== pedal.lo || pedal.eff_hi !== pedal.hi)
        ? h('span', null, '  ',
            `Your keyboard stops at ${noteName(instrument().low)}-${noteName(instrument().high)}, `
            + `so this is sustaining ${noteName(pedal.eff_lo)}-${noteName(pedal.eff_hi)}. `
            + 'The range above is kept as you set it.')
        : null);
  }
}

/* ── effects ──────────────────────────────────────────────────────────────── */
/* group, key, label, min, max, step, shipped default.
 *
 * Every knob in here was rendered offline against the real SoundFonts and moves the
 * output. The ones you would expect beside them -- brightness, tone, attack, release --
 * were rendered too and came back byte-identical at 0, 64 and 127, because FluidSynth
 * installs only the SF2.04 spec's ten default modulators and CC 71-75 are not among
 * them. A knob that does nothing is worse than a knob that is missing. */
const TOUCH_KEYS = [
  [57, 'FIXED'],
  [58, 'SOFT'],
  [59, 'MEDIUM'],
  [60, 'HARD'],
];
let showTouchKeys = false;

function paintTouchKeys(ctx) {
  if (!showTouchKeys) {
    ctx.kb.setHighlight([]);
    // clearLabels() puts every forced key back to its default text, and the label MODE
    // is state the keyboard keeps for itself -- so there is nothing here to restore.
    // This used to also un-highlight the scale and clear #scale-toggle, which lived in
    // the same view. Scale highlighter stayed in Play: the two can no longer be on
    // screen at once, so there is nothing to turn off on the way out.
    ctx.kb.clearLabels();
    return;
  }
  // Highlight, not ghost: the ghost layer is a muted grey that reads as "dimmed" more
  // than "look here", and the printed label sits at the bottom edge of a white key
  // where a short dock can clip it. The key itself has to carry the signal.
  ctx.kb.setHighlight(TOUCH_KEYS.map(([n]) => n));
  for (const [note, label] of TOUCH_KEYS) ctx.kb.setKeyLabel(note, label);
}

/* The whole point of this panel: make "is Touch Sensitivity still on Fixed?" answerable
   at a glance, while you are sitting at the piano, without running a CLI tool. A single
   spike means Fixed. A spread means the hammers are being reported. */
function paintTouch() {
  const fill = $('#touch-fill');
  if (!fill) return;
  const t = appCtx.touch;

  fill.style.width = `${(t.last / 127) * 100}%`;

  const marks = $('#touch-marks');
  const max = Math.max(1, ...t.hist);
  marks.replaceChildren(...t.hist.map((n, i) => h('div.touch__col', {
    style: { height: `${n ? 10 + (n / max) * 90 : 2}%` },
    class: n ? 'is-hot' : '',
    title: `velocity ${i * 8 + 1}-${i * 8 + 8}: ${n}`,
  })));

  const fixed = t.count >= 8 && t.seen.size === 1;
  const spread = t.max - t.min;
  $('#touch-stats').replaceChildren(
    stat(t.count ? t.last : '--', 'Last', 'velocity', 'stat__value--cyan'),
    stat(t.count ? `${t.min}-${t.max}` : '--', 'Range', `${t.seen.size} distinct`,
         fixed ? 'stat__value--amber' : ''),
    stat(t.count, 'Notes', 'since reset'));

  const note = $('#touch-note');
  if (!t.count) {
    note.className = 'note';
    note.replaceChildren(document.createTextNode(
      'Play a few notes as softly as you can, then as hard as you can.'));
  } else if (fixed) {
    note.className = 'note note--warn';
    note.replaceChildren(
      h('strong', null, `Touch Sensitivity is on Fixed — every note is ${t.last}.`),
      document.createTextNode(
        ' The keys are still hammer-weighted; the piano just is not reporting how hard '
        + 'you hit, and no velocity curve can invent dynamics from a constant. Press '),
      h('strong', null, 'Show me the setting keys'),
      document.createTextNode(
        ' and four keys light up on the keyboard below. Hold [GRAND PIANO/FUNCTION] on '
        + 'the piano, press the one marked MEDIUM, release both, then play loud and soft. '
        + 'Yamaha\'s manual calls that key B2; its octave numbering is one below the '
        + 'standard, so it is the white key immediately left of middle C.'));
  } else if (spread < 40) {
    note.className = 'note';
    note.replaceChildren(
      h('strong', null, `Working, but a narrow range (${spread}).`),
      document.createTextNode(' Try playing much softer and much harder.'));
  } else {
    note.className = 'note';
    note.replaceChildren(
      h('strong', null, `Touch response is working — ${spread} points of range.`),
      document.createTextNode(' Velocity curves in Layers will do something now.'));
  }
}

