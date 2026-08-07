/* The note roll -- what you just played, rising out of the keys.
 *
 * The falling-notes videos, upside down: a bar is born at the key you pressed and
 * travels up, growing while you hold and detaching when you let go. So a bar's
 * LENGTH is how long you held the note and the GAP above it is how long you waited
 * -- which makes legato, staccato and a ragged chord visible rather than merely
 * audible.
 *
 * GHOST MODE TURNS THE WHOLE PAPER OVER, and it is worth saying why it reverses the
 * roll rather than adding a second direction to it.
 *
 * The tempting design is to keep your playing rising and let the piece fall to meet
 * it at the keybed. It does not survive the arithmetic. Both bars would be anchored
 * at the same edge, so they GROW FROM IT TOGETHER rather than tessellating: at the
 * halfway point of a held note the target and your answer are exactly coincident,
 * and the rest of the time you are discriminating hollow from solid in two
 * counter-flowing streams sharing 88 narrow columns. That is a reading cost paid on
 * every glance, in the one place attention should be cheapest.
 *
 * So in ghost mode everything falls -- the printed piece AND your playing, at the
 * same px/s, past a now-line HIT_PAD above the keys. That buys the property this
 * mode exists for:
 *
 *     both edges descend at the same rate, so the vertical gap between your bar
 *     and its ghost IS your timing error, and it does not drift as the pair
 *     travels. Dead on: the edges are flush. A hair late: your bar sits above by
 *     exactly that much, frozen, all the way down.
 *
 * Free play is untouched -- with no model set this file behaves exactly as it did
 * before ghost mode existed, which is the point of the `ghost === null` branches.
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

/* Pixels per second, NOT a traversal time.
 *
 * The first version fixed how long a note took to cross the panel, which sounds
 * tidy and is wrong: a fixed traversal time makes notes travel faster the moment the
 * panel gets taller, so the same rule meant two different speeds on two different
 * screens. A roll scrolls at a speed; a taller window should show MORE MUSIC, not the
 * same music in a hurry. */
const DEFAULT_SPEED = 100;    // px/s -- roughly 6-8 seconds on a full-screen panel
const MIN_SPEED = 40;
const MAX_SPEED = 240;
const MAX_BARS = 600;         // a hard ceiling; two hands cannot outrun it
const MIN_HEAD = 2;           // a just-struck note is still worth a sliver

/* How far the ghost-mode now-line sits above the keys, in pixels.
 *
 * Flat, not a fraction of the panel. A fraction would spend 140px of a large screen on
 * dead air between where a note lands and the key it names -- so the taller the
 * window, the further your eye has to travel to answer "which key is that". A constant
 * keeps the landing close to the keys at every size and still leaves room to read the
 * gap you played.
 *
 * Set it to 0 and the feedback strip disappears cleanly, leaving plain
 * notes-land-on-the-keys geometry. That is the whole escape hatch, and it is one
 * number. */
const HIT_PAD = 56;

/* Where a note sits on the falling paper, in pixels down the panel.
 *
 * Pulled out of the draw loop and exported because it is the one piece of arithmetic
 * the whole mode rests on, and it should be checkable without a canvas -- see
 * tools/ghost_check.py. It is also the formula that makes the timing feedback
 * honest: your played bar is projected by the same rule from the same line, so the
 * gap between the two bottom edges is the difference between when the note was due
 * and when you played it, times the scroll rate, and nothing else.
 *
 * The BOTTOM edge is the onset. Falling, the low edge is the leading one, so it is
 * the edge that crosses the now-line at the moment the note is due.
 */
