/* The note roll -- what you just played, rising out of the keys.
 *
 * The falling-notes videos, upside down: a bar is born at the key you pressed and
 * travels up, growing while you hold and detaching when you let go. So a bar's
 * LENGTH is how long you held the note and the GAP above it is how long you waited
 * -- which makes legato, staccato and a ragged chord visible rather than merely
 * audible.
 *
 * Three things worth knowing about how it is built:
 *
 * 1. **Columns come from the real keyboard, not from arithmetic.** Every key in
 *    keyboard.js carries data-midi, so the x and width of each column is read off
 *    the live SVG with getBoundingClientRect. The keyboard letterboxes itself
 *    inside the dock (preserveAspectRatio), and any formula here would drift from
 *    it the first time the dock changed height. Measured once per resize, never
 *    per frame.
 *
 * 2. **Canvas, and it stops.** A few hundred moving rects as DOM nodes would fight
 *    the 60 Hz frame path for layout. The RAF loop also parks itself when the last
 *    bar leaves the screen and is woken by the next note -- an idle visualiser
 *    should cost nothing, and this app's whole pitch is that it is not busy.
 *
 * 3. **Colour is the zone.** A split shows your left hand in one colour and your
 *    right in another; a layer stacks two. That falls straight out of the zone
 *    table the engine already publishes, and it is the thing a general-purpose
 *    piano visualiser cannot do.
 */

const TRAVEL_SECONDS = 4.0;   // how long a note takes to cross, at any panel height
const MAX_BARS = 600;         // a hard ceiling; two hands cannot outrun it
const MIN_HEAD = 2;           // a just-struck note is still worth a sliver

