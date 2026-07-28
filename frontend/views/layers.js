/* Layers -- splits, layers, and the loop station.
 *
 * A SPLIT puts one sound in your left hand and another in your right. A LAYER puts two
 * sounds on the same keys so they sound together. Both are one idea: a zone is a key
 * range pointed at a channel, and overlapping two zones IS the layer. There is no
 * separate "layer mode" because there does not need to be one.
 *
 * The loop station is that idea moved along one more axis. A split stacks sounds across
 * the keys, a layer stacks them on the same key, and a recorded loop stacks them in
 * time -- so it lives here rather than in a tab of its own. Its code is in
 * ../loopstation.js; this file only mounts it.
 *
 * The tab was called "Zones" and nobody knew what that meant, including its author.
 * The concept is genuinely useful; the word was jargon.
 *
 * Each zone owns its own channel. Gain, pan and the reverb/chorus sends are per-channel
 * MIDI controllers, so two zones sharing a channel would fight over them -- the server
 * warns when that happens and this editor auto-assigns to avoid it. */

import { createLoopStation } from '../loopstation.js';
import { ctx as appCtx } from '../app.js';
import { $, $$, api, h, mod, noteName, slider, toast } from '../ui.js';

const LOW = 21, HIGH = 108;
const zoneColour = (i) => `var(--zone-${(i % 6) + 1})`;

/* Defaults lifted from presets/bass-split.json and presets/piano-strings.json rather
   than invented -- those are tuned recipes, particularly the layer's 0.42 gain and soft
   curve, which is what stops the pad swallowing the piano. */
const SPLIT_DEFAULT = { left: 32, right: 0, point: 47 };   // Acoustic Bass | Grand Piano
const LAYER_DEFAULT = { a: 0, b: 48, balance: 0.42 };      // Grand Piano + String Ensemble

let zones = [];
let instruments = [];
let station = null;

export default {
  async mount(root, ctx) {
    zones = (ctx.state.engine?.zones || []).map((z) => ({ ...z }));
    if (!zones.length) zones = [blank(0)];
    station = createLoopStation(ctx, () => instruments);

    root.append(h('div.grid', null,
      h('div.col-6', null, mod('What this is', null,
        h('div.note', null,
          'A ', h('strong', null, 'split'), ' puts one sound in your left hand and another ',
          'in your right -- bass below the split point, piano above. A ',
          h('strong', null, 'layer'), ' puts two sounds on the same keys so they sound ',
          'together -- piano and strings under one finger.'),
        h('div.note', { style: { marginTop: '8px' } },
          'Both are the same idea: a ', h('strong', null, 'zone'), ' is a range of keys ',
          'pointed at a sound, and overlapping two zones ', h('em', null, 'is'), ' the layer. ',
          'Build one below in a click, or open the editor at the bottom to move the split ',
          'point and balance the two sounds.'))),

      h('div.col-6', null, mod('Layout', 'A0 to C8 -- 88 keys',
        h('div.zonebar', { id: 'zonebar' }),
        h('div.btnrow', null,
          h('button.btn', { onclick: () => { zones.push(blank(zones.length)); render(ctx); } },
            '+ Add zone'),
          h('button.btn', { onclick: () => apply(ctx) }, 'Apply'),
          h('button.btn', { onclick: () => saveAs(ctx) }, 'Save as preset...'),
          h('button.btn', { onclick: () => { zones = [blank(0)]; render(ctx); } }, 'Reset')))),

      h('div.col-12', null, mod('Build one', 'a click each',
        h('div.builds', null,
          h('div.build', null,
            h('div.build__title', null, 'Split the keyboard'),
            h('div.build__why', null,
              'Left hand plays one sound, right hand another. Walking bass under a piano ',
              'melody, without a second keyboard.'),
            instSelect('split-left', SPLIT_DEFAULT.left, 'Left hand'),
            instSelect('split-right', SPLIT_DEFAULT.right, 'Right hand'),
            h('label.field', null,
              h('span.field__label', null, h('span', null, 'Split point'),
                h('span.field__value', { id: 'split-pt-v' }, noteName(SPLIT_DEFAULT.point))),
              slider({
                min: LOW + 6, max: HIGH - 6, step: 1, value: SPLIT_DEFAULT.point,
                oninput: (v) => {
                  $('#split-pt-v').textContent = noteName(v);
                  // Light the left hand's half on the dock, so the split point is
                  // something you see rather than a note name you decode.
                  ghost(LOW, v);
                },
              })),
            h('button.btn.btn--wide', { id: 'do-split', onclick: () => buildSplit(ctx) },
              'Make a split')),

          h('div.build', null,
            h('div.build__title', null, 'Layer two sounds'),
            h('div.build__why', null,
              'Both sounds on every key at once. The classic is piano with strings ',
              'underneath, quiet enough that you feel it more than hear it.'),
            instSelect('layer-a', LAYER_DEFAULT.a, 'Main sound'),
            instSelect('layer-b', LAYER_DEFAULT.b, 'Underneath'),
            h('label.field', null,
              h('span.field__label', null, h('span', null, 'How loud underneath'),
                h('span.field__value', { id: 'layer-bal-v' },
                  Math.round(LAYER_DEFAULT.balance * 100) + '%')),
              slider({
                min: 0, max: 1, step: 0.01, value: LAYER_DEFAULT.balance,
                oninput: (v) => { $('#layer-bal-v').textContent = Math.round(v * 100) + '%'; },
              })),
            h('button.btn.btn--wide', { id: 'do-layer', onclick: () => buildLayer(ctx) },
              'Make a layer')),

          h('div.build', null,
            h('div.build__title', null, 'One sound'),
            h('div.build__why', null,
              'The whole keyboard, one instrument, nothing clever. Where to come back to ',
              'when a split or a layer has stopped being useful.'),
            instSelect('single-inst', 0, 'Sound'),
            h('button.btn.btn--wide', { id: 'do-single', onclick: () => buildSingle(ctx) },
              'Use one sound'))))),

      
      h('div.col-12', null, station.el),

      h('div.col-12', null, mod('Zone editor', 'the long way',
        h('div', { id: 'zone-list' }))),
    ));

    try {
      instruments = (await api.get('/api/instruments')).instruments || [];
    } catch { instruments = []; }
    fillInstSelects();
    render(ctx);
    await station.init();
  },

  status(s, ctx) { station?.status(s, ctx); },

  unmount() {
    station?.destroy();
    station = null;
  },
};

