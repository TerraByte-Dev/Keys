/**
 * The 88-key keyboard widget. One SVG, built once, then never rebuilt.
 *
 * Why it is shaped this way:
 *
 * * SVG with a viewBox, not 88 divs. The geometry below is expressed in one
 *   coordinate system that the browser scales for free, so 400 px and 2000 px
 *   wide are the same code path with no layout math on resize. Painting order
 *   also gives black-over-white hit testing for nothing -- no z-index, no
 *   stacking contexts, and document.elementFromPoint already returns the black
 *   key when the pointer is over one.
 * * Everything on the update path is a class toggle or a custom property on one
 *   element. `setHeld` runs at frame rate while a real-time audio thread is
 *   fighting us for the CPU, so it diffs against the previous Set and touches
 *   only keys that actually changed. A ten-note change touches ten elements.
 * * The two Sets used for that diff are swapped rather than reallocated, and the
 *   128 possible values of `--vel` are precomputed as strings at load, so a
 *   steady-state frame allocates nothing.
 * * No colours live here beyond fallbacks. Everything paints from CSS custom
 *   properties so the app stylesheet owns the palette; this file owns geometry.
 */

const SVG_NS = 'http://www.w3.org/2000/svg';
const STYLE_ID = 'keys-keyboard-geometry';

// The instrument: Yamaha P-71B, A0..C8. 52 white keys, 36 black.
const P71B_LOW = 21;
const P71B_HIGH = 108;

const NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
// Index of each white pitch class within its octave; -1 marks a black pitch class.
const WHITE_INDEX = [0, -1, 1, -1, 2, 3, -1, 4, -1, 5, -1, 6];

// --- geometry ---------------------------------------------------------------
// Units are arbitrary but proportioned off a real piano: white key 23.5 mm wide
// by ~145 mm of playing surface, black key 13.7 mm by ~95 mm.
const WHITE_W = 24;
const WHITE_H = 148;
const B = 0.60;                       // black key width, in white-key widths
const BLACK_W = WHITE_W * B;
const BLACK_H = WHITE_H * 0.62;

// Black keys are NOT centred on the joint between two white keys. Derive the real
// offsets instead of eyeballing them:
//
//   The C-D-E group spans 3 white widths and contains 3 white "backs" (the narrow
//   part behind the black keys) plus 2 black keys. Equal backs means
//   3*x + 2*B = 3, so x = (3 - 2B)/3.
//   The F-G-A-B group spans 4 and holds 4 backs plus 3 blacks: y = (4 - 3B)/4.
//   Walking each group left to right from the C of the octave gives the left
//   edges below.
//
// With B = 0.60 that is C#=0.600, D#=1.800, F#=3.550, G#=4.700, A#=5.850, i.e.
// centres at 0.90 / 2.10 / 3.85 / 5.00 / 6.15. So C# sits left of the C|D joint,
// D# an equal amount right of D|E, F# further left still, G# dead centre, A#
// further right -- the asymmetry a pianist's hand is trained on, and precisely
// what "centre it on the gap" gets wrong.
const CDE_BACK = (3 - 2 * B) / 3;
const FGAB_BACK = (4 - 3 * B) / 4;
const BLACK_LEFT = {
  1: CDE_BACK,                          // C#
  3: 2 * CDE_BACK + B,                  // D#
  6: 3 + FGAB_BACK,                     // F#
  8: 3 + 2 * FGAB_BACK + B,             // G#
  10: 3 + 3 * FGAB_BACK + 2 * B,        // A#
};

// Pointer velocity: the top of a key is soft, the bottom is loud. Velocity 1 is
// inaudible on most SoundFonts, so the soft end lands on 20 rather than 1.
const MIN_POINTER_VEL = 20;
const MAX_POINTER_VEL = 127;
const DEFAULT_VEL = 96;

// Precomputed so the hot path never builds a string. Same trick as the velocity
// curves in backend/engine.py: 128 possible inputs, so just enumerate them.
const VEL_CSS = new Array(128);
for (let v = 0; v < 128; v++) VEL_CSS[v] = (v / 127).toFixed(3);

const LABEL_MODES = ['none', 'c-only', 'all'];

function noteName(midi) {
  return NOTE_NAMES[midi % 12] + (Math.floor(midi / 12) - 1);
}

function whiteOrdinal(midi) {
  return Math.floor(midi / 12) * 7 + WHITE_INDEX[midi % 12];
}

