/* Layers -- splits, layers and drum pads.
 *
 * A SPLIT puts one sound in your left hand and another in your right. A LAYER puts two
 * sounds on the same keys so they sound together. Both are one idea: a zone is a key
 * range pointed at a channel, and overlapping two zones IS the layer. There is no
 * separate "layer mode" because there does not need to be one.
 *
 * The tab was called "Zones" and nobody knew what that meant, including its author.
 * The concept is genuinely useful; the word was jargon.
 *
 * Each zone owns its own channel. Gain, pan and the reverb/chorus sends are per-channel
 * MIDI controllers, so two zones sharing a channel would fight over them -- the server
 * warns when that happens and this editor auto-assigns to avoid it. */

import { $, $$, api, h, mod, noteName, slider, toast } from '../ui.js';

const ZONE_COLOURS = ['#ffa62b', '#58c4d4', '#74cf86', '#c98bdb', '#ff8f6b', '#8fa5ff'];
const LOW = 21, HIGH = 108;

let zones = [];
let instruments = [];

export default {
  async mount(root, ctx) {
    zones = (ctx.state.engine?.zones || []).map((z) => ({ ...z }));
    if (!zones.length) zones = [blank(0)];

    root.append(h('div.grid', null,
      h('div.col-12', null, mod('Layout', 'A0 to C8 -- 88 keys',
        h('div.zonebar', { id: 'zonebar' }),
        h('div.btnrow', null,
          h('button.btn', { onclick: () => { zones.push(blank(zones.length)); render(ctx); } },
            '+ Add zone'),
          h('button.btn', { onclick: () => apply(ctx) }, 'Apply'),
          h('button.btn', { onclick: () => saveAs(ctx) }, 'Save as preset...'),
          h('button.btn', { onclick: () => { zones = [blank(0)]; render(ctx); } }, 'Reset')))),
      h('div.col-12', null, mod('Zones', null, h('div', { id: 'zone-list' }))),
    ));

    try {
      instruments = (await api.get('/api/instruments')).instruments || [];
    } catch { instruments = []; }
    render(ctx);
  },

  unmount(ctx) { },
};

function blank(i) {
  return {
    id: 'zone' + (i + 1), name: '', lo: LOW, hi: HIGH, channel: freeChannel(),
    soundfont: 'GeneralUser-GS.sf2', bank: 0, program: 0, transpose: 0,
    gain: 1, pan: 0.5, reverb: 0.3, chorus: 0, curve: 'linear',
    fixed_velocity: 100, enabled: true,
  };
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
        background: ZONE_COLOURS[i % ZONE_COLOURS.length],
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
  const colour = ZONE_COLOURS[i % ZONE_COLOURS.length];
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

      pctField('Gain', z.gain, (v) => set('gain', v), `zg-${i}`),
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

async function apply(ctx) {
  try {
    const res = await api.post('/api/zones', { zones });
    ctx.state.engine = res.engine;
    for (const w of res.warnings || []) toast(w, 'bad', 8000);
    if (!res.warnings?.length) toast('Zones applied', 'good', 1500);
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