export function createRoll(container) {
  const canvas = document.createElement('canvas');
  canvas.className = 'roll__canvas';
  container.append(canvas);
  const ctx2d = canvas.getContext('2d', { alpha: true });

  let W = 0, H = 0, dpr = 1;
  const columns = new Map();        // midi -> {x, w, black}
  const octaves = [];               // x of every C, for the pitch guides
  const held = new Map();           // midi -> bar still being held
  let bars = [];
  let palette = readPalette();
  let zones = [];
  let raf = 0;
  let last = 0;
  let running = false;

  /* ---- geometry ------------------------------------------------------- */
  function measure() {
    const rect = container.getBoundingClientRect();
    dpr = Math.min(2, window.devicePixelRatio || 1);
    W = Math.max(1, Math.round(rect.width));
    H = Math.max(1, Math.round(rect.height));
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);
    canvas.style.width = W + 'px';
    canvas.style.height = H + 'px';
    ctx2d.setTransform(dpr, 0, 0, dpr, 0, 0);

    columns.clear();
    octaves.length = 0;
    // The dock keyboard is the ruler. If it is not on the page yet there is
    // nothing to align to, and the next resize will pick it up.
    const keys = document.querySelectorAll('.dock [data-midi]');
    if (!keys.length) return;
    for (const key of keys) {
      const r = key.getBoundingClientRect();
      if (!r.width) continue;
      const midi = Number(key.dataset.midi);
      const black = key.classList.contains('key--black');
      columns.set(midi, { x: r.left - rect.left, w: r.width, black });
      if (midi % 12 === 0) octaves.push(r.left - rect.left);
    }
    draw();
  }

  function readPalette() {
    const s = getComputedStyle(document.documentElement);
    const v = (n, fallback) => (s.getPropertyValue(n) || fallback).trim();
    return {
      zone: [1, 2, 3, 4, 5, 6].map((i) => v(`--zone-${i}`, '#ffa62b')),
      amber: v('--amber', '#ffa62b'),
      hot: v('--amber-hot', '#ffd08a'),
      guide: v('--hairline', '#2c3037'),
      guideHi: v('--hairline-hi', '#3b4048'),
      panel: v('--panel-0', '#08090a'),
    };
  }

  /* Which enabled zone owns this note. A layer has two; the first one wins,
     because a bar has one colour and the lower zone is the one you built first. */
  function zoneOf(midi) {
    for (let i = 0; i < zones.length; i++) {
      const z = zones[i];
      if (z && z.enabled && midi >= z.lo && midi <= z.hi) return i;
    }
    return 0;
  }

  /* ---- events --------------------------------------------------------- */
  function noteOn(midi, velocity) {
    const col = columns.get(midi);
    if (!col) return;
    // A retrigger before the note-off arrived: close the old bar so the new one
    // does not inherit its length.
    const open = held.get(midi);
    if (open) open.heldOn = false;

    const bar = {
      midi,
      x: col.black ? col.x + col.w * 0.06 : col.x + col.w * 0.10,
      w: col.black ? col.w * 0.88 : col.w * 0.80,
      black: col.black,
      vel: Math.max(1, Math.min(127, velocity | 0)),
      zone: zoneOf(midi),
      head: 0,          // px above the keyboard line
      tail: 0,
      heldOn: true,
    };
    if (bars.length >= MAX_BARS) bars.shift();
    bars.push(bar);
    held.set(midi, bar);
    wake();
  }

  function noteOff(midi) {
    const bar = held.get(midi);
    if (bar) bar.heldOn = false;
    held.delete(midi);
  }

  /* ---- the loop ------------------------------------------------------- */
  function wake() {
    if (running) return;
    running = true;
    last = performance.now();
    raf = requestAnimationFrame(tick);
  }

  function tick(now) {
    const dt = Math.min(0.1, (now - last) / 1000);   // a backgrounded tab must not leap
    last = now;
    const speed = H / TRAVEL_SECONDS;

    let alive = 0;
    for (let i = 0; i < bars.length; i++) {
      const b = bars[i];
      b.head += speed * dt;
      if (!b.heldOn) b.tail += speed * dt;
      if (b.tail < H) alive++;
    }
    if (alive !== bars.length) bars = bars.filter((b) => b.tail < H);

    draw();

    // Nothing on screen and nothing held: stop burning frames until the next note.
    if (!bars.length) { running = false; return; }
    raf = requestAnimationFrame(tick);
  }

  function draw() {
    ctx2d.clearRect(0, 0, W, H);

    // Octave guides, so you can read WHERE on the keyboard a run sat.
    ctx2d.lineWidth = 1;
    ctx2d.strokeStyle = palette.guide;
    ctx2d.beginPath();
    for (const x of octaves) {
      ctx2d.moveTo(Math.round(x) + 0.5, 0);
      ctx2d.lineTo(Math.round(x) + 0.5, H);
    }
    ctx2d.stroke();

    for (const b of bars) {
      const top = H - b.head;
      const bottom = H - b.tail;
      const h = Math.max(MIN_HEAD, bottom - top);
      if (bottom < 0) continue;

      // Velocity is brightness, the same rule the keys use. Soft notes recede.
      const a = 0.30 + 0.70 * (b.vel / 127);
      // ...and everything fades as it climbs, so notes dissolve at the top
      // instead of being guillotined by the edge of the panel.
      const fade = Math.max(0, Math.min(1, bottom / H));
      ctx2d.globalAlpha = a * (0.25 + 0.75 * fade);

      ctx2d.fillStyle = palette.zone[b.zone % 6] || palette.amber;
      ctx2d.beginPath();
      const r = Math.min(3, b.w / 2, h / 2);
      ctx2d.roundRect(b.x, top, b.w, h, r);
      ctx2d.fill();

      // A brighter cap on the leading edge: it reads as the note's attack and it
      // is what makes a chord's spread legible when the bars are short.
      if (h > 3) {
        ctx2d.globalAlpha = a * fade * 0.8;
        ctx2d.fillStyle = palette.hot;
        ctx2d.beginPath();
        ctx2d.roundRect(b.x, top, b.w, Math.min(2.5, h), 1);
        ctx2d.fill();
      }
    }
    ctx2d.globalAlpha = 1;
  }

  /* ---- wiring --------------------------------------------------------- */
  const onResize = () => measure();      // measure() redraws
  window.addEventListener('resize', onResize);

  // A theme swap rewrites every colour this reads, and the canvas is not CSS.
  const themeWatch = new MutationObserver(() => {
    palette = readPalette();
    if (bars.length) draw();
  });
  themeWatch.observe(document.documentElement, {
    attributes: true, attributeFilter: ['data-theme'],
  });

  measure();

  return {
    /* Called from the frame path. Keep it cheap: this runs 60 times a second. */
    frame(f) {
      if (f.on) for (const [n, v] of f.on) noteOn(n, v);
      if (f.off) for (const n of f.off) noteOff(n);
    },
    setZones(list) { zones = Array.isArray(list) ? list : []; },
    /* The dock keyboard may not exist or may have resized when the panel opens. */
    remeasure() { measure(); },
    clear() { bars = []; held.clear(); ctx2d.clearRect(0, 0, W, H); },
    destroy() {
      cancelAnimationFrame(raf);
      running = false;
      window.removeEventListener('resize', onResize);
      themeWatch.disconnect();
      canvas.remove();
    },
  };
}