// Only the bottom corners are rounded. A rect with rx would round the top two as
// well, which reads as a toy keyboard rather than a piano.
function keyPath(x, y, w, h, r) {
  return `M${x} ${y}h${w}v${h - r}a${r} ${r} 0 0 1 ${-r} ${r}h${-(w - 2 * r)}a${r} ${r} 0 0 1 ${-r} ${-r}z`;
}

// Injected once per document. Geometry and structure only -- every colour is a
// custom property with a fallback the app is expected to override.
const CSS = `
.keys-kb{display:block;width:100%;height:auto;touch-action:none;-webkit-user-select:none;user-select:none;overflow:visible}
.keys-kb .key{stroke:var(--key-edge,#00000059);stroke-width:.7;stroke-linejoin:round;transition:fill 60ms linear}
.keys-kb .key--white{fill:var(--key-white,#f2f2ef)}
.keys-kb .key--black{fill:var(--key-black,#16171b)}
.keys-kb .key.is-ghost{fill:var(--key-ghost,#8a93a3)}
.keys-kb .key.is-dead{opacity:.34}
.keys-kb .key.is-dead.key--white{fill:var(--key-dead,#6b675f)}
.keys-kb .key.is-highlight{fill:var(--key-highlight,#e0a53c)}
.keys-kb .key.is-sustained{fill:var(--key-sustain,#6f5bd6)}
.keys-kb .key.is-held{fill:var(--key-held,#3f9dff)}
/* Velocity made visible: a soft strike barely leaves the resting colour, a hard
   one lands fully on --key-held. The plain fill above stays as the fallback if
   color-mix does not parse. */
.keys-kb .key--white.is-held{fill:color-mix(in oklab,var(--key-held,#3f9dff) calc(35% + var(--vel,.75)*65%),var(--key-white,#f2f2ef))}
.keys-kb .key--black.is-held{fill:color-mix(in oklab,var(--key-held,#3f9dff) calc(35% + var(--vel,.75)*65%),var(--key-black,#16171b))}
.keys-kb .key.is-pressed{fill:var(--key-pressed,var(--key-held,#3f9dff))}
.keys-kb .key-label{pointer-events:none;font-family:var(--key-font,ui-sans-serif,system-ui,sans-serif);font-size:9px;text-anchor:middle;fill:var(--key-label,#7a8290)}
.keys-kb .key-label--black{fill:var(--key-label-black,#8d94a1)}
.keys-kb.kb--labels-none .key-label{display:none}
.keys-kb.kb--labels-c-only .key-label{display:none}
.keys-kb.kb--labels-c-only .key-label--c{display:inline}
/* An explicitly set label always shows: you asked for it by name. Must stay last
   so it outranks both hide rules above at equal specificity. */
.keys-kb .key-label.is-forced{display:inline}
`;

function injectStyle(doc) {
  if (doc.getElementById(STYLE_ID)) return;
  const style = doc.createElement('style');
  style.id = STYLE_ID;
  style.textContent = CSS;
  doc.head.appendChild(style);
}

class Keyboard {
  constructor(container, options) {
    const opts = options || {};
    this.container = container;
    this.low = Math.max(0, Math.min(127, opts.low === undefined ? P71B_LOW : opts.low | 0));
    this.high = Math.max(this.low, Math.min(127, opts.high === undefined ? P71B_HIGH : opts.high | 0));
    this.onKeyDown = opts.onKeyDown || null;
    this.onKeyUp = opts.onKeyUp || null;
    this.interactive = opts.interactive !== false;

    // Indexed by MIDI note. Flat arrays, so every lookup on the update path is
    // one array read and never a map probe or a querySelector.
    this._el = new Array(128).fill(null);
    this._label = new Array(128).fill(null);
    this._defaultLabel = new Array(128).fill('');
    this._vel = new Uint8Array(128).fill(DEFAULT_VEL);

    this._held = new Set();
    this._heldSpare = new Set();       // swapped with _held, never reallocated
    this._highlight = new Set();
    this._highlightSpare = new Set();
    this._ghost = new Set();
    this._ghostSpare = new Set();
    this._dead = new Set();
    this._deadSpare = new Set();
    this._sustained = new Set();
    this._forced = new Set();
    this._pointers = new Map();        // pointerId -> midi currently sounding

    this._sustain = false;
    this._labels = LABEL_MODES.indexOf(opts.labels) >= 0 ? opts.labels : 'c-only';
    this._destroyed = false;

    injectStyle(container.ownerDocument || document);
    this._build();
    this.setLabels(this._labels);
    this._syncAria();

    if (this.interactive) this._bind();
  }

