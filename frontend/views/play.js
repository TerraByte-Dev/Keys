/* Play -- the view you are actually looking at while playing.
 *
 * Everything here has to be reachable without taking your hands off the keys for
 * long: presets are one click, the instrument browser filters as you type, and the
 * scale highlighter paints straight onto the dock keyboard. */

import { $, api, h, mod, noteName, paint, slider, stat, toast } from '../ui.js';
import { ctx as appCtx, resetTouch } from '../app.js';

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
  '': 'Standard piano sustain: everything you play rings until you lift your foot. '
      + 'FluidSynth does this itself, so nothing in Keys is in the way of it.',
  zone: 'The pedal only sustains the range below. Hold a bass note with your foot and '
      + 'play staccato on top without it smearing -- an acoustic piano physically '
      + 'cannot do this, because one set of dampers serves the whole instrument.',
  sostenuto: 'The middle pedal of a grand. It catches exactly the notes that are '
      + 'sounding at the instant you press, and nothing you play afterwards. Hold a '
      + 'chord, press, then play over the top of it cleanly.',
  hold: 'Press to sustain and take your foot off; press again to release. For a '
      + 'momentary pedal and a passage where holding your foot down is the awkward part.',
};

const MODES = {
  major: [0, 2, 4, 5, 7, 9, 11],
  natural_minor: [0, 2, 3, 5, 7, 8, 10],
  harmonic_minor: [0, 2, 3, 5, 7, 8, 11],
  melodic_minor: [0, 2, 3, 5, 7, 9, 11],
  dorian: [0, 2, 3, 5, 7, 9, 10],
  phrygian: [0, 1, 3, 5, 7, 8, 10],
  lydian: [0, 2, 4, 6, 7, 9, 11],
  mixolydian: [0, 2, 4, 5, 7, 9, 10],
  locrian: [0, 1, 3, 5, 6, 8, 10],
  major_pentatonic: [0, 2, 4, 7, 9],
  minor_pentatonic: [0, 3, 5, 7, 10],
  blues: [0, 3, 5, 6, 7, 10],
};

const ROOT_PC = { C: 0, 'C#': 1, Db: 1, D: 2, Eb: 3, E: 4, F: 5, 'F#': 6, Gb: 6,
                  G: 7, Ab: 8, A: 9, Bb: 10, B: 11, Cb: 11 };

let instruments = [];
let instrumentsSf = '';   // which SoundFont /api/instruments enumerated
let filter = '';
let scaleOn = false;

