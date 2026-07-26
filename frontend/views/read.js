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

import { $, api, h, mod, stat, toast } from '../ui.js';

const LETTERS = { C: 0, D: 1, E: 2, F: 3, G: 4, A: 5, B: 6 };

/* topD  = diatonic index of the top line, topY = its y in the viewBox.
   Treble top line is F5, bass top line is A3. One diatonic step is half a line gap. */
const STAVES = {
  treble: { topD: 38, topY: 46, clef: '\u{1D11E}', clefY: 94, clefSize: 84 },
  bass:   { topD: 26, topY: 158, clef: '\u{1D122}', clefY: 188, clefSize: 56 },
};
const STEP = 6;            // pixels per diatonic step
const GAP = STEP * 2;      // pixels between staff lines
const X0 = 96;             // where notes start, right of the clefs
const XN = 700;

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

      h('div.col-7', null, mod('Setup', null,
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
  $('#sr-lo-v').textContent = midiName(cfg.low ?? 55);
  $('#sr-hi-v').textContent = midiName(cfg.high ?? 79);
  $('#sr-adaptive').checked = cfg.adaptive !== false;

  $('#staff-host').replaceChildren(renderStaff(state));
  renderStats(state);
  paintGhost(ctx);
}

async function newExercise(ctx) {
  try {
    state = await api.post('/api/sightread/new');
    $('#staff-host').replaceChildren(renderStaff(state));
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
  $('#sr-lo-v').textContent = midiName(patch.low);
  $('#sr-hi-v').textContent = midiName(patch.high);
  try { state = await api.post('/api/sightread/config', patch); } catch (err) { toast(err.message, 'bad'); }
}

function paintGhost(ctx) {
  if (!showOnKeys || !state?.active) { ctx.kb.setGhost([]); return; }
  const target = state.notes?.[state.index]?.midi;
  ctx.kb.setGhost(target != null ? [target] : []);
}

/* ── the staff ────────────────────────────────────────────────────────────── */
function renderStaff(st) {
  const svg = el('svg', {
    class: 'staff', viewBox: '0 0 760 240', preserveAspectRatio: 'xMidYMid meet',
    role: 'img', 'aria-label': 'sight reading staff',
  });
  const clef = st.config?.clef ?? 'both';
  const shown = clef === 'both' ? ['treble', 'bass'] : [clef];

  for (const name of shown) {
    const s = STAVES[name];
    for (let i = 0; i < 5; i++) {
      svg.append(el('line', {
        class: 'staff-line', x1: 40, x2: XN + 30,
        y1: s.topY + i * GAP, y2: s.topY + i * GAP,
      }));
    }
    const glyph = el('text', {
      class: 'clef', x: 46, y: s.clefY,
      'font-size': s.clefSize, 'font-family': '"Segoe UI Symbol","Noto Music",serif',
    });
    glyph.textContent = s.clef;
    svg.append(glyph);
  }
  // Barlines make it read as a measure rather than a row of dots.
  for (const x of [40, XN + 30]) {
    const top = STAVES[shown[0]].topY;
    const bottom = STAVES[shown[shown.length - 1]].topY + 4 * GAP;
    svg.append(el('line', { class: 'staff-line', x1: x, x2: x, y1: top, y2: bottom }));
  }

  const notes = st.notes || [];
  const span = Math.max(1, notes.length);
  notes.forEach((n, i) => {
    const x = X0 + ((XN - X0) / span) * (i + 0.5);
    drawNote(svg, n, x, i, st);
  });

  if (!notes.length) {
    const t = el('text', { x: 380, y: 120, 'text-anchor': 'middle', fill: 'var(--ink-faint)', 'font-size': 14 });
    t.textContent = 'press New exercise';
    svg.append(t);
  }
  return svg;
}

function drawNote(svg, note, x, i, st) {
  const staffName = STAVES[note.staff] ? note.staff : 'treble';
  const s = STAVES[staffName];
  const { d, accidental } = parseName(note.name);
  const y = s.topY + (s.topD - d) * STEP;

  // Ledger lines: every other diatonic step past the outermost staff line.
  const bottomD = s.topD - 8;
  for (let dd = bottomD - 2; dd >= d; dd -= 2) {
    svg.append(el('line', {
      class: 'ledger', x1: x - 13, x2: x + 13,
      y1: s.topY + (s.topD - dd) * STEP, y2: s.topY + (s.topD - dd) * STEP,
    }));
  }
  for (let dd = s.topD + 2; dd <= d; dd += 2) {
    svg.append(el('line', {
      class: 'ledger', x1: x - 13, x2: x + 13,
      y1: s.topY + (s.topD - dd) * STEP, y2: s.topY + (s.topD - dd) * STEP,
    }));
  }

  if (accidental) {
    const a = el('text', {
      class: 'accidental', x: x - 30, y: y + 5, 'font-size': 22, 'text-anchor': 'middle',
    });
    a.textContent = accidental === '#' ? '♯' : accidental === 'b' ? '♭' : '♮';
    svg.append(a);
  }

  const done = i < (st.index ?? 0);
  const target = st.active && i === (st.index ?? 0);
  const head = el('ellipse', {
    id: 'nh-' + i,
    class: 'notehead' + (target ? ' is-target' : done ? ' is-done' : ''),
    cx: x, cy: y, rx: 8, ry: 6,
    transform: `rotate(-18 ${x} ${y})`,
  });
  svg.append(head);
}

/* "Eb4" -> diatonic index 30 (E is step 2, octave 4 -> 2 + 7*4) plus the accidental. */
function parseName(name) {
  const m = /^([A-G])([#b]*)(-?\d+)$/.exec(name || 'C4');
  if (!m) return { d: 28, accidental: '' };
  const [, letter, acc, oct] = m;
  return { d: LETTERS[letter] + 7 * Number(oct), accidental: acc.slice(0, 1) };
}

function el(tag, attrs) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

const NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
const midiName = (n) => `${NAMES[n % 12]}${Math.floor(n / 12) - 1}`;

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
    h('span.mono', null, w.name || midiName(w.note)),
    h('span.list__spacer'),
    h('span.mono', null, `${w.correct}/${w.attempts}`),
    h('div.bar', { style: { width: '120px' } },
      h('div.bar__fill', { style: { width: Math.round((w.accuracy || 0) * 100) + '%' } })),
    h('span.mono', null, Math.round((w.accuracy || 0) * 100) + '%'),
    w.mean_reaction_ms ? h('span.tag', null, Math.round(w.mean_reaction_ms) + 'ms') : null,
  )) : [h('div.empty', null, 'play a few exercises and your weak spots show up here')]));
}