  // ------------------------------------------------------------------ build
  _build() {
    const doc = this.container.ownerDocument || document;
    let firstWhite = -1;
    let lastWhite = -1;
    for (let n = this.low; n <= this.high; n++) {
      if (WHITE_INDEX[n % 12] >= 0) {
        if (firstWhite < 0) firstWhite = n;
        lastWhite = n;
      }
    }
    if (firstWhite < 0) throw new Error('createKeyboard: range contains no white keys');

    const base = whiteOrdinal(firstWhite);
    const whiteCount = whiteOrdinal(lastWhite) - base + 1;
    const width = whiteCount * WHITE_W;

    const svg = doc.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('class', 'keys-kb');
    svg.setAttribute('viewBox', `0 0 ${width} ${WHITE_H}`);
    // width:100% + height:auto in the stylesheet means the viewBox alone sets the
    // aspect ratio, so the widget stays proportional at any container width.
    svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
    svg.setAttribute('focusable', 'false');

    // Two groups, whites first: SVG paints in document order, so every black key
    // is drawn over -- and hit-tested before -- its neighbours.
    const whites = doc.createElementNS(SVG_NS, 'g');
    const blacks = doc.createElementNS(SVG_NS, 'g');
    const labels = doc.createElementNS(SVG_NS, 'g');
    svg.appendChild(whites);
    svg.appendChild(blacks);
    svg.appendChild(labels);

    for (let n = this.low; n <= this.high; n++) {
      const pc = n % 12;
      const isWhite = WHITE_INDEX[pc] >= 0;
      let x;
      let w;
      let h;
      if (isWhite) {
        x = (whiteOrdinal(n) - base) * WHITE_W;
        w = WHITE_W;
        h = WHITE_H;
      } else {
        x = (Math.floor(n / 12) * 7 - base + BLACK_LEFT[pc]) * WHITE_W;
        w = BLACK_W;
        h = BLACK_H;
        // A black key can only fall outside the drawing when the range starts or
        // ends mid-group (e.g. low = A#0). Drop it rather than draw it clipped.
        if (x < 0 || x + w > width) continue;
      }

      const key = doc.createElementNS(SVG_NS, 'path');
      key.setAttribute('class', isWhite ? 'key key--white' : 'key key--black');
      key.setAttribute('d', keyPath(x, 0, w, h, isWhite ? 3 : 2));
      key.setAttribute('data-midi', String(n));
      (isWhite ? whites : blacks).appendChild(key);
      this._el[n] = key;

      const text = doc.createElementNS(SVG_NS, 'text');
      let cls = 'key-label';
      if (!isWhite) cls += ' key-label--black';
      if (pc === 0) cls += ' key-label--c';
      text.setAttribute('class', cls);
      text.setAttribute('x', (x + w / 2).toFixed(2));
      text.setAttribute('y', (isWhite ? WHITE_H - 9 : BLACK_H - 7).toFixed(2));
      const name = noteName(n);
      text.textContent = name;
      labels.appendChild(text);
      this._label[n] = text;
      this._defaultLabel[n] = name;
    }

    this.container.appendChild(svg);
    this.container.setAttribute('role', 'img');
    this._svg = svg;
    this._span = `${noteName(this.low)} to ${noteName(this.high)}`;
    this._keyCount = this.high - this.low + 1;
  }

  // ------------------------------------------------------- per-key primitives
  _on(n, velocity) {
    const el = this._el[n];
    if (!el) return;
    if (velocity !== undefined && velocity !== null) {
      this._vel[n] = velocity < 1 ? 1 : velocity > 127 ? 127 : velocity | 0;
    }
    el.style.setProperty('--vel', VEL_CSS[this._vel[n]]);
    if (this._sustained.delete(n)) el.classList.remove('is-sustained');
    el.classList.add('is-held');
  }

  _off(n) {
    const el = this._el[n];
    if (!el) return;
    el.classList.remove('is-held');
    // Pedal down: the string keeps ringing after the key comes up, so the key
    // moves to the sustained layer instead of going dark.
    if (this._sustain) {
      this._sustained.add(n);
      el.classList.add('is-sustained');
    }
  }

  // Shared diff for the highlight and ghost layers. Fills `spare`, diffs it
  // against `current`, and hands it back so the caller can swap the two -- the
  // stale one becomes next call's scratch and is cleared on entry.
  _layer(list, current, spare, cls) {
    const next = spare;
    next.clear();
    for (let i = 0; i < list.length; i++) {
      const n = list[i] | 0;
      if (this._el[n]) next.add(n);
    }
    for (const n of next) {
      if (!current.has(n)) this._el[n].classList.add(cls);
    }
    for (const n of current) {
      if (!next.has(n)) this._el[n].classList.remove(cls);
    }
    return next;
  }