/* Paint a key range on the docked keyboard while a slider sets it, then let go. */
let ghostTimer = null;
function ghost(lo, hi) {
  const kb = appCtx.kb;
  if (!kb) return;
  const keys = [];
  for (let n = Math.min(lo, hi); n <= Math.max(lo, hi); n++) keys.push(n);
  kb.setGhost(keys);
  clearTimeout(ghostTimer);
  ghostTimer = setTimeout(() => kb.setGhost([]), 1400);
}

function blank(i) {
  return {
    id: 'zone' + (i + 1), name: '', lo: LOW, hi: HIGH, channel: freeChannel(),
    soundfont: 'GeneralUser-GS.sf2', bank: 0, program: 0, transpose: 0,
    gain: 1, pan: 0.5, reverb: 0.3, chorus: 0, curve: 'linear',
    fixed_velocity: 100, enabled: true,
  };
}

/* ── the quick builders ───────────────────────────────────────────────────── */
/* Built empty and filled once /api/instruments lands, so the panel renders instantly
   instead of waiting on a 287-entry list. */
function instSelect(id, defaultProgram, label) {
  return h('label.field', null,
    h('span.field__label', null, h('span', null, label)),
    h('select', { id, 'data-default': defaultProgram },
      h('option', { value: '0' }, 'loading...')));
}

function fillInstSelects() {
  // Melodic sounds only. A drum kit in a split is a real thing, but it belongs on
  // channel 9 with an explicit bank-128 select, which is the editor's job, not a
  // one-click builder's.
  const melodic = instruments.filter((i) => !i.drums);
  for (const el of $$('.build select')) {
    const want = Number(el.dataset.default || 0);
    el.replaceChildren(...melodic.map((i) => h('option', {
      value: `${i.bank}:${i.program}`,
      selected: i.bank === 0 && i.program === want,
    }, i.name)));
  }
}

function chosen(id) {
  const el = $('#' + id);
  const [bank, program] = (el?.value || '0:0').split(':').map(Number);
  const inst = instruments.find((i) => i.bank === bank && i.program === program);
  return { bank, program, name: inst ? inst.name : `${bank}:${program}` };
}

