/* Play -- the view you are actually looking at while playing.
 *
 * Everything here has to be reachable without taking your hands off the keys for
 * long: the instrument browser filters as you type, a saved profile is one click, and
 * the scale highlighter paints straight onto the dock keyboard.
 *
 * Touch response and Pedal left for Tools -- they calibrate the instrument, which is a
 * thing you do once, not a thing you reach for mid-phrase. */

import { $, api, h, knob, mod, paint, slider, stat, toast } from '../ui.js';
import { instrument } from '../app.js';
import { createLoopStation } from '../loopstation.js';

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
let family = '';          // '' = every family
let scaleOn = false;
/* Which shelf the Instruments panel is showing. Module-scoped so leaving Play and
   coming back does not throw away the choice, the same reason `filter` is. */
let shelf = 'instruments';
let station = null;
// The metronome's last known config and beat. Module-scoped so the -10/+10 buttons
// and the lamp painter agree without re-reading the status feed.
let cfg = {};
let lastBeat = -1;

export default {
  async mount(root, ctx) {
    const st = ctx.state;
    const eng = st.engine || {};
    const s = st.settings || {};
    // The sends are per-channel, so they are read off a zone rather than the config.
    // The first enabled one is the instrument you are playing; with the engine down
    // there is none, and the sliders seed from the shipped values instead.
    const zone = (eng.zones || []).find((z) => z.enabled);
    const m = st.metronome || {};
    cfg = { ...(m.config || {}) };
    // Yours first, and only when there are any -- a first run sees exactly what it
    // always saw plus one heading. `saved` is stamped by the backend when it writes
    // the file, which is the only test that works the same in a source checkout and
    // in the packaged app.
    const mine = (st.presets || []).filter((p) => p.saved);
    const shipped = (st.presets || []).filter((p) => !p.saved);
    station = createLoopStation(ctx, () => instruments);

    root.append(h('div.grid', null,
      h('div.col-6', null, mod('Instruments', null,
        // Two shelves, one at a time. Stacked, they made this panel two scrollers tall
        // and neither of them a comfortable height -- the odd shape that made Play's
        // first row awkward in the first place. They are also two different questions:
        // "what sound do I want" is browsing 287 of them, "load the rig I built" is
        // picking one of a handful. Instruments is the default because that is the one
        // you open this panel for.
        h('div.btnrow', { style: { marginBottom: '10px' } },
          h('button.btn', {
            id: 'shelf-instruments',
            class: shelf === 'instruments' ? 'is-on' : '',
            onclick: () => setShelf('instruments'),
          }, 'Instruments'),
          h('button.btn', {
            id: 'shelf-profiles',
            class: shelf === 'profiles' ? 'is-on' : '',
            onclick: () => setShelf('profiles'),
          }, `Profiles (${mine.length + shipped.length})`)),

        h('div', { id: 'shelf-inst', hidden: shelf !== 'instruments' },
          h('div', { style: { display: 'flex', gap: '8px', marginBottom: '10px' } },
            h('input', {
              type: 'text', placeholder: 'filter -- rhodes, organ, kit...',
              // Seeded from the module-scoped filter so the box and the list can never
              // disagree: leaving Play and coming back used to show an empty-looking
              // input that was still filtering.
              value: filter, style: { flex: '1' },
              oninput: (e) => { filter = e.target.value.toLowerCase(); renderList(ctx); },
            }),
            // 287 instruments is enough to browse instead of play. This picks one.
            h('button.btn', { onclick: () => randomInstrument(ctx), title: 'surprise me' },
              'Random'),
            h('button.btn', { onclick: () => audition(ctx) }, 'Audition')),
          // 287 sounds in one flat list is a list nobody reads. GM groups its programs
          // into families of eight and every bank is a variation on the same numbers,
          // so the whole SoundFont sorts itself with no table of names to maintain.
          h('div.chips.chips--tight', { id: 'fam-chips' }),
          h('div.scroller', null, h('div.list', { id: 'inst-list' },
            h('div.empty', null, 'loading...')))),

        h('div', { id: 'shelf-prof', hidden: shelf !== 'profiles' },
          // A profile is a sound plus its zones plus the two FX units, and the 62 that
          // ship with Keys are the read-only half of the same shelf -- so they are one
          // list with a heading, not two. `saved` is stamped by the backend when it
          // writes the file, the only shipped-vs-yours test that works the same in a
          // source checkout and in the packaged app.
          h('div.scroller', null,
            h('div.list', { id: 'profile-list' },
              ...mine.map((pr) => profileRow(pr, eng.preset_id, ctx)),
              ...shipped.map((pr) => profileRow(pr, eng.preset_id, ctx)))),
          h('div.btnrow', { style: { marginTop: '10px' } },
            h('input', {
              type: 'text', id: 'preset-name', placeholder: 'name this sound',
              style: { flex: '1', minWidth: '140px' },
              onkeydown: (e) => { if (e.key === 'Enter') savePreset(ctx); },
            }),
            h('button.btn', { onclick: () => savePreset(ctx) }, 'Save profile'))))),

      // col-6, and authored second on purpose. Instruments is the tallest panel in the
      // app (a 287-row scroller plus the profile shelf) and Effects was col-12, so it
      // could not fit beside it -- half of a 1456px row sat empty under the one panel
      // you always look at first. At col-6 the FX_GRID auto-fit reflows to four columns
      // and the two pack side by side, which took the tab from 2198px to 1593px.
      h('div.col-6', null, mod('Effects', 'the two units FluidSynth has',
        h('div.chips__head', null, 'Reverb -- the room'),
        h('div', { style: FX_GRID },
          fxKnobs('reverb', s, ctx),
          fxField('fx-send-reverb', 'Reverb send', 0, 1, 0.01, zone?.reverb ?? 0.3,
                  pushSend('reverb', ctx))),
        h('div.chips__head', null, 'Chorus -- movement and thickness'),
        h('div', { style: FX_GRID },
          fxKnobs('chorus', s, ctx),
          h('label.field', null,
            h('span.field__label', null, h('span', null, 'Shape')),
            h('select', {
              id: 'fx-chorus-type',
              onchange: (e) => api.post('/api/settings', { chorus: { type: Number(e.target.value) } })
                .then((st) => { ctx.state.settings = st; })   // see fxKnobs
                .catch((err) => toast(err.message, 'bad')),
            },
              h('option', { value: '0', selected: (s.chorus?.type ?? 0) === 0 }, 'Sine'),
              h('option', { value: '1', selected: (s.chorus?.type ?? 0) === 1 }, 'Triangle'))),
          fxField('fx-send-chorus', 'Chorus send', 0, 1, 0.01, zone?.chorus ?? 0,
                  pushSend('chorus', ctx))),
        h('div.btnrow', null,
          h('button.btn', { onclick: () => resetFx() }, 'Reset both units')),
        h('div.note', { style: { marginTop: '12px' } },
          'Both units are global -- they colour every zone at once -- and a ',
          h('strong', null, 'send'), ' is how much of what you are playing goes into one. ',
          'The sends belong to the instrument rather than to the room, so they travel ',
          'with it: pick another preset and you get that preset\'s sends, and Reset ',
          'leaves them alone. ',
          'FluidSynth has exactly two effect units, so there is no tone or brightness ',
          'knob to offer: the CCs that would drive one render byte-identical at 0 and 127.'))),

      // Third, not last. Alone at the bottom it was a 152px panel with 721px of dead
      // space beside it; here it fills the gap under Effects instead.
      h('div.col-6', null, mod('Scale highlighter', null,
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

      h('div.col-6', null, mod('Tempo', null,
        h('div', { style: { display: 'flex', alignItems: 'baseline' } },
          h('span.bpm', { id: 'bpm-display' }, String(cfg.bpm ?? 80)),
          h('span.bpm__unit', null, 'BPM')),
        slider({
          // Declared here rather than stamped on afterwards by walking the DOM from
          // #bpm-display. That walk depended on three things nobody wrote down --
          // that the display stays the slider's previous sibling, that the panel is
          // in the document when mount() finishes, and that the id is set before the
          // -10/+10 buttons can be pressed -- and it silently no-ops rather than
          // throwing when any of them stops being true.
          id: 'bpm-slider',
          min: 30, max: 240, step: 1, value: cfg.bpm ?? 80,
          oninput: (v) => { $('#bpm-display').textContent = v; },
          onchange: (v) => push({ bpm: v }),
        }),
        h('div.btnrow', { style: { marginTop: '6px' } },
          [-10, -5, -1, +1, +5, +10].map((d) => h('button.btn', {
            onclick: () => {
              const next = Math.max(30, Math.min(240, (cfg.bpm ?? 80) + d));
              $('#bpm-display').textContent = next;
              $('#bpm-slider').value = next;
              push({ bpm: next });
            },
          }, (d > 0 ? '+' : '') + d))),
        h('div.btnrow', { style: { marginTop: '12px' } },
          h('button.btn.btn--lg', { id: 'metro-toggle', onclick: toggle }, 'Start'),
          h('button.btn', { onclick: () => api.post('/api/metronome/setback') }, 'Drop a step')))),

      h('div.col-6', null, mod('Meter', null,
        h('div.beats', { id: 'beat-lamps' }),
        h('div', { style: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px', marginTop: '16px' } },
          h('label.field', null,
            h('span.field__label', null, h('span', null, 'Beats per bar')),
            h('select', {
              onchange: (e) => push({ beats_per_bar: Number(e.target.value) }),
              value: String(cfg.beats_per_bar ?? 4),
            }, [2, 3, 4, 5, 6, 7, 9, 12].map((n) =>
              h('option', { value: n, selected: n === (cfg.beats_per_bar ?? 4) }, String(n))))),
          h('label.field', null,
            h('span.field__label', null, h('span', null, 'Subdivision')),
            h('select', {
              onchange: (e) => push({ subdivision: Number(e.target.value) }),
            }, [[1, 'quarter'], [2, 'eighths'], [3, 'triplets'], [4, 'sixteenths']].map(([n, label]) =>
              h('option', { value: n, selected: n === (cfg.subdivision ?? 1) }, label))))),
        h('div.stats', { id: 'metro-stats', style: { marginTop: '4px' } }))),

      h('div.col-6', null, mod('Tempo ramp', 'gets faster as you go',
        h('div', { style: { marginBottom: '10px' } },
          h('label.toggle', null,
            h('input', {
              type: 'checkbox', checked: !!cfg.ramp_enabled,
              onchange: (e) => push({ ramp_enabled: e.target.checked }),
            }),
            h('span.toggle__track'), 'Climb automatically')),
        numField('Every (bars)', 'ramp_bars', 1, 32),
        numField('Faster by (bpm)', 'ramp_bpm_step', 1, 20),
        numField('Stop at (bpm)', 'ramp_bpm_max', 40, 240),
        h('div.note', { style: { marginTop: '10px' } },
          'Start slower than feels necessary and let it climb. ',
          h('strong', null, 'Drop a step'), ' when you miss -- that is the loop that ',
          'builds speed, not playing fast badly.'))),

      // "Click kit", not "Sound". layout.js keys a panel's saved position on the slug
      // of its rendered title, and Play's master-volume panel slugged to `sound` too --
      // so moving this one into Play while deleting that one would silently hand this
      // panel the deleted one's saved slot AND its col-3 width. Under 1100px col-3
      // collapses to span 6, so the same bug would have looked different per window
      // size. The cost is that this panel loses its own saved slot once.
      h('div.col-6', null, mod('Click kit', null,
        h('label.field', null,
          h('span.field__label', null, h('span', null, 'Kit')),
          h('select', { id: 'kit-select', onchange: (e) => push({ kit: Number(e.target.value) }) },
            h('option', { value: 0 }, 'loading...'))),
        h('div', { style: { display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '10px' } },
          gainField('Downbeat', 'accent_velocity'),
          gainField('Beat', 'beat_velocity'),
          gainField('Subdivision', 'sub_velocity')),
        h('div.note', { style: { marginTop: '10px' } },
          'The click has its own MIDI channel, so a drum zone can never steal it.'))),

      h('div.col-12', null, station.el),

    ));

    // Seeded from state rather than left at the markup's defaults, so the panel does
    // not show "Damper / A0-C8" for a second before the first status frame corrects it.

    const root0 = ctx.keySignature();
    if (ROOT_PC[root0] !== undefined) $('#scale-root').value = root0;

    try {
      const res = await api.get('/api/instruments');
      instruments = res.instruments || [];
      // Carry the SoundFont these bank/program numbers came from. Without it, loading
      // an instrument into a zone pointing at a different SF2 silently falls back to
      // Grand Piano, because that bank/program need not exist over there.
      instrumentsSf = res.soundfont || '';
      renderFamilies(ctx);
      renderList(ctx);
    } catch (err) {
      $('#inst-list').replaceChildren(h('div.empty', null, 'could not load: ' + err.message));
    }

    renderBeats(cfg.beats_per_bar ?? 4);
    loadKits();

    // AFTER the /api/instruments fetch above, and not beside createLoopStation. The
    // station memoises its layer rows on the layer ids alone, and drops the per-layer
    // Sound picker entirely when getInstruments() comes back empty -- so initialising
    // it first meant that reloading the page with a loop already recorded produced
    // rows with no instrument dropdown, which then stayed that way until the layer set
    // itself changed.
    await station.init();
  },

  status(s, ctx) {
    paintMetronome(s);
    station?.status(s, ctx);
    if (!s.engine) return;
    // Each block guards itself, and the early return above tests s.engine rather than a
    // DOM node. It used to be gated on #play-stats, an id owned only by the Sound panel,
    // so deleting that one panel would have silently taken the profile sync and
    // paintPedal with it -- and paintPedal is the only thing reflecting HARDWARE pedal
    // down/up, so it would have looked half-alive rather than dead.
    //
    // Keeps the shelf honest when the profile changed from another tab or the API. An
    // empty preset_id means nothing saved is loaded, so every row goes dark.
    const list = $('#profile-list');
    if (list) {
      for (const r of list.querySelectorAll('.list__row')) {
        r.classList.toggle('is-active', r.dataset.id === s.engine.preset_id);
      }
    }
  },

  unmount() {
    lastBeat = -1;
    scaleOn = false;
    // destroy() holds the only cancelAnimationFrame in loopstation.js, and its tick()
    // re-arms the next frame BEFORE its own empty-loop guard -- so without this the
    // rAF chain outlives the view, pinning ctx and a detached subtree, once per
    // navigation away and back.
    station?.destroy();
    station = null;
  },
};

let labelMode = 'c-only';

/* ── pedal ────────────────────────────────────────────────────────────────── */
/* Guarded against paint-after-unmount: status() fires once a second and can arrive
   after the router has replaced the stage. */
const FX_KNOBS = [
  ['reverb', 'room',    'Room',       0,   1,   0.01, 0.3],
  ['reverb', 'damping', 'Damping',    0,   1,   0.01, 0.4],
  ['reverb', 'width',   'Width',      0,   100, 1,    6],
  ['reverb', 'level',   'Room level', 0,   1,   0.01, 0.55],
  ['chorus', 'level',   'Chorus',     0,   10,  0.1,  1.2],
  ['chorus', 'nr',      'Voices',     0,   20,  1,    3],
  // 0.1, not the 0.29 these shipped with: FluidSynth's own warning names 0.100000 as
  // the floor, so the old minimum threw away a third of the travel for nothing.
  ['chorus', 'speed',   'Speed',      0.1, 5,   0.01, 0.4],
  ['chorus', 'depth',   'Depth',      0,   21,  0.1,  6],
];

const FX_GRID = {
  display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: '0 12px',
};

function fxKnobs(group, s, ctx) {
  return FX_KNOBS.filter(([g]) => g === group).map(([g, key, label, min, max, step, dflt]) =>
    fxField(`fx-${g}-${key}`, label, min, max, step, s[g]?.[key] ?? dflt,
            (v) => api.post('/api/settings', { [g]: { [key]: v } })
              // POST /api/settings returns the whole merged snapshot and this used to
              // throw it away, leaving ctx.state.settings on its boot values for the
              // rest of the session. Profiles read the two FX units out of there, so a
              // profile saved after moving a reverb slider would have stored the sound
              // you had when the app opened.
              .then((st) => { ctx.state.settings = st; })
              .catch((err) => toast(err.message, 'bad'))));
}

/* One knob. The post is on onchange and never on oninput, which is not a style
   preference: writing a setting rewrites the whole config file, 1.60 ms a go, on the
   same asyncio loop that drains notes at 60 Hz. Once when you let go of the slider is
   free; sixty a second is 96 ms of that loop every second, and the note display is
   what pays for it. oninput moves the readout and nothing else. */
function fxField(id, label, min, max, step, value, push) {
  // 0..1 is a mix amount and gets a dial; the rest of this panel is unit parameters in
  // their own units -- width 0-100, chorus voices 0-20, speed in Hz, depth in ms -- and
  // a dial would be lying about the scale. So the panel is deliberately mixed: five
  // dials and five tracks, split on what the number MEANS rather than on tidiness.
  const control = (min === 0 && max === 1) ? knob : slider;
  return h('label.field', null,
    h('span.field__label', null, h('span', null, label),
      h('span.field__value', { id }, String(value))),
    control({
      min, max, step, value,
      oninput: (v) => { $('#' + id).textContent = String(v); },
      onchange: push,
    }));
}

/* The one control here that is not global: a send is CC91/CC93 on every enabled zone's
   channel, so the engine answers with the zones it just rewrote and we take them. */
function pushSend(kind, ctx) {
  return (v) => api.post('/api/fx/send', { [kind]: v })
    .then((res) => { ctx.state.engine = res.engine; })
    .catch((err) => toast(err.message, 'bad'));
}

/* Presets no longer carry a room, so this is the only way back to the sound Keys
   shipped with -- and an effects panel you cannot undo is one you play with once. One
   post for both units, then the sliders are moved to match rather than re-read. */
async function resetFx() {
  const body = { reverb: {}, chorus: { type: 0 } };
  for (const [group, key, , , , , dflt] of FX_KNOBS) body[group][key] = dflt;
  try {
    await api.post('/api/settings', body);
  } catch (err) { toast(err.message, 'bad'); return; }
  for (const [group, key, , , , , dflt] of FX_KNOBS) {
    const readout = $(`#fx-${group}-${key}`);
    readout.textContent = String(dflt);
    const input = readout.closest('.field').querySelector('input');
    input.value = dflt;
    paint(input);
  }
  $('#fx-chorus-type').value = '0';
  toast('Reverb and chorus back to the shipped sound', 'good', 2200);
}

/* Too many options and you browse instead of play. This is the cure for that. */
async function randomInstrument(ctx) {
  // Melodic only. Landing on a drum kit when you wanted "a sound" reads as a bug, and
  // a kit needs channel 9 and a bank-128 select to be right anyway.
  const pool = instruments.filter((i) => !i.drums);
  if (!pool.length) { toast('No instruments loaded yet', 'bad'); return;
  }
  const pick = pool[Math.floor(Math.random() * pool.length)];
  await applyInstrument(pick, ctx);
  toast(`${pick.name} -- play something`, 'good', 2600);
  // Show it, so Random is a way to discover the list rather than a black box.
  filter = '';
  const box = $('#inst-list')?.closest('.mod')?.querySelector('input[type=text]');
  if (box) box.value = '';
  renderList(ctx);
  const row = [...($('#inst-list')?.children || [])]
    .find((el) => el.textContent.includes(pick.name));
  row?.scrollIntoView({ block: 'center' });
  row?.classList.add('is-on');
}

/* Save whatever is loaded right now as a preset chip. Presets used to be read-only
   files you could pick from and never add to, which made the panel a menu rather than
   a place you keep things. */
async function savePreset(ctx) {
  const input = $('#preset-name');
  const name = (input?.value || '').trim();
  if (!name) { toast('Give the sound a name first', 'bad'); return; }
  const zones = ctx.state.engine?.zones || [];
  if (!zones.length) { toast('Nothing loaded to save', 'bad'); return; }
  const id = name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  if (!id) { toast('That name has no letters or numbers in it', 'bad'); return; }
  try {
    // The two global units ride along, so a profile restores the sound you saved and
    // not just the keys it was mapped to. Read from ctx.state.settings, which is only
    // trustworthy because every FX writer now stores the merged snapshot it gets back.
    const st = ctx.state.settings || {};
    const effects = { reverb: st.reverb || {}, chorus: st.chorus || {} };
    await api.post('/api/presets/save', { id, name, zones, effects });
    await api.post(`/api/presets/${id}/load`);
    input.value = '';
    toast(`Saved "${name}"`, 'good');
    await ctx.refresh();      // rebuild the chips so the new one is there
  } catch (err) { toast(err.message, 'bad'); }
}

/* One row on the profile shelf. Yours get Rename and a delete; the shipped 62 get
   neither, because a shipped preset lives in the application bundle, is reinstated by
   the next update anyway, and is not ours to remove.

   Rename has no endpoint and does not need one: the id is the file name, so a rename
   that keeps the id is just a re-save with a new display name. Renaming the FILE is
   what would need the collision guard the save route just grew. */
/* Swap the two shelves. Both subtrees stay in the DOM -- renderList, renderFamilies
   and the status() profile sync all address them by id and would otherwise have to
   learn which half is mounted. Hiding is cheaper than teaching four call sites. */
function setShelf(which) {
  shelf = which;
  $('#shelf-inst')?.toggleAttribute('hidden', which !== 'instruments');
  $('#shelf-prof')?.toggleAttribute('hidden', which !== 'profiles');
  $('#shelf-instruments')?.classList.toggle('is-on', which === 'instruments');
  $('#shelf-profiles')?.classList.toggle('is-on', which === 'profiles');
}

function profileRow(preset, activeId, ctx) {
  const load = async (e) => {
    try {
      const res = await api.post(`/api/presets/${preset.id}/load`);
      for (const r of document.querySelectorAll('#profile-list .list__row')) {
        r.classList.remove('is-active');
      }
      e.target.closest('.list__row')?.classList.add('is-active');
      for (const w of res.warnings || []) toast(w, 'bad', 7000);
      ctx.state.engine = res.engine;
      // The load route applies the profile's saved effects to the settings file, and
      // returns only `engine` -- so without this ctx.state.settings still holds the
      // reverb from before the load, and the next Save profile would bake THAT in.
      if (Object.keys(preset.effects || {}).length) await ctx.refresh();
    } catch (err) { toast(err.message, 'bad'); }
  };
  return h('div.list__row', {
    'data-id': preset.id,
    class: preset.id === activeId ? 'is-active' : '',
    title: preset.description || preset.name,
  },
    // .list__row has no overflow handling of its own, so a long name would push the
    // buttons off the end of the panel -- same guard library.js uses on song titles.
    h('span', {
      style: { flex: '1', minWidth: 0, overflow: 'hidden',
               textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
    }, preset.name),
    preset.zones.length > 1 ? h('span.tag.tag--cyan', null, preset.zones.length + 'Z') : null,
    // Only a profile you saved carries the units, so this is also the badge for which
    // rows can rewrite your reverb when you load them.
    Object.keys(preset.effects || {}).length
      ? h('span.tag.tag--amber', { title: 'carries reverb and chorus' }, 'fx')
      : null,
    h('button.btn', { title: 'Load this profile', onclick: load }, 'Load'),
    preset.saved
      ? h('button.btn', { title: 'Rename', onclick: () => renameProfile(preset, ctx) }, 'Rename')
      : null,
    preset.saved
      ? h('button.btn', { title: 'Delete this profile',
                          onclick: () => deleteProfile(preset, ctx) }, '×')
      : null);
}

async function renameProfile(preset, ctx) {
  const name = (window.prompt('Rename this profile', preset.name) || '').trim();
  if (!name || name === preset.name) return;
  try {
    // Same id, new name -- the file keeps its name and only the label changes. The
    // zones and effects are re-sent from the stored preset rather than from the live
    // engine, so renaming cannot quietly capture whatever you happen to be playing.
    await api.post('/api/presets/save', {
      id: preset.id, name, description: preset.description,
      zones: preset.zones, effects: preset.effects || {},
    });
    toast(`Renamed to "${name}"`, 'good');
    await ctx.refresh();
  } catch (err) { toast(err.message, 'bad'); }
}

async function deleteProfile(preset, ctx) {
  if (!window.confirm(`Delete the profile "${preset.name}"?`)) return;
  try {
    await api.del(`/api/presets/${preset.id}`);
    toast(`Deleted "${preset.name}"`, 'good');
    await ctx.refresh();
  } catch (err) { toast(err.message, 'bad'); }
}

function renderFamilies(ctx) {
  const host = $('#fam-chips');
  if (!host) return;
  const counts = new Map();
  for (const i of instruments) counts.set(i.family, (counts.get(i.family) || 0) + 1);
  const names = [...counts.keys()].sort();
  host.replaceChildren(
    famChip('', `All ${instruments.length}`, ctx),
    ...names.map((n) => famChip(n, `${n} ${counts.get(n)}`, ctx)));
}

function famChip(value, label, ctx) {
  return h('button.chip', {
    class: value === family ? 'is-active' : '',
    onclick: () => {
      family = value;
      renderFamilies(ctx);
      renderList(ctx);
    },
  }, label);
}

function renderList(ctx) {
  const host = $('#inst-list');
  if (!host) return;
  const rows = instruments
    .filter((i) => !family || i.family === family)
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
      id: 'main', name: inst.name, lo: instrument().low, hi: instrument().high, channel: 0,
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
    // No chord. Browsing 287 instruments used to fire a fortissimo triad on every
    // click, which is startling rather than informative -- you are at a piano, and
    // the fastest way to hear a sound is to play it. Audition is still one button
    // away for when your hands are on the mouse.
  } catch (err) { toast(err.message, 'bad'); }
}

/* Deliberate, so it may make a noise -- but a soft one. 92 was most of the way to
   fortissimo for something you did not ask to be loud. */
function audition() {
  api.post('/api/preview', { notes: [48, 55, 64, 67, 72], velocity: 58, ms: 1400 })
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
function paintScale(ctx) {
  if (!scaleOn) { ctx.kb.setHighlight([]); return; }
  const root = ROOT_PC[$('#scale-root').value] ?? 0;
  const steps = MODES[$('#scale-mode').value] || MODES.major;
  const pcs = new Set(steps.map((s) => (root + s) % 12));
  const notes = [];
  const { low, high } = instrument();
  for (let n = low; n <= high; n++) if (pcs.has(n % 12)) notes.push(n);
  ctx.kb.setHighlight(notes);
}

function renderBeats(n) {
  const lamps = $('#beat-lamps');
  if (!lamps) return;
  lamps.replaceChildren(...Array.from({ length: n }, (_, i) =>
    h('div.beat' + (i === 0 ? '.is-down' : ''))));
}

function numField(label, key, min, max) {
  return h('label.field', null,
    h('span.field__label', null, h('span', null, label),
      h('span.field__value', { id: 'v-' + key }, String(cfg[key] ?? min))),
    slider({
      min, max, step: 1, value: cfg[key] ?? min,
      oninput: (v) => { $('#v-' + key).textContent = v; },
      onchange: (v) => push({ [key]: v }),
    }));
}

function gainField(label, key) {
  return h('label.field', null,
    h('span.field__label', null, h('span', null, label),
      h('span.field__value', { id: 'v-' + key }, String(cfg[key] ?? 90))),
    slider({
      min: 1, max: 127, step: 1, value: cfg[key] ?? 90,
      oninput: (v) => { $('#v-' + key).textContent = v; },
      onchange: (v) => push({ [key]: v }),
    }));
}

async function push(patch) {
  cfg = { ...cfg, ...patch };
  try {
    await api.post('/api/metronome/config', patch);
  } catch (err) { toast(err.message, 'bad'); }
}

async function toggle() {
  try {
    const res = await api.post('/api/metronome/toggle');
    const btn = $('#metro-toggle');
    btn.textContent = res.metronome.running ? 'Stop' : 'Start';
    btn.classList.toggle('is-on', res.metronome.running);
  } catch (err) { toast(err.message, 'bad'); }
}

async function loadKits() {
  try {
    const res = await api.get('/api/instruments');
    const kits = (res.instruments || []).filter((i) => i.drums);
    const sel = $('#kit-select');
    if (!sel) return;
    sel.replaceChildren(...kits.map((k) =>
      h('option', { value: k.program, selected: k.program === (cfg.kit ?? 0) }, k.name)));
  } catch { /* the metronome still clicks on the default kit */ }
}

/* The metronome half of the status feed. Split out of status() rather than inlined
   because it must run even when the engine is down -- the click is scheduled on the
   sequencer, and a view that returns early on !s.engine would freeze the lamps. */
function paintMetronome(s) {
  const m = s.metronome;
  if (!m) return;
  cfg = { ...m.config };
  const btn = $('#metro-toggle');
  if (btn) {
    btn.textContent = m.running ? 'Stop' : 'Start';
    btn.classList.toggle('is-on', m.running);
  }
  const lamps = $('#beat-lamps');
  if (lamps && lamps.children.length !== (m.config.beats_per_bar || 4)) {
    renderBeats(m.config.beats_per_bar || 4);
  }
  if (lamps && m.running && m.beat !== lastBeat) {
    lastBeat = m.beat;
    for (let i = 0; i < lamps.children.length; i++) {
      lamps.children[i].classList.toggle('is-on', i === m.beat);
    }
  } else if (lamps && !m.running) {
    for (const el of lamps.children) el.classList.remove('is-on');
    lastBeat = -1;
  }
  const host = $('#metro-stats');
  if (host) {
    host.replaceChildren(
      stat(Math.round(m.effective_bpm), 'Playing at',
           m.config.ramp_enabled ? `+${m.ramp_steps} step(s)` : 'no ramp', 'stat__value--amber'),
      stat(m.running ? m.bar + 1 : '--', 'Bar'),
      stat(m.clock_samples, 'Clock samples'));
  }
}