  // ------------------------------------------------------------ public API
  setHeld(midiArray) {
    if (this._destroyed) return;
    const list = midiArray || [];
    const next = this._heldSpare;
    next.clear();
    for (let i = 0; i < list.length; i++) {
      const n = list[i] | 0;
      if (this._el[n]) next.add(n);
    }
    let changed = false;
    for (const n of next) {
      if (!this._held.has(n)) {
        this._on(n, undefined);
        changed = true;
      }
    }
    for (const n of this._held) {
      if (!next.has(n)) {
        this._off(n);
        changed = true;
      }
    }
    // Swap, do not allocate. The old Set becomes next frame's scratch buffer.
    this._heldSpare = this._held;
    this._held = next;
    if (changed) this._syncAria();
  }

  noteOn(midi, velocity) {
    if (this._destroyed || !this._el[midi]) return;
    const had = this._held.has(midi);
    this._held.add(midi);
    this._on(midi, velocity === undefined ? DEFAULT_VEL : velocity);
    if (!had) this._syncAria();
  }

  noteOff(midi) {
    if (this._destroyed || !this._el[midi]) return;
    if (this._held.delete(midi)) {
      this._off(midi);
      this._syncAria();
    }
  }

  setSustain(on) {
    const next = !!on;
    if (this._destroyed || next === this._sustain) return;
    this._sustain = next;
    this._svg.classList.toggle('kb--sustain', next);
    if (!next) {
      for (const n of this._sustained) this._el[n].classList.remove('is-sustained');
      this._sustained.clear();
    }
    this._syncAria();
  }

  setHighlight(midiArray) {
    if (this._destroyed) return;
    const next = this._layer(midiArray || [], this._highlight, this._highlightSpare, 'is-highlight');
    this._highlightSpare = this._highlight;
    this._highlight = next;
  }

  setGhost(midiArray) {
    if (this._destroyed) return;
    const next = this._layer(midiArray || [], this._ghost, this._ghostSpare, 'is-ghost');
    this._ghostSpare = this._ghost;
    this._ghost = next;
  }

  /* Keys that no enabled zone covers, i.e. keys that will do nothing when pressed.
     A silent key is indistinguishable from a broken app unless the keyboard says so. */
  setDead(midiArray) {
    if (this._destroyed) return;
    const next = this._layer(midiArray || [], this._dead, this._deadSpare, 'is-dead');
    this._deadSpare = this._dead;
    this._dead = next;
  }

  setLabels(mode) {
    if (this._destroyed) return;
    this._labels = LABEL_MODES.indexOf(mode) >= 0 ? mode : 'none';
    const cl = this._svg.classList;
    cl.remove('kb--labels-none', 'kb--labels-c-only', 'kb--labels-all');
    cl.add('kb--labels-' + this._labels);
  }

  setKeyLabel(midi, text) {
    const el = this._label[midi];
    if (this._destroyed || !el) return;
    el.textContent = String(text);
    el.classList.add('is-forced');
    this._forced.add(midi);
  }

  clearLabels() {
    if (this._destroyed) return;
    for (const n of this._forced) {
      this._label[n].textContent = this._defaultLabel[n];
      this._label[n].classList.remove('is-forced');
    }
    this._forced.clear();
  }

  destroy() {
    if (this._destroyed) return;
    this._destroyed = true;
    if (this.interactive) {
      for (const id of [...this._pointers.keys()]) {
        // Every down we announced owes the app a matching up, teardown included,
        // or the engine is left with a note that never stops.
        this._release(id);
        try {
          this._svg.releasePointerCapture(id);
        } catch (e) { /* the pointer is already gone; nothing to release */ }
      }
      const svg = this._svg;
      svg.removeEventListener('pointerdown', this._onDown);
      svg.removeEventListener('pointermove', this._onMove);
      svg.removeEventListener('pointerup', this._onUp);
      svg.removeEventListener('pointercancel', this._onUp);
      svg.removeEventListener('pointerleave', this._onUp);
      svg.removeEventListener('lostpointercapture', this._onLost);
    }
    this._pointers.clear();
    this._svg.remove();
    this.container.removeAttribute('role');
    this.container.removeAttribute('aria-label');
  }

