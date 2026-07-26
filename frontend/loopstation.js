/* The loop station -- record a part, then play against it, until there is a band.
 *
 * Lives in the Layers tab because it is the same idea as a split or a layer, moved
 * along one axis: a split stacks sounds across the keys, a layer stacks them on the
 * same key, and this stacks them in time.
 *
 * Its own module rather than more of views/layers.js, for the same reason staff.js is
 * its own module: the zone editor and the transport share a tab and nothing else.
 *
 * The playhead is animated locally. Position arrives once a second with the status
 * frame; interpolating between those against loop_ms is the difference between a
 * transport and a progress bar, and it costs one rAF and no traffic.
 */

import { $, api, h, mod, noteName, slider, toast } from './ui.js';

const LOW = 21, HIGH = 108;
const BAR_CHOICES = [1, 2, 4, 8, 16];

export function createLoopStation(ctx, getInstruments) {
  let raf = null;
  let last = { position: 0, loop_ms: 0, at: 0, running: false };
  let state = null;
  let saved = [];
  // null, not '' -- an empty layer list signs as '', so starting at '' would count as
  // "already drawn" and the first paint would skip the empty state entirely.
  let signature = null;      // what the layer list was last built from

  const el = mod('Loop station', 'an ensemble of one',
    h('div.note', null,
      'Record a few bars, and they start looping. Play something else over the top and ',
      'record that too -- bass, then chords, then a melody -- until you are playing with ',
      'a band that is entirely you. Each layer keeps the sound you recorded it with, so ',
      'pick your instrument in ', h('strong', null, 'Play'), ' first.'),
    h('div.note', { style: { marginTop: '8px' } },
      'Takes are locked to the bar. You get a count-in, the recording starts on the ',
      'downbeat and ends itself at the end of the loop -- so a take is never 40 ms too ',
      'long, which is what makes a hand-stopped loop drift by the fourth pass.'),

    h('div.loop', null,
      h('div.loop__transport', null,
        h('button.btn.loop__rec', { id: 'loop-rec', onclick: () => act('record') },
          h('span.loop__dot'), h('span', { id: 'loop-rec-label' }, 'Record a layer')),
        h('button.btn.btn--lg', { id: 'loop-play', onclick: () => act(running() ? 'stop' : 'start') },
          'Play'),
        h('div.loop__state', null,
          h('div.loop__state-main', { id: 'loop-state' }, 'stopped'),
          h('div.loop__state-sub', { id: 'loop-sub' }, '')),
        h('div.loop__setup', null,
          h('label.field', null,
            h('span.field__label', null, h('span', null, 'Length')),
            h('select', { id: 'loop-bars', onchange: (e) => conf({ bars: Number(e.target.value) }) },
              BAR_CHOICES.map((b) => h('option', { value: b }, b === 1 ? '1 bar' : `${b} bars`)))),
          h('label.field', null,
            h('span.field__label', null, h('span', null, 'Count-in')),
            h('select', {
              id: 'loop-countin',
              onchange: (e) => conf({ count_in_bars: Number(e.target.value) }),
            }, [0, 1, 2].map((b) => h('option', { value: b },
              b === 0 ? 'none' : b === 1 ? '1 bar' : `${b} bars`)))),
          h('label.toggle', null,
            h('input', {
              type: 'checkbox', id: 'loop-click',
              onchange: (e) => conf({ click: e.target.checked }),
            }),
            h('span.toggle__track'), 'Click'))),

      h('div.loop__track', { id: 'loop-track' },
        h('div.loop__grid', { id: 'loop-grid' }),
        h('div.loop__head', { id: 'loop-head' })),

      h('div.note.note--warn', { id: 'loop-desync', style: { display: 'none' } },
        h('strong', null, 'The tempo moved under the loop.'),
        ' A recorded layer is a fixed number of milliseconds, so it no longer lines up ',
        'with the click. Stop and start the transport to re-lock at the new tempo.')),

    h('div', { id: 'loop-layers' }),

    h('div.btnrow', { style: { marginTop: '12px' } },
      h('input', {
        type: 'text', id: 'loop-name', placeholder: 'name this loop',
        style: { width: '160px' },
      }),
      h('button.btn', { onclick: save }, 'Save'),
      h('select', { id: 'loop-saved', onchange: load }),
      h('button.btn', { id: 'loop-clear', onclick: () => act('clear') }, 'Clear all')),
    h('div.note', { style: { marginTop: '8px' } },
      'Saved loops keep their tempo, and land in ', h('strong', null, 'recordings/'),
      ' as plain JSON. Five layers is the ceiling: there are sixteen MIDI channels, the ',
      'click owns one, and your live zones need the rest.'));

  const running = () => state && state.state !== 'stopped';

  async function act(action) {
    try {
      apply(await api.post(`/api/loop/${action}`, {}));
      if (state?.error) toast(state.error, 'bad', 6000);
    } catch (err) { toast(err.message, 'bad'); }
  }

  async function conf(patch) {
    try { apply(await api.post('/api/loop/config', patch)); }
    catch (err) { toast(err.message, 'bad'); }
  }

  async function save() {
    const name = $('#loop-name').value.trim();
    if (!name) { toast('Give the loop a name first', 'bad'); return; }
    if (!state?.layers?.length) { toast('Nothing to save yet', 'bad'); return; }
    try {
      apply(await api.post('/api/loop/save', { name }));
      toast(state?.error || `Saved as ${name}`, state?.error ? 'bad' : 'good');
    } catch (err) { toast(err.message, 'bad'); }
  }

  async function load(e) {
    const name = e.target.value;
    if (!name) return;
    try {
      apply(await api.post('/api/loop/load', { name }));
      toast(state?.error || `Loaded ${name}`, state?.error ? 'bad' : 'good');
      $('#loop-name').value = name;
    } catch (err) { toast(err.message, 'bad'); }
  }

  function apply(res) {
    if (!res) return;
    if (res.loop) state = res.loop;
    if (res.saved) saved = res.saved;
    paint();
  }

  /* ── layer rows ─────────────────────────────────────────────────────────── */
  /* Rebuilt only when the set of layers actually changes. A layer row owns a live
     slider and a text input; blowing it away every second would fight the user's
     hands mid-drag. */
  function layerRows() {
    const layers = state?.layers || [];
    const sig = layers.map((l) => `${l.id}:${l.notes}`).join('|');
    if (sig === signature) {
      for (const l of layers) {
        const row = document.getElementById('lay-' + l.id);
        if (row) row.classList.toggle('is-muted', l.muted);
      }
      return;
    }
    signature = sig;

    const host = $('#loop-layers');
    if (!layers.length) {
      host.replaceChildren(h('div.empty', null,
        'no layers yet -- hit Record and play something'));
      return;
    }
    host.replaceChildren(...layers.map((l, i) => layerRow(l, i)));
  }

  function layerRow(l, i) {
    const patch = (body) => api.post(`/api/loop/layer/${l.id}`, body)
      .then(apply).catch((err) => toast(err.message, 'bad'));
    const instruments = getInstruments?.() || [];

    return h('div.layerrow', { id: 'lay-' + l.id, class: l.muted ? 'is-muted' : '' },
      h('div.layerrow__head', null,
        h('span.layerrow__n', null, String(i + 1)),
        h('input.layerrow__name', {
          type: 'text', value: l.name,
          onchange: (e) => patch({ name: e.target.value }),
        }),
        h('span.tag', null, `ch${l.channel}`),
        h('span.tag.tag--cyan', null, `${l.notes} notes`),
        h('span.list__spacer'),
        h('button.btn', {
          class: l.muted ? 'is-on' : '',
          onclick: () => patch({ muted: !l.muted }),
        }, l.muted ? 'Muted' : 'Mute'),
        h('button.btn', { onclick: () => del(l.id) }, 'Delete')),

      h('div.layerrow__roll', null, roll(l)),

      h('div.layerrow__ctl', null,
        instruments.length
          ? h('label.field', null,
              h('span.field__label', null, h('span', null, 'Sound')),
              h('select', {
                onchange: (e) => {
                  const inst = instruments[Number(e.target.value)];
                  if (inst) patch({ bank: inst.bank, program: inst.program, name: inst.name });
                },
              }, instruments.map((inst, idx) => h('option', {
                value: idx,
                selected: inst.bank === l.bank && inst.program === l.program,
              }, `${inst.drums ? 'KIT ' : ''}${inst.name}`))))
          : null,
        h('label.field', null,
          h('span.field__label', null, h('span', null, 'Level'),
            h('span.field__value', { id: `lg-${l.id}` }, Math.round(l.gain * 100) + '%')),
          slider({
            min: 0, max: 1, step: 0.01, value: l.gain,
            oninput: (v) => { $(`#lg-${l.id}`).textContent = Math.round(v * 100) + '%'; },
            onchange: (v) => patch({ gain: v }),
          })),
        h('label.field', null,
          h('span.field__label', null, h('span', null, 'Pan'),
            h('span.field__value', { id: `lp-${l.id}` }, panLabel(l.pan))),
          slider({
            min: 0, max: 1, step: 0.01, value: l.pan,
            oninput: (v) => { $(`#lp-${l.id}`).textContent = panLabel(v); },
            onchange: (v) => patch({ pan: v }),
          }))));
  }

  async function del(id) {
    try { apply(await api.del(`/api/loop/layer/${id}`)); }
    catch (err) { toast(err.message, 'bad'); }
  }

  /* A layer at a glance: where its notes sit in the bar, how long they are, how hard.
     Spans, not a canvas -- a few hundred absolutely positioned elements is nothing, and
     they inherit the panel's colours for free.

     The vertical range is shared across every layer rather than fitted per layer, so a
     bass part visibly sits below a chord part. Fitting each strip to its own notes
     would draw them at identical heights and quietly throw that away. */
  function pitchRange() {
    let lo = 127, hi = 0;
    for (const l of state?.layers || []) {
      for (const [, key] of l.marks || []) {
        if (key < lo) lo = key;
        if (key > hi) hi = key;
      }
    }
    if (hi < lo) return [60, 72];
    const pad = Math.max(2, Math.round((hi - lo) * 0.12));
    return [Math.max(LOW, lo - pad), Math.min(HIGH, hi + pad)];
  }

  function roll(l) {
    const [lo, hi] = pitchRange();
    const span = Math.max(1, hi - lo);
    const total = state?.loop_ms || 1;
    return (l.marks || []).map(([pos, key, vel, dur]) => h('i.layerrow__note', {
      style: {
        left: (pos / total) * 100 + '%',
        // A floor, so a staccato note is still a visible mark rather than a hairline.
        width: `max(3px, ${Math.min(100, ((dur || 120) / total) * 100)}%)`,
        // 3..93% rather than 0..100%: the note has height, and a top note pinned at
        // 100% would hang half out of the strip.
        bottom: (3 + ((key - lo) / span) * 90) + '%',
        opacity: String(0.4 + (vel / 127) * 0.6),
      },
      title: `${noteName(key)} v${vel}`,
    }));
  }

  /* ── paint ──────────────────────────────────────────────────────────────── */
  function paint() {
    if (!state) return;
    const st = state.state;
    const isRunning = st !== 'stopped';

    const rec = $('#loop-rec');
    rec.classList.toggle('is-armed', !!state.armed && st !== 'recording');
    rec.classList.toggle('is-recording', st === 'recording');
    $('#loop-rec-label').textContent =
      st === 'recording' ? 'Recording' : state.armed ? 'Waiting for the bar' : 'Record a layer';

    $('#loop-play').textContent = isRunning ? 'Stop' : 'Play';
    $('#loop-play').classList.toggle('is-on', isRunning);

    $('#loop-state').textContent =
      st === 'counting' ? 'count-in' : st === 'recording' ? 'recording' : st;
    const bars = state.bars || 4;
    $('#loop-sub').textContent = isRunning
      ? `${bars} bar${bars > 1 ? 's' : ''} · ${Math.round(state.loop_ms)} ms · pass ${(state.cycle ?? 0) + 1}`
      : `${bars} bar${bars > 1 ? 's' : ''} at ${Math.round(60000 / (state.bar_ms / state.beats_per_bar))} bpm`;

    const barsSel = $('#loop-bars');
    barsSel.value = String(BAR_CHOICES.includes(bars) ? bars : 4);
    // Length is fixed once there is material -- every recorded layer is that many bars
    // long, so changing it mid-flight would leave them all the wrong shape.
    barsSel.disabled = isRunning;
    $('#loop-countin').value = String(state.count_in_bars ?? 1);
    $('#loop-countin').disabled = isRunning;
    $('#loop-click').checked = !!state.click;
    $('#loop-click').disabled = isRunning;
    $('#loop-clear').disabled = !(state.layers || []).length;
    $('#loop-desync').style.display = state.desynced ? '' : 'none';

    drawGrid();
    layerRows();

    const sel = $('#loop-saved');
    const names = saved.map((s) => s.name).join('|');
    if (sel.dataset.names !== names) {
      sel.dataset.names = names;
      sel.replaceChildren(
        h('option', { value: '' }, saved.length ? 'load a loop...' : 'nothing saved yet'),
        ...saved.map((s) => h('option', { value: s.name },
          `${s.name} -- ${s.layers} layers, ${s.bars} bars, ${Math.round(s.bpm)} bpm`)));
    }
  }

  /* Bar lines, so four bars looks like four bars rather than like a progress bar. */
  function drawGrid() {
    const grid = $('#loop-grid');
    const bars = state?.bars || 4;
    const beats = state?.beats_per_bar || 4;
    if (grid.dataset.shape === `${bars}x${beats}`) return;
    grid.dataset.shape = `${bars}x${beats}`;
    const ticks = [];
    for (let i = 0; i < bars * beats; i++) {
      ticks.push(h('i', {
        class: i % beats === 0 ? 'loop__tick loop__tick--bar' : 'loop__tick',
        style: { left: (i / (bars * beats)) * 100 + '%' },
      }));
    }
    grid.replaceChildren(...ticks);
  }

  /* ── live playhead ──────────────────────────────────────────────────────── */
  function tick() {
    raf = requestAnimationFrame(tick);
    const head = document.getElementById('loop-head');
    if (!head) return;
    if (!last.running || !last.loop_ms) { head.style.opacity = '0'; return; }
    head.style.opacity = '1';
    const elapsed = performance.now() - last.at;
    const pos = (last.position + elapsed / last.loop_ms) % 1;
    head.style.left = (pos * 100).toFixed(3) + '%';
  }

  return {
    el,

    async init() {
      try { apply(await api.get('/api/loop')); }
      catch { /* engine down; the panel still renders its explanation */ }
      raf = requestAnimationFrame(tick);
    },

    status(s) {
      if (!s.loop) return;
      state = s.loop;
      last = {
        position: s.loop.position || 0,
        loop_ms: s.loop.loop_ms || 0,
        at: performance.now(),
        // A count-in has no playhead to show yet: position is pinned at 0 until the
        // loop's own bar 1 arrives, and a head parked at the left edge reads as broken.
        running: s.loop.state === 'playing' || s.loop.state === 'recording',
      };
      paint();
    },

    destroy() {
      if (raf) cancelAnimationFrame(raf);
      raf = null;
    },
  };
}

function panLabel(v) {
  const n = Math.round((v - 0.5) * 200);
  return n === 0 ? 'centre' : n < 0 ? `L${-n}` : `R${n}`;
}
