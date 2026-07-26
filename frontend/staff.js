/* A grand staff, drawn in SVG.
 *
 * Hand-rolled rather than VexFlow, because the whole job is "put a notehead on the
 * right line with the right ledger lines and the right accidental", and a notation
 * engine would be a bigger dependency than the feature that needs it.
 *
 * Positions are computed in DIATONIC steps, not semitones -- that is the entire trick.
 * C#4 and C4 sit on the same line and differ only by the glyph in front of them, which
 * is exactly why the server spells notes by key signature instead of always saying C#.
 *
 * Dumb on purpose: no fetch, no app state, no ctx. It takes a spec and returns an
 * <svg>. Anything that wants a staff -- sight reading, scales, arpeggios, cadences --
 * builds the same spec and gets the same renderer, so exercise types cost no drawing
 * code at all. */

const SVG_NS = 'http://www.w3.org/2000/svg';
const LETTERS = { C: 0, D: 1, E: 2, F: 3, G: 4, A: 5, B: 6 };

/* topD = diatonic index of the top line, topY = its y in the viewBox. Treble top line
   is F5, bass top line is A3. One diatonic step is half a line gap. */
export const STAVES = {
  treble: { topD: 38, topY: 46, clef: '\u{1D11E}', clefY: 94, clefSize: 84 },
  bass: { topD: 26, topY: 158, clef: '\u{1D122}', clefY: 188, clefSize: 56 },
};
const STEP = 6;            // pixels per diatonic step
const GAP = STEP * 2;      // pixels between staff lines
const SIG_X = 96;          // first key-signature glyph, clear of the 84px treble clef
const SIG_DX = 11;         // spacing between signature glyphs
const X0 = 128;            // where notes start when there is no key signature
const XN = 700;

const GLYPH = { '#': '♯', b: '♭', n: '♮' };

function el(tag, attrs, text) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
  if (text !== undefined) node.textContent = text;
  return node;
}