  // --------------------------------------------------------------- pointer
  _bind() {
    const svg = this._svg;
    this._onDown = (e) => this._down(e);
    this._onMove = (e) => this._move(e);
    this._onUp = (e) => this._up(e);
    this._onLost = (e) => this._up(e);
    svg.addEventListener('pointerdown', this._onDown);
    svg.addEventListener('pointermove', this._onMove);
    svg.addEventListener('pointerup', this._onUp);
    svg.addEventListener('pointercancel', this._onUp);
    svg.addEventListener('pointerleave', this._onUp);
    // The one that actually guarantees no stuck key: it fires on release for any
    // reason at all, including reasons the other five do not cover.
    svg.addEventListener('lostpointercapture', this._onLost);
  }

  _keyAt(x, y) {
    const doc = this.container.ownerDocument || document;
    const hit = doc.elementFromPoint(x, y);
    if (!hit) return -1;
    const key = hit.closest ? hit.closest('[data-midi]') : null;
    if (!key || !this._svg.contains(key)) return -1;
    return Number(key.getAttribute('data-midi'));
  }

  // Lower on the key is louder, which is roughly how a real key behaves: strike
  // near the fallboard and you get less leverage.
  _velocityAt(el, clientY) {
    const box = el.getBoundingClientRect();
    if (!box.height) return DEFAULT_VEL;
    let f = (clientY - box.top) / box.height;
    f = f < 0 ? 0 : f > 1 ? 1 : f;
    return Math.round(MIN_POINTER_VEL + f * (MAX_POINTER_VEL - MIN_POINTER_VEL));
  }

  _strike(id, midi, clientY) {
    this._pointers.set(id, midi);
    const vel = this._velocityAt(this._el[midi], clientY);
    this._el[midi].classList.add('is-pressed');
    if (this.onKeyDown) this.onKeyDown(midi, vel);
  }

  // Silence whatever this pointer is sounding but keep the pointer registered.
  // A glissando that dips off the bottom of the keybed and comes back has to go
  // on receiving moves, so "down, but not on a key" (-1) is a real state rather
  // than a reason to forget the pointer.
  _silence(id) {
    const midi = this._pointers.get(id);
    if (midi === undefined || midi < 0) return;
    this._pointers.set(id, -1);
    this._el[midi].classList.remove('is-pressed');
    if (this.onKeyUp) this.onKeyUp(midi);
  }

  _release(id) {
    if (!this._pointers.has(id)) return;
    this._silence(id);
    this._pointers.delete(id);
  }

  _down(e) {
    const midi = this._keyAt(e.clientX, e.clientY);
    if (midi < 0) return;
    e.preventDefault();
    // Capture on the root, not the key: a glissando has to keep getting moves
    // after the pointer has left the key it started on, and the capture is what
    // guarantees the matching up event lands here too.
    try {
      this._svg.setPointerCapture(e.pointerId);
    } catch (err) { /* capture is a nicety; the drag still mostly works without it */ }
    this._strike(e.pointerId, midi, e.clientY);
  }

  _move(e) {
    if (!this._pointers.has(e.pointerId)) return;
    // Capture retargets events to the root, so the DOM cannot tell us which key
    // is under the pointer -- ask the document directly.
    const midi = this._keyAt(e.clientX, e.clientY);
    const current = this._pointers.get(e.pointerId);
    if (midi === current) return;
    this._silence(e.pointerId);
    if (midi >= 0) this._strike(e.pointerId, midi, e.clientY);
  }

  _up(e) {
    this._release(e.pointerId);
  }

  // ------------------------------------------------------------------ aria
  // Rebuilt only when the held set actually changes, never per frame.
  _syncAria() {
    let text = `Piano keyboard, ${this._keyCount} keys, ${this._span}. `;
    const n = this._held.size;
    if (!n) {
      text += 'No keys held.';
    } else {
      const names = [];
      for (const m of this._held) {
        if (names.length === 12) break;
        names.push(noteName(m));
      }
      text += `Holding ${names.join(', ')}`;
      text += n > 12 ? ` and ${n - 12} more.` : '.';
    }
    if (this._sustain) text += ' Sustain pedal down.';
    this.container.setAttribute('aria-label', text);
  }
}

/**
 * Render an 88-key keyboard into `container` and return a controller for it.
 *
 * options: { low, high, onKeyDown(midi, velocity), onKeyUp(midi), interactive,
 *            labels: 'none' | 'c-only' | 'all' }
 */
export function createKeyboard(container, options = {}) {
  return new Keyboard(container, options);
}