function zoneFrom(inst, over) {
  return { ...blank(0), ...inst, ...over };
}

async function buildSplit(ctx) {
  const point = Number($('#split-pt-v').closest('.field').querySelector('input').value);
  const left = chosen('split-left');
  const right = chosen('split-right');
  zones = [
    zoneFrom(left, { id: 'left', lo: LOW, hi: point, channel: 0, gain: 0.9, reverb: 0.15 }),
    zoneFrom(right, { id: 'right', lo: point + 1, hi: HIGH, channel: 1, gain: 1.0 }),
  ];
  render(ctx);
  await apply(ctx, `Split at ${noteName(point)} -- ${left.name} / ${right.name}`);
}

async function buildLayer(ctx) {
  const balance = Number($('#layer-bal-v').closest('.field').querySelector('input').value);
  const a = chosen('layer-a');
  const b = chosen('layer-b');
  zones = [
    zoneFrom(a, { id: 'main', channel: 0, gain: 1.0, reverb: 0.25 }),
    // The soft curve is why the second sound sits under the first instead of fighting
    // it: it lifts quiet notes, so the layer is present at every dynamic without
    // spiking when you dig in.
    zoneFrom(b, { id: 'under', channel: 1, gain: balance, reverb: 0.55, curve: 'soft' }),
  ];
  render(ctx);
  await apply(ctx, `${a.name} + ${b.name} at ${Math.round(balance * 100)}%`);
}

async function buildSingle(ctx) {
  const inst = chosen('single-inst');
  zones = [zoneFrom(inst, { id: 'main', channel: 0 })];
  render(ctx);
  await apply(ctx, inst.name);
}

/* Channel 9 is the GM drum channel and 15 is reserved for the metronome click, so
   neither is handed out automatically. */
function freeChannel() {
  const used = new Set(zones.map((z) => z.channel));
  for (let c = 0; c < 15; c++) if (c !== 9 && !used.has(c)) return c;
  return 0;
}

function render(ctx) {
  drawBar();
  $('#zone-list').replaceChildren(...zones.map((z, i) => zoneCard(z, i, ctx)));
}

function drawBar() {
  const bar = $('#zonebar');
  if (!bar) return;
  const span = HIGH - LOW;
  bar.replaceChildren(...zones.map((z, i) => {
    const left = ((z.lo - LOW) / span) * 100;
    const width = ((z.hi - z.lo) / span) * 100;
    return h('div.zonebar__seg', {
      style: {
        left: left + '%', width: width + '%',
        background: zoneColour(i),
        opacity: z.enabled ? (0.55 + 0.45 / zones.length) : 0.18,
        // Stack overlapping zones so a layer reads as a stripe rather than hiding one.
        top: `${(i * 100) / zones.length}%`,
        height: `${100 / zones.length}%`,
      },
      title: `${z.id}: ${noteName(z.lo)}-${noteName(z.hi)} ch${z.channel}`,
    }, width > 8 ? (z.name || z.id) : '');
  }));
}