export default {
  async mount(root, ctx) {
    const st = ctx.state;
    const eng = st.engine || {};

    root.append(h('div.grid', null,
      h('div.col-7', null, mod('Presets',
        h('span', { id: 'preset-state' }, `${(st.presets || []).length} on file`),
        h('div.chips', { id: 'preset-chips' },
          (st.presets || []).map((p) => chip(p, eng.preset_id, ctx))),
        h('div.note', { style: { marginTop: '12px' } },
          'Overlap is the layering mechanism. A preset with two zones over the same keys ',
          'sounds both -- see ', h('strong', null, 'Layers'), ' to build your own.'))),

      h('div.col-5', null, mod('Sound', null,
        h('div.stats', { id: 'play-stats' },
          stat(eng.voices ?? 0, 'Voices', null, 'stat__value--amber'),
          stat(eng.buffer_ms ?? 'sys', 'Buffer ms',
               eng.exclusive ? 'WASAPI exclusive' : 'WASAPI shared'),
          stat(eng.polyphony ?? 256, 'Polyphony')),
        h('div', { style: { marginTop: '14px' } },
          h('span.field__label', null, h('span', null, 'Master gain'),
            h('span.field__value', { id: 'gain-val' }, String(eng.gain ?? 0.6))),
          slider({
            min: 0, max: 1.2, step: 0.02, value: eng.gain ?? 0.6,
            oninput: (v) => { $('#gain-val').textContent = v.toFixed(2); },
            onchange: (v) => api.post('/api/settings', { audio: { gain: v } }),
          })))),

      h('div.col-7', null, mod('Instrument browser', null,
        h('div', { style: { display: 'flex', gap: '8px', marginBottom: '10px' } },
          h('input', {
            type: 'text', placeholder: 'filter -- rhodes, organ, kit...',
            // Seeded from the module-scoped filter so the box and the list can never
            // disagree: leaving Play and coming back used to show an empty-looking
            // input that was still filtering.
            value: filter, style: { flex: '1' },
            oninput: (e) => { filter = e.target.value.toLowerCase(); renderList(ctx); },
          }),
          h('button.btn', { onclick: () => audition(ctx) }, 'Audition')),
        h('div.scroller', null, h('div.list', { id: 'inst-list' },
          h('div.empty', null, 'loading...'))))),

      h('div.col-7', null, mod('Touch response', 'play soft, then hard',
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

      h('div.col-12', null, mod('Pedal', 'you have one; a grand has three',
        h('div.note', null,
          'Your piano has a damper pedal, and by default that is exactly what it is. ',
          'The other settings here spend that one pedal on something it cannot ',
          'otherwise do.'),
        h('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '10px', marginTop: '12px' } },
          h('label.field', null,
            h('span.field__label', null, h('span', null, 'What the pedal does')),
            h('select', { id: 'pedal-mode', onchange: () => pushPedal() },
              PEDAL_LABELS.map(([v, label]) => h('option', { value: v }, label)))),
          h('label.field', { id: 'pedal-range-lo' },
            h('span.field__label', null, h('span', null, 'Sustains from'),
              h('span.field__value', { id: 'pedal-lo-v' }, 'A0')),
            slider({
              min: 21, max: 108, step: 1, value: 21,
              oninput: (v) => { $('#pedal-lo-v').textContent = noteName(v); },
              onchange: () => pushPedal(),
            })),
          h('label.field', { id: 'pedal-range-hi' },
            h('span.field__label', null, h('span', null, 'up to'),
              h('span.field__value', { id: 'pedal-hi-v' }, 'C8')),
            slider({
              min: 21, max: 108, step: 1, value: 108,
              oninput: (v) => { $('#pedal-hi-v').textContent = noteName(v); },
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
          h('button.btn', {
            id: 'pedal-split',
            onclick: () => { setPedalRange(21, 59); pushPedal(); },
          }, 'Left hand only (A0-B3)'),
          h('button.btn', {
            onclick: () => { setPedalRange(21, 108); pushPedal(); },
          }, 'Whole keyboard')),
        h('div.note', { id: 'pedal-note', style: { marginTop: '10px' } }))),

      h('div.col-5', null, mod('Scale highlighter', null,
        h('div', { style: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px' } },
          h('select', { id: 'scale-root', onchange: () => paintScale(ctx) },
            Object.keys(ROOT_PC).map((k) => h('option', { value: k }, k))),
          h('select', { id: 'scale-mode', onchange: () => paintScale(ctx) },
            Object.keys(MODES).map((m) =>
              h('option', { value: m }, m.replace(/_/g, ' '))))),
        h('div.btnrow', { style: { marginTop: '10px' } },
          h('button.btn', {
            id: 'scale-toggle',
            onclick: (e) => {
              scaleOn = !scaleOn;
              e.target.classList.toggle('is-on', scaleOn);
              paintScale(ctx);
            },
          }, 'Show on keys'),
          h('button.btn', {
            onclick: (e) => {
              const modes = ['none', 'c-only', 'all'];
              const next = modes[(modes.indexOf(labelMode) + 1) % 3];
              labelMode = next;
              ctx.kb.setLabels(next);
              e.target.textContent = 'Labels: ' + next;
            },
          // Rendered from the variable, not hardcoded. Label mode is a property of the
          // always-docked keyboard -- like sustain, it should survive navigation -- so
          // the fix is an honest label, not resetting the mode.
          }, 'Labels: ' + labelMode)),
        h('div.note', { style: { marginTop: '12px' } },
          'Highlighted keys are the scale. They do not change what sounds -- ',
          'this is a reading aid, not a lock.'))),
    ));

    // Seeded from state rather than left at the markup's defaults, so the panel does
    // not show "Damper / A0-C8" for a second before the first status frame corrects it.
    paintPedal(st.pedal || ctx.status?.pedal, true);

    const root0 = ctx.keySignature();
    if (ROOT_PC[root0] !== undefined) $('#scale-root').value = root0;

    try {
      const res = await api.get('/api/instruments');
      instruments = res.instruments || [];
      // Carry the SoundFont these bank/program numbers came from. Without it, loading
      // an instrument into a zone pointing at a different SF2 silently falls back to
      // Grand Piano, because that bank/program need not exist over there.
      instrumentsSf = res.soundfont || '';
      renderList(ctx);
    } catch (err) {
      $('#inst-list').replaceChildren(h('div.empty', null, 'could not load: ' + err.message));
    }
  },

  frame() { paintTouch(); },

  status(s, ctx) {
    const host = $('#play-stats');
    if (!host || !s.engine) return;
    host.children[0].firstChild.textContent = s.engine.voices ?? 0;
    // Keep the preset chips honest if the preset changed from another tab or the API.
    // An empty preset_id means no saved preset is loaded, so every chip goes dark and
    // the aside says what you are actually hearing.
    const chips = $('#preset-chips');
    if (chips) {
      for (const c of chips.children) {
        c.classList.toggle('is-active', c.dataset.id === s.engine.preset_id);
      }
    }
    const aside = $('#preset-state');
    if (aside) {
      aside.textContent = s.engine.preset_id
        ? `${(ctx.state.presets || []).length} on file`
        : `unsaved -- ${s.engine.preset_name || 'custom'}`;
    }
    paintPedal(s.pedal);
  },

  unmount() { scaleOn = false; showTouchKeys = false; },
};

let labelMode = 'c-only';

/* ── pedal ────────────────────────────────────────────────────────────────── */
/* Guarded against paint-after-unmount: status() fires once a second and can arrive
   after the router has replaced the stage. */
function pedalEls() {
  const mode = $('#pedal-mode');
  return mode ? { mode, lo: $('#pedal-range-lo input'), hi: $('#pedal-range-hi input') } : null;
}

function setPedalRange(lo, hi) {
  const els = pedalEls();
  if (!els) return;
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

  if (force) setPedalRange(pedal.lo, pedal.hi);

  const note = $('#pedal-note');
  if (note) {
    const held = (pedal.holding || []).length;
    note.replaceChildren(
      h('span', null, PEDAL_HELP[pedal.mode || ''] || ''),
      pedal.mode
        ? h('span', null, '  ',
            h('strong', null, pedal.down ? 'Pedal down.' : 'Pedal up.'),
            held ? ` Ringing on the pedal: ${held} note${held > 1 ? 's' : ''}.` : '')
        : null);
  }
}

function chip(preset, activeId, ctx) {
  return h('button.chip', {
    'data-id': preset.id,
    class: preset.id === activeId ? 'is-active' : '',
    title: preset.description || preset.name,
    onclick: async (e) => {
      try {
        const res = await api.post(`/api/presets/${preset.id}/load`);
        for (const c of e.target.closest('.chips').children) c.classList.remove('is-active');
        e.target.closest('.chip').classList.add('is-active');
        for (const w of res.warnings || []) toast(w, 'bad', 7000);
        ctx.state.engine = res.engine;
      } catch (err) { toast(err.message, 'bad'); }
    },
  },
    preset.name,
    preset.zones.length > 1 ? h('span.chip__zones', null, preset.zones.length + 'Z') : null);
}

function renderList(ctx) {
  const host = $('#inst-list');
  if (!host) return;
  const rows = instruments
    .filter((i) => !filter || i.name.toLowerCase().includes(filter)
                || String(i.program) === filter)
    .slice(0, 300);
  if (!rows.length) {
    host.replaceChildren(h('div.empty', null, 'nothing matches'));
    return;
  }
  host.replaceChildren(...rows.map((i) => h('div.list__row', {
    onclick: () => applyInstrument(i, ctx),
    title: 'click to load into the first zone',
  },
    h('span.mono', null, String(i.program).padStart(3, ' ')),
    h('span', null, i.name),
    h('span.list__spacer'),
    i.drums ? h('span.tag.tag--cyan', null, 'kit')
            : (i.bank ? h('span.tag', null, 'bank ' + i.bank) : null))));
}

async function applyInstrument(inst, ctx) {
  const eng = ctx.state.engine || {};
  // The old code returned silently here, which is what made this panel look broken:
  // zones is empty whenever the audio engine failed to start, so every click did
  // nothing at all with no toast, no console error and no explanation.
  if (!eng.started) {
    toast('The audio engine is not running, so there is nothing to load a sound into. '
        + 'Check Settings -> Audio output.', 'bad', 9000);
    return;
  }
  let zones = (eng.zones || []).map((z) => ({ ...z }));
  if (!zones.length) {
    zones = [{
      id: 'main', name: inst.name, lo: 21, hi: 108, channel: 0,
      soundfont: instrumentsSf, bank: inst.bank, program: inst.program, transpose: 0,
      gain: 1, pan: 0.5, reverb: 0.3, chorus: 0, curve: 'linear',
      fixed_velocity: 100, enabled: true,
    }];
  }
  zones[0].bank = inst.bank;
  zones[0].program = inst.program;
  zones[0].name = inst.name;
  if (instrumentsSf) zones[0].soundfont = instrumentsSf;
  try {
    const res = await api.post('/api/zones', { zones, name: inst.name });
    ctx.state.engine = res.engine;
    for (const w of res.warnings || []) toast(w, 'bad', 7000);
    toast(`${inst.name} -> ${zones[0].id}`, 'good', 1800);
    api.post('/api/preview', { notes: [60, 64, 67], velocity: 88, ms: 900 }).catch(() => {});
  } catch (err) { toast(err.message, 'bad'); }
}

function audition() {
  api.post('/api/preview', { notes: [48, 55, 64, 67, 72], velocity: 92, ms: 1400 })
    .catch(() => {});
}

/* The four keys that set Touch Sensitivity on a Yamaha P-45 / P-71.
 *
 * Yamaha's manual calls them A2/A#2/B2/C3, but its octave numbering runs one below
 * scientific -- the manual labels the lowest key of the 88 "A-1", which is MIDI 21.
 * So Yamaha's C3 is scientific C4, middle C, MIDI 60. Getting that wrong by an octave
 * is the single easiest way to press the wrong key and conclude the piano is broken,
 * which is exactly why this highlights them on the real keyboard instead of describing
 * them in words. */
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
    ctx.kb.clearLabels();
    ctx.kb.setLabels(labelMode);
    paintScale(ctx);
    return;
  }
  // Highlight, not ghost: the ghost layer is a muted grey that reads as "dimmed" more
  // than "look here", and the printed label sits at the bottom edge of a white key
  // where a short dock can clip it. The key itself has to carry the signal.
  scaleOn = false;
  $('#scale-toggle')?.classList.remove('is-on');
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

function paintScale(ctx) {
  if (!scaleOn) { ctx.kb.setHighlight([]); return; }
  const root = ROOT_PC[$('#scale-root').value] ?? 0;
  const steps = MODES[$('#scale-mode').value] || MODES.major;
  const pcs = new Set(steps.map((s) => (root + s) % 12));
  const notes = [];
  for (let n = 21; n <= 108; n++) if (pcs.has(n % 12)) notes.push(n);
  ctx.kb.setHighlight(notes);
}