/* "Eb4" -> diatonic index 30 (E is step 2, octave 4 -> 2 + 7*4) plus the accidental. */
export function parseName(name) {
  const m = /^([A-G])([#b]*)(-?\d+)$/.exec(name || 'C4');
  if (!m) return { d: 28, accidental: '' };
  const [, letter, acc, oct] = m;
  return { d: LETTERS[letter] + 7 * Number(oct), accidental: acc.slice(0, 1) };
}

/**
 * spec = {
 *   clefs: 'both' | 'treble' | 'bass',
 *   keySignature: { accidentals: ['F#', 'C#'], uses_flats: false } | null,
 *   steps: [{ notes: [{ name, staff }], fingers: [1,2], label: 'ii7' }],
 *   cursor: 0,                 // index of the step being played now
 *   active: true,              // false greys the cursor
 *   showFingers: false,
 *   empty: 'press New exercise',
 * }
 */
export function renderStaff(spec) {
  const shown = spec.clefs === 'both' || !spec.clefs
    ? ['treble', 'bass']
    : [spec.clefs];
  const svg = el('svg', {
    class: 'staff', viewBox: '0 0 760 240', preserveAspectRatio: 'xMidYMid meet',
    role: 'img', 'aria-label': 'music staff',
  });

  for (const name of shown) {
    const s = STAVES[name];
    for (let i = 0; i < 5; i++) {
      svg.append(el('line', {
        class: 'staff-line', x1: 40, x2: XN + 30,
        y1: s.topY + i * GAP, y2: s.topY + i * GAP,
      }));
    }
    svg.append(el('text', {
      class: 'clef', x: 46, y: s.clefY,
      'font-size': s.clefSize, 'font-family': '"Segoe UI Symbol","Noto Music",serif',
    }, s.clef));
  }

  // Barlines make it read as a measure rather than a row of dots.
  const top = STAVES[shown[0]].topY;
  const bottom = STAVES[shown[shown.length - 1]].topY + 4 * GAP;
  for (const x of [40, XN + 30]) {
    svg.append(el('line', { class: 'staff-line', x1: x, x2: x, y1: top, y2: bottom }));
  }

  drawKeySignature(svg, spec.keySignature, shown);

  // Notes start after the signature, not at a fixed x -- seven flats is 77px of glyphs
  // and would otherwise sit under the first notehead.
  const nAcc = (spec.keySignature?.accidentals || []).length;
  const x0 = nAcc ? Math.max(X0, SIG_X + nAcc * SIG_DX + 16) : X0;

  const steps = spec.steps || [];
  const span = Math.max(1, steps.length);
  steps.forEach((step, i) => {
    drawStep(svg, step, x0 + ((XN - x0) / span) * (i + 0.5), i, spec, shown);
  });

  if (!steps.length && spec.empty) {
    svg.append(el('text', {
      x: 380, y: 120, 'text-anchor': 'middle',
      fill: 'var(--ink-faint)', 'font-size': 14,
    }, spec.empty));
  }
  return svg;
}

/* Where each accidental of a key signature sits.
 *
 * These are CONVENTIONS, not arithmetic. Deriving them by clamping an octave into the
 * staff gets you glyphs that are roughly in the right area and wrong to a reader --
 * G# genuinely belongs ABOVE the treble staff, and a naive clamp pulls it down an
 * octave. In an app whose whole point is teaching you to read, a key signature that is
 * nearly right is worse than none.
 *
 * Values are diatonic indices (letter + 7*octave), the same units the noteheads use.
 * Treble lines bottom to top are E4 G4 B4 D5 F5; bass lines are G2 B2 D3 F3 A3. */
const SIG_POS = {
  treble: {
    'F#': 38, 'C#': 35, 'G#': 39, 'D#': 36, 'A#': 33, 'E#': 37, 'B#': 34,
    Bb: 34, Eb: 37, Ab: 33, Db: 36, Gb: 32, Cb: 35, Fb: 31,
  },
  bass: {
    'F#': 24, 'C#': 21, 'G#': 25, 'D#': 22, 'A#': 19, 'E#': 23, 'B#': 20,
    Bb: 20, Eb: 23, Ab: 19, Db: 22, Gb: 18, Cb: 21, Fb: 17,
  },
};

/* music.key_signature() already returns the accidentals in staff order -- sharps
   F C G D A E B, flats B E A D G C F -- so this only has to place them. */
function drawKeySignature(svg, sig, shown) {
  const accs = sig?.accidentals || [];
  if (!accs.length) return;
  for (const name of shown) {
    const s = STAVES[name];
    const table = SIG_POS[name];
    accs.forEach((a, i) => {
      const d = table[a];
      if (d === undefined) return;              // never guess at a position
      svg.append(el('text', {
        class: 'accidental', x: SIG_X + i * SIG_DX, y: s.topY + (s.topD - d) * STEP + 6,
        'font-size': 19, 'text-anchor': 'middle',
      }, GLYPH[a.slice(1, 2)] || GLYPH['#']));
    });
  }
}

function drawStep(svg, step, x, i, spec, shown) {
  const cursor = spec.cursor ?? 0;
  const done = i < cursor;
  const target = (spec.active ?? true) && i === cursor;
  const cls = 'notehead' + (target ? ' is-target' : done ? ' is-done' : '');

  const notes = step.notes || [];
  notes.forEach((note, j) => {
    const staffName = STAVES[note.staff] ? note.staff : shown[0];
    const s = STAVES[staffName];
    const { d, accidental } = parseName(note.name);
    const y = s.topY + (s.topD - d) * STEP;

    // Ledger lines: every other diatonic step past the outermost staff line.
    const bottomD = s.topD - 8;
    for (let dd = bottomD - 2; dd >= d; dd -= 2) ledger(svg, x, s, dd);
    for (let dd = s.topD + 2; dd <= d; dd += 2) ledger(svg, x, s, dd);

    if (accidental) {
      svg.append(el('text', {
        class: 'accidental', x: x - 20, y: y + 5, 'font-size': 22, 'text-anchor': 'middle',
      }, GLYPH[accidental] || ''));
    }

    // One id per STEP, on its first notehead, so a caller can mark a whole chord by
    // index without knowing how many notes are in it.
    svg.append(el('ellipse', {
      ...(j === 0 ? { id: 'nh-' + i } : {}),
      class: cls, cx: x, cy: y, rx: 8, ry: 6,
      transform: `rotate(-18 ${x} ${y})`,
    }));

    if (spec.showFingers && step.fingers && step.fingers[j]) {
      svg.append(el('text', {
        class: 'finger', x, y: y - 13, 'text-anchor': 'middle', 'font-size': 11,
      }, String(step.fingers[j])));
    }
  });

  if (step.label) {
    svg.append(el('text', {
      class: 'steplabel', x, y: 232, 'text-anchor': 'middle', 'font-size': 11,
    }, step.label));
  }
}

function ledger(svg, x, s, dd) {
  const y = s.topY + (s.topD - dd) * STEP;
  svg.append(el('line', { class: 'ledger', x1: x - 13, x2: x + 13, y1: y, y2: y }));
}

/** Mark one step's notehead. cls is 'is-target' | 'is-done' | 'is-wrong'. */
export function markStep(svg, i, cls) {
  const head = svg?.querySelector?.('#nh-' + i) || document.getElementById('nh-' + i);
  if (head) head.classList.add(cls);
}