function zoneCard(z, i, ctx) {
  const colour = zoneColour(i);
  const set = (k, v) => { z[k] = v; drawBar(); };

  return h('div.zone', null,
    h('div.zone__head', null,
      h('span.zone__swatch', { style: { background: colour } }),
      h('input', {
        type: 'text', value: z.id, style: { width: '110px' },
        onchange: (e) => { z.id = e.target.value.trim() || 'zone' + (i + 1); },
      }),
      h('span.zone__name', { id: `zname-${i}` }, z.name || instrumentName(z)),
      h('label.toggle', null,
        h('input', {
          type: 'checkbox', checked: z.enabled,
          onchange: (e) => { set('enabled', e.target.checked); apply(ctx); },
        }),
        h('span.toggle__track'), 'On'),
      h('button.btn', {
        onclick: () => { zones.splice(i, 1); if (!zones.length) zones = [blank(0)]; render(ctx); },
      }, 'Remove')),

    h('div.zone__grid', null,
      rangeField(`Low  ${noteName(z.lo)}`, z.lo, LOW, HIGH, (v) => {
        set('lo', Math.min(v, z.hi));
        $(`#zlo-${i}`).textContent = noteName(z.lo);
      }, `zlo-${i}`, noteName(z.lo)),
      rangeField(`High ${noteName(z.hi)}`, z.hi, LOW, HIGH, (v) => {
        set('hi', Math.max(v, z.lo));
        $(`#zhi-${i}`).textContent = noteName(z.hi);
      }, `zhi-${i}`, noteName(z.hi)),

      h('label.field', null,
        h('span.field__label', null, h('span', null, 'Channel')),
        h('select', { onchange: (e) => set('channel', Number(e.target.value)) },
          Array.from({ length: 16 }, (_, c) => h('option', {
            value: c, selected: c === z.channel,
          }, c === 9 ? '9 (drums)' : c === 15 ? '15 (metronome)' : String(c))))),

      h('label.field', null,
        h('span.field__label', null, h('span', null, 'Instrument')),
        h('select', {
          onchange: (e) => {
            const inst = instruments[Number(e.target.value)];
            if (!inst) return;
            z.bank = inst.bank; z.program = inst.program; z.name = inst.name;
            $(`#zname-${i}`).textContent = inst.name;
            if (inst.drums && z.channel !== 9) toast('Drum kits usually want channel 9', '', 2600);
            drawBar();
          },
        }, instruments.map((inst, idx) => h('option', {
          value: idx,
          selected: inst.bank === z.bank && inst.program === z.program,
        }, `${inst.drums ? 'KIT ' : ''}${inst.name}`)))),

      rangeField('Transpose', z.transpose, -24, 24, (v) => {
        set('transpose', v);
        $(`#ztr-${i}`).textContent = (v > 0 ? '+' : '') + v;
      }, `ztr-${i}`, (z.transpose > 0 ? '+' : '') + z.transpose),

      pctField('Volume', z.gain, (v) => set('gain', v), `zg-${i}`),
      pctField('Pan', z.pan, (v) => set('pan', v), `zp-${i}`),
      pctField('Reverb', z.reverb, (v) => set('reverb', v), `zr-${i}`),
      pctField('Chorus', z.chorus, (v) => set('chorus', v), `zc-${i}`),

      h('label.field', null,
        h('span.field__label', null, h('span', null, 'Velocity curve')),
        h('select', { onchange: (e) => set('curve', e.target.value) },
          (ctx.state.curves || ['linear']).map((c) =>
            h('option', { value: c, selected: c === z.curve }, c))))),
  );
}

function rangeField(label, value, min, max, onchange, valueId, valueText) {
  return h('label.field', null,
    h('span.field__label', null, h('span', null, label),
      h('span.field__value', { id: valueId }, valueText)),
    slider({ min, max, step: 1, value, oninput: onchange }));
}

function pctField(label, value, onchange, id) {
  return h('label.field', null,
    h('span.field__label', null, h('span', null, label),
      h('span.field__value', { id }, Math.round(value * 100) + '%')),
    slider({
      min: 0, max: 1, step: 0.01, value,
      oninput: (v) => { $('#' + id).textContent = Math.round(v * 100) + '%'; onchange(v); },
    }));
}

function instrumentName(z) {
  const found = instruments.find((i) => i.bank === z.bank && i.program === z.program);
  return found ? found.name : `bank ${z.bank} / ${z.program}`;
}

async function apply(ctx, label) {
  try {
    const res = await api.post('/api/zones', { zones, name: label || '' });
    ctx.state.engine = res.engine;
    for (const w of res.warnings || []) toast(w, 'bad', 8000);
    if (!res.warnings?.length) toast(label || 'Applied', 'good', 2200);
    // No chord. This used to demonstrate the split by sounding each zone in its own
    // register, which on a LAYER means both instruments firing at once on the same
    // notes -- a blast you did not ask for, every time you press Apply. Same rule as
    // the instrument browser: you are sitting at a piano, so the fastest and least
    // startling way to hear what you just built is to play it.
  } catch (err) { toast(err.message, 'bad'); }
}

async function saveAs(ctx) {
  const name = prompt('Preset name?', 'My split');
  if (!name) return;
  const id = name.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  try {
    await api.post('/api/presets/save', { id, name, zones });
    await api.post(`/api/presets/${id}/load`);
    toast(`Saved presets/${id}.json`, 'good');
    await ctx.refresh();
  } catch (err) { toast(err.message, 'bad'); }
}
