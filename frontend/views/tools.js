/* Tools -- things you run while you play.
 *
 * The metronome and the backing-track shelf; a tuner, chord finder, scale reference and
 * transposer belong here too. Tools lives in its own tab rather than as a peer of Play
 * and Practice because one tool does not deserve a top-level slot, and because there
 * was nowhere for the second one to go.
 *
 * The clicks are scheduled on FluidSynth's sequencer, driven by the audio render
 * thread. Nothing here times anything -- this view sends configuration and draws the
 * beat lamps from the status feed. `m` toggles it from any tab. */

import { createBacking } from '../backing.js';
import { $, api, h, mod, slider, stat, toast } from '../ui.js';

let cfg = {};
let lastBeat = -1;
let backing = null;

export default {
  async mount(root, ctx) {
    const m = ctx.state.metronome || {};
    cfg = { ...(m.config || {}) };
    backing = createBacking();

    root.append(h('div.grid', null,
      h('div.col-6', null, mod('Tempo', null,
        h('div', { style: { display: 'flex', alignItems: 'baseline' } },
          h('span.bpm', { id: 'bpm-display' }, String(cfg.bpm ?? 80)),
          h('span.bpm__unit', null, 'BPM')),
        slider({
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

      h('div.col-6', null, mod('Sound', null,
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

      h('div.col-12', null, backing.el),
    ));

    renderBeats(cfg.beats_per_bar ?? 4);
    $('#bpm-display').parentElement.nextElementSibling.id = 'bpm-slider';
    loadKits();
    await backing.init();
  },

  status(s) {
    backing?.status(s);
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
  },

  unmount() {
    lastBeat = -1;
    backing?.destroy();
    backing = null;
  },
};

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
