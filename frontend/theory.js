/* Chords & scales -- the shape you are about to play, and how to find it.
 *
 * The ribbon is the point. Every other piano app shows you which keys light up;
 * this one shows the GAPS, because the gaps are the part you can carry to a key
 * you have not memorised. A major scale is W W H W W W H everywhere, and a major
 * chord is four keys then three everywhere, and knowing that beats knowing that
 * C major is C D E F G A B.
 *
 * So: the numbers between the notes are large, the note names are normal, and the
 * intervals from the root -- how the shape is NAMED rather than how it is found --
 * are small and last.
 *
 * Spelling and fingering both come from the server. The frontend does not own a
 * mode table; there is one in music.py and a second one here would drift. */

import { createKeyboard } from './keyboard.js';
import { $, api, h, mod, toast } from './ui.js';

const LOW = 60, HIGH = 84;          // two octaves from middle C, plus the closing note

export function createTheory() {
  let kb = null;                    // the panel's own small keyboard
  let vocab = null;
  let plan = null;
  let dock = null;                  // the 88-key dock, so you can see it where you play
  let kind = 'scale';
  const pick = { root: 'C', mode: 'major', quality: '', octaves: 1, hand: 'R', inversion: 0 };

  const el = h('div.col-12', null, mod('Chords & scales', 'count the gaps, not the notes',
    h('div.th__top', null,
      h('div.seg', { id: 'th-kind' },
        h('button.seg__btn.is-on', { onclick: () => setKind('scale') }, 'Scale'),
        h('button.seg__btn', { onclick: () => setKind('chord') }, 'Chord')),
      h('div.th__roots', { id: 'th-roots' }),
      h('select.th__shape', { id: 'th-shape', onchange: onShape }),
      h('div.th__opts', { id: 'th-opts' })),

    /* Keyboard beside the ribbon, not above it: the shape and the counting are two
       readings of one thing, and a wide panel has room for both. Wraps back to
       stacked when the panel is dragged narrow. */
    h('div.th__main', null,
      h('div.th__board', { id: 'th-board' }),
      h('div.th__right', null,
        h('div.th__fingers', { id: 'th-fingers' }),
        h('div.th__ribbon', { id: 'th-ribbon' }))),

    h('div.th__foot', null,
      h('button.btn.btn--lg', { id: 'th-hear' }, 'Hear it'),
      h('span.th__from', { id: 'th-from' }),
      h('span.list__spacer'),
      h('span.th__title', { id: 'th-title' }))));

  const chordsEl = h('div.col-12', null, mod('Chords in this key', 'everything that fits',
    h('div.th__chords', { id: 'th-chords' })));

  /* ---- data ---- */
  async function refresh() {
    try {
      plan = kind === 'scale'
        ? await api.get(`/api/theory/scale?key=${enc(pick.root)}&mode=${pick.mode}`
                        + `&octaves=${pick.octaves}&hand=${pick.hand}`)
        : await api.get(`/api/theory/chord?root=${enc(pick.root)}&quality=${enc(pick.quality)}`
                        + `&inversion=${pick.inversion}`);
      draw();
    } catch (err) { toast(err.message, 'bad'); }
  }

  const enc = encodeURIComponent;

  /* ---- drawing ---- */
  function draw() {
    if (!plan) return;
    $('#th-title').textContent = plan.kind === 'chord'
      ? plan.spoken + (plan.inversion ? ` · ${ordinal(plan.inversion)} inversion` : '')
      : plan.title;

    kb?.setHighlight(plan.midi);
    // The same shape on the keyboard you actually play, so the jump from this
    // panel to the piano is not a jump.
    dock?.setHighlight(plan.midi);

    buildRoots();      // the lit chip follows what came back, not what was clicked
    drawRibbon();
    drawFingers();
    drawFrom();
    drawOpts();
    if (plan.kind === 'scale') drawChords();
    chordsEl.style.display = plan.kind === 'scale' && plan.chords?.length ? '' : 'none';
  }

  /* The ribbon: note, gap, note, gap. The gap is the big number. */
  function drawRibbon() {
    const host = $('#th-ribbon');
    if (!host) return;
    const names = plan.names;
    const steps = plan.steps;
    const parts = [];
    const fingers = plan.fingers || [];
    for (let i = 0; i < names.length; i++) {
      parts.push(h('div.th__note' + (i === 0 ? '.is-root' : ''), null,
        // The finger sits with its note rather than on a row of its own. A
        // separate row cannot stay lined up once the scale runs two octaves.
        fingers.length
          ? h('span.th__finger' + (plan.crossings?.[i] ? '.is-cross' : ''),
              { title: plan.crossings?.[i] ? 'thumb crosses here' : '' },
              String(fingers[i] ?? ''))
          : h('span.th__finger.is-none'),
        h('span.th__nname', null, names[i]),
        h('span.th__ndeg', null, plan.kind === 'scale'
          ? (plan.degrees?.[i] ?? '')
          : (plan.from_root?.[i]?.short ?? ''))));
      const gap = steps[i];
      if (gap) {
        parts.push(h('div.th__gap' + (gap.semitones === 1 ? '.is-half' : ''), null,
          h('span.th__gnum', null, String(gap.semitones)),
          h('span.th__gword', null, plan.kind === 'scale'
            ? gap.short
            : (gap.short || ''))));
      }
    }
    host.replaceChildren(...parts);
  }

  /* Just the caption -- the digits themselves live on the notes. */
  function drawFingers() {
    const host = $('#th-fingers');
    if (!host) return;
    if (plan.kind !== 'scale') { host.replaceChildren(); return; }
    host.replaceChildren(plan.fingers?.length
      ? h('span.th__flabel', null,
          `${pick.hand === 'R' ? 'Right' : 'Left'} hand — amber is a thumb crossing`)
      : h('span.th__nofinger', null,
          'No standard fingering for this one. Major and the three minors have it.'));
  }

  function drawFrom() {
    const host = $('#th-from');
    if (!host) return;
    if (plan.kind === 'chord') {
      host.textContent = plan.from_root.map((f) => f.short).join(' · ');
      return;
    }
    // The signature only where it is not a trap. Harmonic and melodic minor are
    // WRITTEN with the natural-minor signature and an accidental on the raised
    // note, so "no sharps or flats" beside a scale containing G# is true and
    // actively misleading. The modes borrow a signature too.
    const named = plan.mode === 'major' || plan.mode === 'natural_minor';
    host.textContent = plan.formula + (named ? `  ·  ${signature(plan.signature)}` : '');
  }

  function drawChords() {
    const host = $('#th-chords');
    if (!host) return;
    host.replaceChildren(...(plan.chords || []).map((c) =>
      h('button.th__chord', {
        onclick: () => {
          kind = 'chord';
          pick.root = c.root;      // the spelling the key gave it, E# and all
          pick.quality = c.quality;
          pick.inversion = 0;
          syncKindUI();
          buildShape();
          buildRoots();
          refresh();
        },
        onmouseenter: () => kb?.setGhost(c.midi),
        onmouseleave: () => kb?.setGhost([]),
      },
        h('span.th__croman', null, c.roman),
        h('span.th__csym', null, c.symbol),
        h('span.th__cnotes', null, c.names.join(' ')))));
  }

  /* ---- pickers ---- */
  /* Matched by pitch class, not by name. A diatonic chord in C# major is rooted on
     E#, which is the honest spelling and is not one of the twelve chips -- so the
     chip it sounds like is the one that lights. */
  function buildRoots() {
    const host = $('#th-roots');
    if (!host || !vocab) return;
    const pc = plan ? plan.pcs[0] : null;
    host.replaceChildren(...vocab.roots.map((r, i) =>
      h('button.th__root' + ((pc === null ? r === pick.root : pc === i) ? '.is-on' : '')
        + (r.length > 1 ? '.is-black' : ''), {
        onclick: () => { pick.root = r; refresh(); },
      }, r)));
  }

  function buildShape() {
    const sel = $('#th-shape');
    if (!sel || !vocab) return;
    if (kind === 'scale') {
      sel.replaceChildren(...vocab.modes.map((m) =>
        h('option', { value: m.id, selected: m.id === pick.mode }, m.label)));
    } else {
      sel.replaceChildren(...vocab.qualities.map((q) =>
        h('option', { value: q.id, selected: q.id === pick.quality }, q.name)));
    }
  }

  function onShape(e) {
    if (kind === 'scale') pick.mode = e.target.value;
    else { pick.quality = e.target.value; pick.inversion = 0; }
    refresh();
  }

  /* Octaves and hand for a scale; inversion for a chord. Different question, same
     slot -- showing both at once would mean half the controls are always inert. */
  function drawOpts() {
    const host = $('#th-opts');
    if (!host) return;
    if (plan.kind === 'scale') {
      host.replaceChildren(
        h('div.seg', null, [1, 2].map((n) =>
          h('button.seg__btn' + (n === pick.octaves ? '.is-on' : ''), {
            onclick: () => { pick.octaves = n; refresh(); },
          }, n === 1 ? '1 octave' : '2 octaves'))),
        h('div.seg', null, ['R', 'L'].map((hd) =>
          h('button.seg__btn' + (hd === pick.hand ? '.is-on' : ''), {
            onclick: () => { pick.hand = hd; refresh(); },
          }, hd === 'R' ? 'R.H.' : 'L.H.'))));
    } else {
      const n = plan.names.length;
      host.replaceChildren(h('div.seg', null,
        Array.from({ length: Math.min(4, n) }, (_, i) =>
          h('button.seg__btn' + (i === pick.inversion ? '.is-on' : ''), {
            onclick: () => { pick.inversion = i; refresh(); },
          }, i === 0 ? 'root' : ordinal(i)))));
    }
  }

  function setKind(k) {
    if (k === kind) return;
    kind = k;
    syncKindUI();
    buildShape();
    refresh();
  }

  function syncKindUI() {
    const btns = $('#th-kind')?.children;
    if (!btns) return;
    btns[0].classList.toggle('is-on', kind === 'scale');
    btns[1].classList.toggle('is-on', kind === 'chord');
  }

  async function hear() {
    if (!plan) return;
    const run = plan.kind === 'scale';
    try {
      await api.post('/api/preview', {
        notes: plan.midi,
        velocity: 78,
        ms: run ? 320 : 1500,
        stagger: run ? 150 : 0,
      });
    } catch (err) { toast(err.message, 'bad'); }
  }

  return {
    el,
    chordsEl,

    async init(ctx) {
      dock = ctx?.kb || null;
      kb = createKeyboard($('#th-board'), {
        low: LOW,
        high: HIGH,
        labels: 'c-only',
        onKeyDown: (n) => api.post('/api/preview', { notes: [n], velocity: 74, ms: 600 })
          .catch(() => { /* auditioning a note is not worth a toast */ }),
      });
      $('#th-hear').onclick = hear;
      try {
        vocab = await api.get('/api/theory');
      } catch (err) { toast(err.message, 'bad'); return; }
      buildRoots();
      buildShape();
      await refresh();
    },

    destroy() {
      dock?.setHighlight([]);       // this panel borrowed the dock; give it back
      kb?.destroy?.();
      kb = null; dock = null; plan = null; vocab = null;
    },
  };
}

function ordinal(n) {
  return ['root', '1st', '2nd', '3rd'][n] || `${n}th`;
}

/* music.key_signature reports counts and the accidentals in staff order; the
   sentence is this side's job. */
function signature(sig) {
  if (!sig) return '';
  const n = sig.sharps || sig.flats;
  if (!n) return 'no sharps or flats';
  return `${n} ${sig.uses_flats ? 'flat' : 'sharp'}${n > 1 ? 's' : ''} `
    + `(${sig.accidentals.join(' ')})`;
}
