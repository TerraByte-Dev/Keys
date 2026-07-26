/* Play -- the view you are actually looking at while playing.
 *
 * Everything here has to be reachable without taking your hands off the keys for
 * long: presets are one click, the instrument browser filters as you type, and the
 * scale highlighter paints straight onto the dock keyboard. */

import { $, api, h, mod, slider, stat, toast } from '../ui.js';
import { ctx as appCtx, resetTouch } from '../app.js';

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
let filter = '';
let scaleOn = false;

export default {
  async mount(root, ctx) {
    const st = ctx.state;
    const eng = st.engine || {};

    root.append(h('div.grid', null,
      h('div.col-7', null, mod('Presets', `${(st.presets || []).length} on file`,
        h('div.chips', { id: 'preset-chips' },
          (st.presets || []).map((p) => chip(p, eng.preset_id, ctx))),
        h('div.note', { style: { marginTop: '12px' } },
          'Overlap is the layering mechanism. A preset with two zones over the same keys ',
          'sounds both -- see ', h('strong', null, 'Zones'), ' to build your own.'))),

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
            style: { flex: '1' },
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
          }, 'Labels: c-only')),
        h('div.note', { style: { marginTop: '12px' } },
          'Highlighted keys are the scale. They do not change what sounds -- ',
          'this is a reading aid, not a lock.'))),
    ));

    const root0 = ctx.keySignature();
    if (ROOT_PC[root0] !== undefined) $('#scale-root').value = root0;

    try {
      const res = await api.get('/api/instruments');
      instruments = res.instruments || [];
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
    const chips = $('#preset-chips');
    if (chips) {
      for (const c of chips.children) {
        c.classList.toggle('is-active', c.dataset.id === s.engine.preset_id);
      }
    }
  },

  unmount() { scaleOn = false; showTouchKeys = false; },
};

let labelMode = 'c-only';

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
  const zones = (ctx.state.engine?.zones || []).map((z) => ({ ...z }));
  if (!zones.length) return;
  zones[0].bank = inst.bank;
  zones[0].program = inst.program;
  zones[0].name = inst.name;
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
      document.createTextNode(' Velocity curves in Zones will do something now.'));
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