export function noteSpan(onset, duration, nowQ, qps, speed, hitY) {
  const bottom = hitY + ((nowQ - onset) / qps) * speed;
  return { top: bottom - (duration / qps) * speed, bottom };
}

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
  let speed = DEFAULT_SPEED;

  /* Ghost mode. `ghost` is the piece model from ghost.js, or null in free play.
     This file owns pixels and nothing else: the model owns the clock, the gates and
     the hand filter, and gets advanced once per frame from the RAF loop below so
     there is exactly one clock in the feature. */
  let ghost = null;
  let hitY = 0;              // y of the now-line: H in free play, H - HIT_PAD in ghost
  let gLo = 0, gHi = 0;      // monotonic cursors into ghost.notes -- never filter per frame
  let gSeq = -1;             // model bumps this on any seek; cursors rebuild when it moves
  let gMaxDur = 0;           // longest note, so `lo` can advance on onset order alone

  /* The room the notes live in. Kept behind a flag because ghost mode turns it off:
     a wash of light and drifting motes behind a target you are reading is decoration
     fighting the one job the screen has. */
  let immersive = false;
  let energy = 0;          // decaying loudness, drives the glow at the keybed
  const motes = [];        // slow rising specks; a fixed pool, never grown per frame
  const MOTES = 46;

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

    // Every path that changes the panel's size routes through here -- toggleRoll,
    // toggleImmersive, setImmersive and the resize listener -- so this is the one
    // place the now-line can be kept true.
    hitY = ghost ? Math.max(0, H - HIT_PAD) : H;

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

  /* Where a bar sits inside its key's column. Black keys are narrower and get a
     tighter inset, so a bar reads as belonging to the key under it. Shared by your
     playing and by the ghost, because a target that does not line up with the answer
     is worse than no target. */
  function columnRect(midi) {
    const col = columns.get(midi);
    if (!col) return null;
    return {
      x: col.black ? col.x + col.w * 0.06 : col.x + col.w * 0.10,
      w: col.black ? col.w * 0.88 : col.w * 0.80,
    };
  }

  /* ---- events --------------------------------------------------------- */
  function noteOn(midi, velocity) {
    const col = columns.get(midi);
    if (!col) return;
    // A retrigger before the note-off arrived: close the old bar so the new one
    // does not inherit its length.
    const open = held.get(midi);
    if (open) open.heldOn = false;

    const rect = columnRect(midi);
    const bar = {
      midi,
      x: rect.x,
      w: rect.w,
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
    // The keybed glow answers how hard and how much you are playing, not each note
    // -- a bump that decays, so a run swells it and a single note barely moves it.
    energy = Math.min(1, energy + 0.22 + 0.5 * (bar.vel / 127));
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

    // The model's clock IS this frame loop. It decides whether time moves -- in wait
    // mode a gate freezes it -- and this file never asks; it just draws wherever the
    // playhead ended up.
    if (ghost) ghost.advance(dt);

    // Ambience is off in ghost mode: motes and a keybed glow behind a target you are
    // trying to read is decoration fighting the one job the screen has.
    if (immersive && !ghost) {
      energy *= Math.pow(0.28, dt);          // ~1.3s to fall away
      for (const m of motes) {
        m.y -= m.v * dt;
        if (m.y < -8) { m.y = H + 8; m.x = Math.random() * W; }
      }
    }

    // How far a bar's trailing edge may travel before it is off the panel. Falling,
    // that is only the strip below the now-line; rising, it is the whole thing.
    const life = ghost ? Math.max(1, H - hitY) : H;
    let alive = 0;
    for (let i = 0; i < bars.length; i++) {
      const b = bars[i];
      b.head += speed * dt;
      if (!b.heldOn) b.tail += speed * dt;
      if (b.tail < life) alive++;
    }
    if (alive !== bars.length) bars = bars.filter((b) => b.tail < life);

    draw();

    // Nothing on screen and nothing held: stop burning frames until the next note.
    //
    // Immersive is the exception and keeps drifting. Parking it when the glow fades
    // freezes the motes in mid-air, which reads as a hung screen rather than a calm
    // one -- and this is a mode you deliberately opened to leave running.
    //
    // Ghost mode is the other exception, and a harder one: the piece keeps falling
    // whether or not you are playing, so parking on an empty bar list would stop the
    // music.
    if (!bars.length && !immersive && !ghost) { running = false; return; }
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

    if (immersive && !ghost) drawAmbience();
    if (ghost) drawGhost();          // first, so your own playing sits in front of it

    const life = ghost ? Math.max(1, H - hitY) : H;
    for (const b of bars) {
      /* The only difference between the two modes, and it is a mirror about the
         now-line. Rising: `head` is the leading edge and it is ABOVE, so it makes
         the top. Falling: the leading edge is the low one, so `head` makes the
         bottom instead and the same bar object draws upside down. */
      const top = ghost ? hitY + b.tail : H - b.head;
      const bottom = ghost ? hitY + b.head : H - b.tail;
      const h = Math.max(MIN_HEAD, bottom - top);
      if (ghost ? top > H : bottom < 0) continue;

      // Velocity is brightness, the same rule the keys use. Soft notes recede.
      const a = 0.30 + 0.70 * (b.vel / 127);
      // ...and everything fades as it travels, so notes dissolve at the far edge
      // instead of being guillotined by it.
      const fade = ghost
        ? Math.max(0, Math.min(1, 1 - (top - hitY) / life))
        : Math.max(0, Math.min(1, bottom / H));
      ctx2d.globalAlpha = a * (0.25 + 0.75 * fade);

      ctx2d.fillStyle = palette.zone[b.zone % 6] || palette.amber;
      ctx2d.beginPath();
      const r = Math.min(3, b.w / 2, h / 2);
      ctx2d.roundRect(b.x, top, b.w, h, r);
      ctx2d.fill();

      // A brighter cap on the leading edge: it reads as the note's attack and it
      // is what makes a chord's spread legible when the bars are short.
      //
      // Which edge that is flips with the mode, and getting it wrong would quietly
      // break the one thing ghost mode is for: the cap must sit on the edge that
      // marks WHEN YOU PRESSED, because that is the edge being compared against the
      // ghost's onset edge. Rising, the attack leads at the top; falling, it is the
      // bottom, and the two travel together.
      if (h > 3) {
        const cap = Math.min(2.5, h);
        ctx2d.globalAlpha = a * fade * 0.8;
        ctx2d.fillStyle = palette.hot;
        ctx2d.beginPath();
        ctx2d.roundRect(b.x, ghost ? bottom - cap : top, b.w, cap, 1);
        ctx2d.fill();
      }
    }
    ctx2d.globalAlpha = 1;
  }

  /* The room the notes live in: a wash of light at the keybed that answers your
     playing, and slow motes so the screen is never completely still. Drawn first,
     so every note sits in front of it. */
  function drawAmbience() {
    const lift = 0.10 + 0.55 * energy;
    const g = ctx2d.createRadialGradient(W / 2, H, 0, W / 2, H, H * (0.55 + 0.45 * energy));
    g.addColorStop(0, palette.zone[0] || palette.amber);
    g.addColorStop(1, 'transparent');
    ctx2d.globalAlpha = lift * 0.22;
    ctx2d.fillStyle = g;
    ctx2d.fillRect(0, 0, W, H);

    ctx2d.globalAlpha = 0.16 + 0.20 * energy;
    ctx2d.fillStyle = palette.hot;
    for (const m of motes) {
      ctx2d.beginPath();
      ctx2d.arc(m.x, m.y, m.r, 0, 6.283185);
      ctx2d.fill();
    }
    ctx2d.globalAlpha = 1;
  }

  /* ---- ghost mode ------------------------------------------------------ */
  /* The piece, falling. Nothing here touches the model -- the clock was already
     advanced for this frame, and drawing twice from one position must look the
     same both times. */
  function drawGhost() {
    const notes = ghost.notes;
    const qps = Math.max(0.001, ghost.qps);
    const nowQ = ghost.nowQ;
    const life = Math.max(1, H - hitY);
    // Quarter notes that fit between the now-line and each edge of the panel. Both
    // shrink when you slow the tempo down, which is the whole reason slow practice
    // buys reading time: fewer notes on the same glass.
    const aheadQ = (hitY / speed) * qps;
    const pastQ = (life / speed) * qps;
    const yOf = (q) => hitY + ((nowQ - q) / qps) * speed;

    // A seek moves the playhead backwards, and a cursor that only walks forward
    // cannot follow it. The model bumps `seq` whenever that happens.
    if (gSeq !== ghost.seq) { gSeq = ghost.seq; gLo = 0; gHi = 0; }

    /* Both cursors walk forward only; nothing here allocates or filters per frame.
       `lo` cannot key off the note it is skipping -- a whole note that started eight
       beats ago is still on screen long after a semiquaver that started later has
       gone -- so it lags by the longest note in the piece, measured once at load. */
    while (gLo < notes.length && notes[gLo].onset + gMaxDur < nowQ - pastQ) gLo++;
    while (gHi < notes.length && notes[gHi].onset < nowQ + aheadQ) gHi++;

    drawGrid(yOf, nowQ - pastQ, nowQ + aheadQ);
    if (ghost.looping) drawSection(yOf);

    const hands = ghost.hands;
    for (let i = gLo; i < gHi; i++) {
      const n = notes[i];
      const { top, bottom } = noteSpan(n.onset, n.duration, nowQ, qps, speed, hitY);
      if (bottom <= 0 || top >= H) continue;

      const rect = columnRect(n.midi);
      if (!rect) continue;                      // outside the 88 keys on screen

      /* The muted hand does not vanish -- it scrolls past at a quarter of the
         weight. You need it to keep your place in the piece; you do not need it
         competing with the hand you are actually drilling. */
      const live = hands === 'both' || (hands === 'R' ? n.staff === 1 : n.staff === 2);
      const colour = palette.zone[n.staff === 2 ? 1 : 0] || palette.amber;
      const h = Math.max(MIN_HEAD, bottom - top);
      const r = Math.min(3, rect.w / 2, h / 2);

      // Fades in at the far edge rather than appearing whole, so notes arrive
      // rather than blink into being.
      const enter = Math.max(0, Math.min(1, bottom / Math.max(1, hitY * 0.35)));
      // ...and dims once it is behind you, where it is no longer a target.
      const spent = top > hitY ? 0.35 : 1;
      const weight = (live ? 1 : 0.25) * enter * spent;

      ctx2d.globalAlpha = 0.16 * weight;
      ctx2d.fillStyle = colour;
      ctx2d.beginPath();
      ctx2d.roundRect(rect.x, top, rect.w, h, r);
      ctx2d.fill();

      // The outline is what makes it read as a target rather than as a note that
      // already sounded: hollow is "yours to fill".
      ctx2d.globalAlpha = 0.75 * weight;
      ctx2d.strokeStyle = colour;
      ctx2d.lineWidth = 1;
      ctx2d.beginPath();
      ctx2d.roundRect(rect.x + 0.5, top + 0.5, Math.max(1, rect.w - 1),
                      Math.max(1, h - 1), r);
      ctx2d.stroke();
    }
    ctx2d.globalAlpha = 1;
    drawNowLine();
  }

  /* The two ends of the looping section, on the paper. The scrub band says WHERE in
     the piece you are grinding; these say when the wrap is coming, which is the one
     you need while you are reading. Dashed and hot, because a solid hairline here
     would be a bar line that lies. */
  function drawSection(yOf) {
    ctx2d.setLineDash([6, 5]);
    ctx2d.strokeStyle = palette.hot;
    ctx2d.globalAlpha = 0.7;
    ctx2d.lineWidth = 1;
    ctx2d.beginPath();
    for (const q of [ghost.loopA, ghost.loopB]) {
      const y = Math.round(yOf(q)) + 0.5;
      if (y < -2 || y > H + 2) continue;
      ctx2d.moveTo(0, y);
      ctx2d.lineTo(W, y);
    }
    ctx2d.stroke();
    ctx2d.setLineDash([]);
    ctx2d.globalAlpha = 1;
  }

  /* Bar lines and beats. Without them a roll is confetti: you can see WHICH notes
     are coming and have no idea where the downbeat is. */
  function drawGrid(yOf, fromQ, toQ) {
    const bars2 = ghost.measures;
    if (!bars2.length) return;

    for (let i = 0; i < bars2.length; i++) {
      const m = bars2[i];
      const next = bars2[i + 1];
      const end = next ? next.onset : m.onset + m.beats * (4 / m.beat_type);
      if (end < fromQ || m.onset > toQ) continue;

      const y = Math.round(yOf(m.onset)) + 0.5;
      if (y > -2 && y < H + 2) {
        ctx2d.strokeStyle = palette.guideHi;
        ctx2d.globalAlpha = 0.9;
        ctx2d.lineWidth = 1;
        ctx2d.beginPath();
        ctx2d.moveTo(0, y);
        ctx2d.lineTo(W, y);
        ctx2d.stroke();
      }

      /* Derived from THIS measure's own signature and clipped to its own end, never
         from one global grid. A measure is as long as what was actually written in
         it -- a pickup bar is short and a cadenza is long, and both are correct --
         so a grid extrapolated from bar 1 walks off the music by the second page. */
      const beat = 4 / m.beat_type;
      ctx2d.strokeStyle = palette.guide;
      ctx2d.globalAlpha = 0.55;
      ctx2d.beginPath();
      for (let k = 1; k < m.beats; k++) {
        const q = m.onset + k * beat;
        if (q >= end) break;
        const by = Math.round(yOf(q)) + 0.5;
        if (by < -2 || by > H + 2) continue;
        ctx2d.moveTo(0, by);
        ctx2d.lineTo(W, by);
      }
      ctx2d.stroke();
    }
    ctx2d.globalAlpha = 1;
  }

  /* The now-line: where a note is due. It brightens and thickens while a gate is
     holding the clock, because "the app is waiting for you" and "the app has hung"
     must never look the same. */
  function drawNowLine() {
    const waiting = ghost.waiting;
    const y = Math.round(hitY) + 0.5;
    const g = ctx2d.createLinearGradient(0, 0, W, 0);
    g.addColorStop(0, 'transparent');
    g.addColorStop(0.10, waiting ? palette.hot : palette.amber);
    g.addColorStop(0.90, waiting ? palette.hot : palette.amber);
    g.addColorStop(1, 'transparent');
    ctx2d.globalAlpha = waiting ? 0.95 : 0.55;
    ctx2d.strokeStyle = g;
    ctx2d.lineWidth = waiting ? 2 : 1;
    ctx2d.beginPath();
    ctx2d.moveTo(0, y);
    ctx2d.lineTo(W, y);
    ctx2d.stroke();
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

    setSpeed(pxPerSecond) {
      speed = Math.max(MIN_SPEED, Math.min(MAX_SPEED, Number(pxPerSecond) || DEFAULT_SPEED));
      return speed;
    },

    /* How much of your playing fits on screen at the current size and speed. The
       number people actually care about, and it changes when either does. */
    secondsOnScreen() { return H / speed; },

    /* Arm ghost mode with the model from ghost.js, or pass null to go back to free
       play. Null restores exactly the behaviour that shipped before this existed --
       hitY returns to H and every branch above takes its original side. */
    setGhost(model) {
      ghost = model || null;
      gSeq = -1; gLo = 0; gHi = 0; gMaxDur = 0;
      if (ghost) {
        // Measured once. drawGhost's `lo` cursor lags by exactly this much, so a
        // piece with one long pedal tone does not drop it off the screen early.
        for (const n of ghost.notes) if (n.duration > gMaxDur) gMaxDur = n.duration;
        // Whatever you were playing before belongs to the other mode's geometry;
        // leaving it would send bars flying the wrong way from the wrong line.
        bars = [];
        held.clear();
        energy = 0;
      }
      measure();                      // hitY moves, and measure() redraws
      if (ghost) wake();
      return !!ghost;
    },

    /* Seconds of music between the now-line and the top of the panel -- your actual
       lookahead, which is what "can I read this at this speed" means. Distinct from
       secondsOnScreen(), which counts the whole panel. */
    secondsAhead() { return (ghost ? hitY : H) / speed; },

    setImmersive(on) {
      immersive = !!on;
      if (immersive && !motes.length) {
        for (let i = 0; i < MOTES; i++) {
          motes.push({ x: Math.random() * W, y: Math.random() * H,
                       v: 6 + Math.random() * 16, r: 0.6 + Math.random() * 1.4 });
        }
      }
      if (!immersive) energy = 0;
      measure();
      if (immersive) wake();
    },
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
