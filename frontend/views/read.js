/* Sight reading.
 *
 * A hand-rolled SVG grand staff. No VexFlow, because the only thing needed here is
 * "put a notehead on the right line with the right ledger lines and accidental", and
 * pulling in a notation engine to do that would be a bigger dependency than the
 * feature.
 *
 * Positions are computed in DIATONIC steps, not semitones -- that is the whole trick.
 * C#4 and C4 sit on the same line and differ only by the accidental glyph, which is
 * exactly why the server spells notes by key signature instead of always saying "C#". */

import { $, api, h, mod, noteName, stat, toast } from '../ui.js';
import { renderStaff } from '../staff.js';


let state = null;
let showOnKeys = false;

export default {
  async mount(root, ctx) {
    root.append(h('div.grid', null,
      h('div.col-12', null, mod('Staff', null,
        h('div', { id: 'staff-host' }),
        h('div.btnrow', { style: { marginTop: '12px' } },
          h('button.btn.btn--lg', { id: 'new-ex', onclick: () => newExercise(ctx) },
            'New exercise'),
          h('button.btn', { onclick: () => api.post('/api/sightread/stop').then(() => paint(ctx)) },
            'Stop'),
          h('button.btn', {
            id: 'show-keys',
            onclick: (e) => {
              showOnKeys = !showOnKeys;
              e.target.classList.toggle('is-on', showOnKeys);
              paintGhost(ctx);
            },
          }, 'Show on keys')))),

      h('div.col-5', null, mod('This run', null,
        h('div.stats', { id: 'run-stats' }, h('div.empty', null, 'no attempts yet')))),

      h('div.col-7', null, mod('Exercise setup', null,
        h('div', { style: { display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '10px' } },
          h('label.field', null,
            h('span.field__label', null, h('span', null, 'Key')),
            h('select', { id: 'sr-key', onchange: pushCfg },
              (ctx.state.keys || ['C']).map((k) => h('option', { value: k }, k)))),
          h('label.field', null,
            h('span.field__label', null, h('span', null, 'Clef')),
            h('select', { id: 'sr-clef', onchange: pushCfg },
              ['both', 'treble', 'bass'].map((c) => h('option', { value: c }, c)))),
          h('label.field', null,
            h('span.field__label', null, h('span', null, 'Notes')),
            h('select', { id: 'sr-count', onchange: pushCfg },
              [1, 2, 3, 4, 6, 8].map((n) => h('option', { value: n }, String(n)))))),
        h('div', { style: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' } },
          h('label.field', null,
            h('span.field__label', null, h('span', null, 'Lowest'),
              h('span.field__value', { id: 'sr-lo-v' }, '')),
            h('input', { id: 'sr-lo', type: 'range', min: 21, max: 108, onchange: pushCfg })),
          h('label.field', null,
            h('span.field__label', null, h('span', null, 'Highest'),
              h('span.field__value', { id: 'sr-hi-v' }, '')),
            h('input', { id: 'sr-hi', type: 'range', min: 21, max: 108, onchange: pushCfg }))),
        h('label.toggle', { style: { marginTop: '4px' } },
          h('input', { id: 'sr-adaptive', type: 'checkbox', onchange: pushCfg }),
          h('span.toggle__track'), 'Weight toward my worst notes'))),

      h('div.col-12', null, mod('Weakest notes', 'from your own attempt history',
        h('div.list', { id: 'weak-list' }, h('div.empty', null, 'not enough attempts yet')))),
    ));

    await paint(ctx);
  },

  frame(f, ctx) {
    if (!f.sight) return;
    const fb = f.sight;
    if (state) {
      // Mark the notehead immediately -- waiting for the next status frame would put
      // a visible lag between the key going down and the staff acknowledging it.
      const head = $(`#nh-${fb.index}`);
      if (head) head.classList.add(fb.correct ? 'is-done' : 'is-wrong');
      if (fb.correct) {
        state.index = fb.index + 1;
        const next = $(`#nh-${state.index}`);
        if (next) next.classList.add('is-target');
        paintGhost(ctx);
      }
    }
    if (fb.complete) {
      toast('Measure complete', 'good', 1400);
      setTimeout(() => newExercise(ctx), 600);
    }
  },

  unmount(ctx) { showOnKeys = false; },
};

async function paint(ctx) {
  state = await api.get('/api/sightread');
  const cfg = state.config || {};
  $('#sr-key').value = cfg.key ?? 'C';
  $('#sr-clef').value = cfg.clef ?? 'both';
  $('#sr-count').value = String(cfg.notes_per_measure ?? 4);
  $('#sr-lo').value = cfg.low ?? 55;
  $('#sr-hi').value = cfg.high ?? 79;
  $('#sr-lo-v').textContent = noteName(cfg.low ?? 55);
  $('#sr-hi-v').textContent = noteName(cfg.high ?? 79);
  $('#sr-adaptive').checked = cfg.adaptive !== false;

  $('#staff-host').replaceChildren(staffFor(state));
  renderStats(state);
  paintGhost(ctx);
}

async function newExercise(ctx) {
  try {
    state = await api.post('/api/sightread/new');
    $('#staff-host').replaceChildren(staffFor(state));
    renderStats(state);
    paintGhost(ctx);
  } catch (err) { toast(err.message, 'bad'); }
}

async function pushCfg() {
  const patch = {
    key: $('#sr-key').value,
    clef: $('#sr-clef').value,
    notes_per_measure: Number($('#sr-count').value),
    low: Number($('#sr-lo').value),
    high: Number($('#sr-hi').value),
    adaptive: $('#sr-adaptive').checked,
  };
  if (patch.low > patch.high) [patch.low, patch.high] = [patch.high, patch.low];
  $('#sr-lo-v').textContent = noteName(patch.low);
  $('#sr-hi-v').textContent = noteName(patch.high);
  try { state = await api.post('/api/sightread/config', patch); } catch (err) { toast(err.message, 'bad'); }
}

function paintGhost(ctx) {
  if (!showOnKeys || !state?.active) { ctx.kb.setGhost([]); return; }
  const target = state.notes?.[state.index]?.midi;
  ctx.kb.setGhost(target != null ? [target] : []);
}

/* The sight-reading payload is one note per step -- the degenerate case of the shared
   staff spec, where a step is a chord. */
function staffFor(st) {
  return renderStaff({
    clefs: st.config?.clef ?? 'both',
    keySignature: st.key_signature,
    steps: (st.notes || []).map((n) => ({ notes: [{ name: n.name, staff: n.staff }] })),
    cursor: st.index ?? 0,
    active: !!st.active,
    empty: 'press New exercise',
  });
}

/* ── stats ────────────────────────────────────────────────────────────────── */
function renderStats(st) {
  const host = $('#run-stats');
  const hist = st.history || {};
  host.replaceChildren(
    stat(st.run_accuracy != null ? Math.round(st.run_accuracy * 100) + '%' : '--',
         'This run', `${st.run_correct}/${st.run_total}`, 'stat__value--amber'),
    stat(hist.accuracy != null ? Math.round(hist.accuracy * 100) + '%' : '--',
         'Last 30 days', `${hist.attempts || 0} attempts`),
    stat(hist.mean_reaction_ms ? Math.round(hist.mean_reaction_ms) + 'ms' : '--',
         'Reaction', 'to find the key', 'stat__value--cyan'));

  const weak = st.weak_notes || [];
  $('#weak-list').replaceChildren(...(weak.length ? weak.map((w) => h('div.list__row', null,
    h('span.mono', null, w.name || noteName(w.note)),
    h('span.list__spacer'),
    h('span.mono', null, `${w.correct}/${w.attempts}`),
    h('div.bar', { style: { width: '120px' } },
      h('div.bar__fill', { style: { width: Math.round((w.accuracy || 0) * 100) + '%' } })),
    h('span.mono', null, Math.round((w.accuracy || 0) * 100) + '%'),
    w.mean_reaction_ms ? h('span.tag', null, Math.round(w.mean_reaction_ms) + 'ms') : null,
  )) : [h('div.empty', null, 'play a few exercises and your weak spots show up here')]));
}
