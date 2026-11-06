/* Chord Flow -- freestyle with the keys lit up for you.
 *
 * The keyboard always shows one chord: a solid shape to try (left hand low, right
 * hand above middle C) and every other key of that chord, tinted, because any of them
 * sounds good right now. Before the chord changes, the next one appears as an outline;
 * a key that is solid AND outlined is one you keep holding. That overlap is the lesson
 * -- most chord changes move one or two fingers, not the whole hand -- and it is drawn
 * rather than explained.
 *
 * What plays comes from harmony.js: real progressions, each played twice, chained by
 * how well one ends into the next. This file is the clock, the drawing and the pad.
 *
 * Spelling comes from the server (/api/theory/scale), the same rule as theory.js:
 * the frontend owns no mode table. harmony.js works in pitch classes and never names
 * a note, so the two cannot disagree about which key is which.
 *
 * Nothing here is graded or saved. The counts at the bottom are for this sitting only,
 * and they are counts, not a percentage: a beginner who is told "62%" after a freestyle
 * has been told the wrong thing.
 */

import { api, h, mod, slider, toast, toggle } from './ui.js';
import { ghostArmed, onPanic } from './app.js';
import { createProgression, leftKeys, voiceLeft, voiceRight } from './harmony.js';

/* The keys on offer, easiest first. The hint is what a beginner actually wants to
   know about a key: how many black keys it uses. */
const KEYS = [
  { id: 'C-major', key: 'C', mode: 'major' },
  { id: 'A-minor', key: 'A', mode: 'natural_minor' },
  { id: 'G-major', key: 'G', mode: 'major' },
  { id: 'E-minor', key: 'E', mode: 'natural_minor' },
  { id: 'F-major', key: 'F', mode: 'major' },
  { id: 'D-minor', key: 'D', mode: 'natural_minor' },
  { id: 'D-major', key: 'D', mode: 'major', note: 'the key Memories is in' },
  { id: 'B-minor', key: 'B', mode: 'natural_minor' },
  { id: 'Bb-major', key: 'Bb', mode: 'major' },
  { id: 'G-minor', key: 'G', mode: 'natural_minor' },
  { id: 'F#-major', key: 'F#', mode: 'major', note: 'your black-key habit' },
  { id: 'Eb-minor', key: 'Eb', mode: 'natural_minor', note: 'the black keys, moodier' },
];
const PC = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };
const pcOf = (name) => (PC[name[0]] + (name.match(/#/g) || []).length
  - (name.slice(1).match(/b/g) || []).length + 12) % 12;
const BLACK = new Set([1, 3, 6, 8, 10]);
const pretty = (s) => s.replace(/#/g, '♯').replace(/(?<=[A-G])b/g, '♭');
const DEGREE = { I: 0, ii: 1, iii: 2, IV: 3, V: 4, vi: 5, i: 0, III: 2, iv: 3, v: 4, VI: 5, VII: 6 };

/* Seconds per chord. "Wait" has no clock: the chord changes when you have played it. */
const PACES = [
  { id: 'slow', label: 'Slow', secs: 8 },
  { id: 'steady', label: 'Steady', secs: 4 },
  { id: 'quick', label: 'Quick', secs: 2 },
  { id: 'wait', label: 'Wait for me', secs: 0 },
];
const BEATS = 4;
const SOUNDS = [
  { id: 'off', label: 'Off' },
  { id: 'pad', label: 'Warm pad' },
  { id: 'strings', label: 'Strings' },
  { id: 'epiano', label: 'Electric piano' },
  { id: 'choir', label: 'Choir' },
];
const ROLE_TEXT = {
  home: ['Home', 'sounds settled'],
  away: ['Away', 'travelling'],
  pull: ['Pull', 'wants to go home'],
};
const ROMAN_HELP = 'The numbers count up the key: I is the chord built on its 1st note, '
  + 'IV on its 4th, V on its 5th. Capitals are major chords (brighter), small letters minor (sadder).';
const DEFAULTS = { key: 'C-major', pace: 'slow', lh: 'root', sound: 'pad', volume: 0.5, wander: false };

export function createFlow(ctx, { onStart } = {}) {
  const saved = ctx.state?.settings?.ui?.flow || {};
  const opt = { ...DEFAULTS, ...saved };
  if (!KEYS.some((k) => k.id === opt.key)) opt.key = DEFAULTS.key;
  if (!PACES.some((p) => p.id === opt.pace)) opt.pace = DEFAULTS.pace;

  const plans = new Map();     // key id -> loaded plan
  const loading = new Map();   // key id -> its request, so two callers share one
  let plan = null;             // the current key's
  let prog = null;
  let now = null, next = null; // see shape()
  let playing = false;
  let destroyed = false;
  let starting = false;
  let drawn = false;           // our marks are on the keyboard
  let yielded = false;         // stepped aside for a song on the roll
  let startedAt = 0, pausedAt = 0;
  let raf = 0;
  let caught = new Set();      // chord pitch classes you have played during this chord
  let advanceAt = 0;           // wait mode: when the caught chord gives way
  let phrasesInKey = 0;
  let padTimer = 0;
  let padWarned = false;
  const tally = { chord: 0, key: 0, out: 0, chords: 0, hit: 0 };

  /* ── the panel ─────────────────────────────────────────────────────────── */
  const seg = (items, current, onpick) => {
    const row = h('div.seg', null, items.map((it) => h('button.seg__btn' + (it.id === current ? '.is-on' : ''), {
      onclick: (e) => {
        for (const b of row.children) b.classList.toggle('is-on', b === e.currentTarget);
        onpick(it.id);
      },
    }, it.label)));
    return row;
  };

  const keySelect = h('select.flow__select', { onchange: (e) => setKey(e.target.value) },
    KEYS.map((k) => h('option', { value: k.id, selected: k.id === opt.key }, keyLabel(k))));
  const soundSelect = h('select.flow__select', {
    onchange: (e) => { opt.sound = e.target.value; padWarned = false; save(); sendPad(); },
  }, SOUNDS.map((s) => h('option', { value: s.id, selected: s.id === opt.sound }, s.label)));
  const volume = slider({
    min: 0, max: 100, value: Math.round(opt.volume * 100),
    oninput: (v) => { opt.volume = v / 100; },
    onchange: () => { save(); sendPad(); },
  });

  const el = {
    nowSym: h('div.flow__sym'), nowRole: h('div.flow__role'), nowHands: h('div.flow__hands'),
    nextSym: h('div.flow__sym.flow__sym--next'), nextRole: h('div.flow__role'), tip: h('div.flow__tip'),
    beats: h('div.flow__beats', null, Array.from({ length: BEATS }, () => h('span.flow__beat'))),
    bar: h('div.flow__bar', null, h('span.flow__fill')),
    phraseName: h('div.flow__pname', { title: ROMAN_HELP }), phraseBlurb: h('div.flow__pblurb'),
    chips: h('div.flow__chips', { title: ROMAN_HELP }),
    go: h('button.btn.btn--lg.btn--primary.flow__go', { onclick: () => (playing ? pause() : start()) }, 'Start'),
    stats: h('div.flow__stats'),
    status: h('div.flow__status'),
  };

  const root = h('div.col-12', null, mod('Chord Flow', 'every lit key sounds good right now',
    h('div.flow', null,
      h('div.flow__setup', null,
        h('label.flow__opt', null, h('span.flow__lbl', null, 'Key'), keySelect),
        h('div.flow__opt', null, h('span.flow__lbl', null, 'Pace'),
          seg(PACES, opt.pace, (id) => { opt.pace = id; save(); restartClock(); })),
        h('div.flow__opt', null, h('span.flow__lbl', null, 'Left hand'),
          seg([{ id: 'root', label: 'One note' }, { id: 'fifth', label: 'Two notes' },
            { id: 'off', label: 'Rest' }], opt.lh, (id) => { opt.lh = id; save(); revoice(); })),
        h('label.flow__opt', null, h('span.flow__lbl', null, 'Backing'), soundSelect),
        h('label.flow__opt.flow__opt--vol', null, h('span.flow__lbl', null, 'Volume'), volume),
        h('div.flow__opt', null, toggle('Wander to nearby keys', opt.wander,
          (on) => { opt.wander = on; save(); }))),

      h('div.flow__stage', null,
        h('div.flow__card', null, h('div.flow__lbl', null, 'Now'), el.nowSym, el.nowRole, el.nowHands),
        h('div.flow__clock', null, el.beats, el.bar, el.status),
        h('div.flow__card.flow__card--next', null, h('div.flow__lbl', null, 'Next'),
          el.nextSym, el.nextRole, el.tip)),

      h('div.flow__phrase', null,
        h('div.flow__ptext', null, el.phraseName, el.phraseBlurb), el.chips),

      h('div.flow__foot', null,
        el.go,
        h('button.btn', { onclick: () => skip() }, 'Next chord'),
        h('button.btn', { onclick: () => newPhrase() }, 'New progression'),
        h('span.list__spacer'),
        el.stats),

      h('div.flow__legend', null,
        legend('flow__sw--target', 'play this shape'),
        legend('flow__sw--tone', 'also sounds good'),
        legend('flow__sw--next', 'coming next'),
        legend('flow__sw--good', 'you hit the chord'),
        legend('flow__sw--out', 'outside the key (spicy)')))));

  function legend(cls, text) {
    return h('span.flow__key', null, h('span.flow__sw.' + cls), text);
  }

  function keyLabel(k) {
    const name = `${pretty(k.key)} ${k.mode === 'major' ? 'major' : 'minor'}`;
    const black = scalePcsGuess(k).filter((pc) => BLACK.has(pc)).length;
    const keys = black === 0 ? 'all white keys' : black === 5 ? 'all five black keys + 2 white'
      : `${black} black key${black === 1 ? '' : 's'}`;
    return `${name} -- ${keys}${k.note ? ` (${k.note})` : ''}`;
  }

  // Only for the label, before the server has answered. Major and natural minor only.
  function scalePcsGuess(k) {
    const t = pcOf(k.key);
    const steps = k.mode === 'major' ? [0, 2, 4, 5, 7, 9, 11] : [0, 2, 3, 5, 7, 8, 10];
    return steps.map((s) => (t + s) % 12);
  }

  /* ── keys and chords ───────────────────────────────────────────────────── */
  function loadPlan(id) {
    if (plans.has(id)) return Promise.resolve(plans.get(id));
    if (loading.has(id)) return loading.get(id);
    const k = KEYS.find((x) => x.id === id);
    const req = api.get(`/api/theory/scale?key=${encodeURIComponent(k.key)}&mode=${k.mode}`)
      .then((p) => {
        const out = { ...k, pcs: p.pcs, chords: p.chords,
          title: `${pretty(k.key)} ${k.mode === 'major' ? 'major' : 'minor'}` };
        plans.set(id, out);
        return out;
      })
      .finally(() => loading.delete(id));
    loading.set(id, req);
    return req;
  }

  /* Neighbouring keys differ by one note -- or by none, for the relative major/minor,
     which is the same seven notes with a different home. Only keys on the list, so a
     wander never lands somewhere with six flats. */
  function neighbours(id) {
    const k = KEYS.find((x) => x.id === id);
    const t = pcOf(k.key);
    const major = k.mode === 'major';
    const want = [
      { pc: major ? (t + 9) % 12 : (t + 3) % 12, mode: major ? 'natural_minor' : 'major', w: 3 },
      { pc: (t + 7) % 12, mode: k.mode, w: 1 },
      { pc: (t + 5) % 12, mode: k.mode, w: 1 },
    ];
    return want.map((x) => ({ ...x, k: KEYS.find((c) => pcOf(c.key) === x.pc && c.mode === x.mode) }))
      .filter((x) => x.k);
  }

  const modeOf = (p) => (p.mode === 'major' ? 'major' : 'minor');

  function spell(roman, p) {
    // The palette's minor V is the scale's v with its third raised: E G B -> E G# B.
    const c = p.chords[DEGREE[roman]];
    let names = c.names.slice(0, 3);
    let symbol = c.symbol;
    if (roman === 'V' && modeOf(p) === 'minor') {
      const third = names[1];
      names = [names[0], third.endsWith('b') ? third.slice(0, -1) : third + '#', names[2]];
      symbol = names[0];
    }
    const byPc = new Map(names.map((n) => [pcOf(n), n]));
    return { names, symbol: pretty(symbol), byPc };
  }

  /* Everything about one chord that the drawing, the tip and the counting need, fixed
     at the moment it is shaped -- including the key it belongs to, so a wander that
     changes key while this chord is still sounding cannot respell or recolour it. */
  function shape(chord, prevRh, prevLh, p = plan) {
    const rh = voiceRight(chord.pcs, prevRh);
    const lhRoot = voiceLeft(chord.pcs[0], prevLh);
    const lh = leftKeys(lhRoot, opt.lh, rh[0]);
    // The scale a held key is judged against. Over minor V the raised third replaces
    // the natural seventh -- G sharp is the chord, so G natural is the clash.
    const scale = p.pcs.slice();
    if (chord.roman === 'V' && modeOf(p) === 'minor') scale[6] = chord.pcs[1];
    return { chord, rh, lhRoot, lh, plan: p, scale, ...spell(chord.roman, p) };
  }

  /* ── the clock ─────────────────────────────────────────────────────────── */
  const pace = () => PACES.find((p) => p.id === opt.pace);
  const elapsed = () => ((playing ? performance.now() : pausedAt) - startedAt) / 1000;

  function tick() {
    if (destroyed) return;
    raf = requestAnimationFrame(tick);
    // A song on the roll owns the keyboard. Step aside once, rather than paint over it.
    if (ghostArmed()) {
      if (!yielded) { pause(); release(); yielded = true; }
      return;
    }
    if (!playing) return;
    const secs = pace().secs;
    const t = elapsed();
    if (secs) {
      const frac = Math.min(1, t / secs);
      paintClock(frac, secs);
      setPreview(t >= secs - previewWindow(secs));
      if (t >= secs) advance();
    } else {
      paintClock(caught.size >= 2 ? 1 : 0, 0);
      setPreview(caught.size >= 2);
      if (advanceAt && performance.now() >= advanceAt) advance();
    }
  }

  // The last beat, and never less than a second: long enough to see where you are going.
  const previewWindow = (secs) => Math.min(secs / 2, Math.max(1, secs / BEATS));

  function paintClock(frac, secs) {
    const lit = secs ? Math.min(BEATS, Math.floor(frac * BEATS) + 1) : (frac ? BEATS : 0);
    [...el.beats.children].forEach((b, i) => b.classList.toggle('is-on', i < lit));
    el.bar.firstChild.style.width = (frac * 100).toFixed(1) + '%';
  }

  let previewing = false;
  function setPreview(on) {
    if (on === previewing) return;
    previewing = on;
    paintKeys();
  }

  /* ── moving through the music ──────────────────────────────────────────── */
  function advance() {
    if (now) {
      tally.chords++;
      if (caught.size >= 2) tally.hit++;
    }
    const prev = now;
    now = next && prog.peek() === next.chord
      ? (prog.next(), shape(next.chord, prev?.rh, prev?.lhRoot, next.plan))
      : shape(prog.next(), prev?.rh, prev?.lhRoot);
    // A wander is decided while shaping what comes NEXT, so the outline you are shown
    // is the chord you will actually get -- the first chord of the new key.
    const upcoming = prog.peek();
    if (opt.wander && upcoming.step === 0 && upcoming.pass === 1) {
      phrasesInKey++;
      if (phrasesInKey >= 3 && Math.random() < 0.5) wander();
    }
    next = shape(prog.peek(), now.rh, now.lhRoot);
    caught = new Set();
    advanceAt = 0;
    startedAt = performance.now();
    previewing = false;
    paintChord();
    sendPad();
  }

  function wander() {
    const choices = neighbours(opt.key).filter((x) => plans.has(x.k.id));
    if (!choices.length) return;
    const total = choices.reduce((a, x) => a + x.w, 0);
    let r = Math.random() * total;
    const pick = choices.find((x) => (r -= x.w) < 0) || choices[0];
    const before = new Set(plan.pcs);
    plan = plans.get(pick.k.id);
    opt.key = pick.k.id;
    keySelect.value = opt.key;
    phrasesInKey = 0;
    prog = createProgression({ mode: modeOf(plan), scale: plan.pcs });
    const added = plan.pcs.filter((pc) => !before.has(pc));
    const names = plan.chords.flatMap((c) => c.names);
    const what = added.length
      ? `new note: ${added.map((pc) => pretty(names.find((n) => pcOf(n) === pc) || '')).join(', ')}`
      : 'same notes, new home';
    toast(`Next: moving to ${plan.title} -- ${what}`, 'good');
    prefetchNeighbours();
  }

  function prefetchNeighbours() {
    for (const x of neighbours(opt.key)) loadPlan(x.k.id).catch(() => {});
  }

  async function setKey(id, { quiet = false } = {}) {
    opt.key = id;
    save();
    let p;
    try {
      p = await loadPlan(id);
    } catch (err) {
      toast('could not load that key: ' + err.message, 'bad');
      return;
    }
    if (destroyed || opt.key !== id) return;     // gone, or another pick overtook this one
    plan = p;
    phrasesInKey = 0;
    prog = createProgression({ mode: modeOf(plan), scale: plan.pcs });
    now = null;
    next = null;
    prefetchNeighbours();
    if (playing) advance();
    else if (!quiet) { release(); idle(); }
  }

  async function start(fresh = false) {
    if (starting || playing || destroyed) return;
    if (!plan) {
      starting = true;
      try { await setKey(opt.key, { quiet: true }); } finally { starting = false; }
    }
    if (!plan || playing || destroyed) return;
    onStart?.();
    ctx.kb.setMode('flow', true);
    yielded = false;
    playing = true;
    el.go.textContent = 'Pause';
    if (now && !fresh) {
      // Resume: both clocks move on by however long the pause was.
      const gap = performance.now() - pausedAt;
      startedAt += gap;
      if (advanceAt) advanceAt += gap;
      paintChord();
      sendPad();
    } else {
      advance();
    }
    if (!raf) raf = requestAnimationFrame(tick);
  }

  /* `quietly` is for panic, which silences the pad itself and reports doing so. */
  function pause(quietly = false) {
    if (!playing) return;
    playing = false;
    pausedAt = performance.now();
    el.go.textContent = 'Resume';
    el.status.textContent = 'paused';
    if (quietly) clearTimeout(padTimer);
    else stopPad();
  }

  /* Hand the keyboard back: something else is about to draw on it. */
  function release() {
    if (!drawn) return;
    drawn = false;
    ctx.kb?.clearMarks();
    ctx.kb?.clearLabels();
    ctx.kb?.setMode('flow', false);
  }

  function skip() {
    if (!prog) return;
    if (playing) advance();
    else start(true);
  }

  function newPhrase() {
    if (!prog) return;
    prog.skipPhrase();
    next = null;
    if (playing) advance();
    else start(true);
  }

  function restartClock() {
    startedAt = performance.now();
    pausedAt = startedAt;
    advanceAt = 0;
    previewing = false;
    if (now && drawn) paintKeys();
  }

  function revoice() {
    if (!now || !next) return;
    now = shape(now.chord, now.rh, now.lhRoot, now.plan);
    next = shape(next.chord, now.rh, now.lhRoot, next.plan);
    paintChord();
    if (playing) sendPad();
  }

  /* ── what you play ─────────────────────────────────────────────────────── */
  function frame(f) {
    if (!playing || !now || !f.on) return;
    const scale = new Set(now.scale);
    for (const [n] of f.on) {
      const pc = n % 12;
      if (now.chord.pcs.includes(pc)) { tally.chord++; caught.add(pc); }
      else if (scale.has(pc)) tally.key++;
      else tally.out++;
    }
    // Two different notes of the chord is "you found it": a full triad is a lot to ask
    // of a beginner's left hand, and one note could be anything.
    if (pace().secs === 0 && caught.size >= 2 && !advanceAt) advanceAt = performance.now() + 900;
    paintStats();
  }

  /* ── drawing ───────────────────────────────────────────────────────────── */
  function paintChord() {
    const n = now, x = next;
    const names = (ms, s) => ms.map((m) => pretty(s.byPc.get(m % 12))).join(' ');
    el.nowSym.textContent = n.symbol;
    el.nowRole.replaceChildren(role(n.chord));
    el.nowHands.replaceChildren(
      h('span', null, 'Right hand ', h('b', null, names(n.rh, n))),
      n.lh.length ? h('span', null, 'Left hand ', h('b', null, names(n.lh, n))) : null);
    el.nextSym.textContent = x.symbol;
    el.nextRole.replaceChildren(role(x.chord));
    el.tip.textContent = moveTip(n, x);

    const ph = n.chord.phrase;
    el.phraseName.textContent = `${ph.name}  ·  ${ph.seq.join(' – ')}`;
    el.phraseBlurb.textContent = ph.blurb;
    el.chips.replaceChildren(...ph.seq.map((r, i) => {
      const live = i === n.chord.step;
      const roman = live ? n.chord.roman : r;
      return h('span.flow__chip' + (live ? '.is-on' : '') + (live && n.chord.varied ? '.is-varied' : ''),
        { title: live && n.chord.varied ? 'swapped for a chord that shares two notes' : null },
        h('b', null, spell(roman, n.plan).symbol), h('small', null, roman));
    }), h('span.flow__pass', null, ph.seq.length >= 8 ? '' : `time ${n.chord.pass} of 2`));
    el.status.textContent = pace().secs ? '' : 'play two notes of the chord';
    paintKeys();
    paintStats();
  }

  function role(chord) {
    const [word, gloss] = ROLE_TEXT[chord.role];
    return h('span.flow__pill.flow__pill--' + chord.role, { title: ROMAN_HELP },
      `${chord.roman} · ${word}`, h('small', null, gloss));
  }

  /* Which fingers move. Said in notes, because "keep E and G down" is something a hand
     can do and "common tones" is not. Arrows only when each moving finger has a clear
     partner: if a held key changes place inside the shape, a pairing by pitch would
     send a finger across the one holding still, so that case says lift and add. */
  function moveTip(n, x) {
    const name = (m, s) => pretty(s.byPc.get(m % 12));
    const list = (ms, s) => ms.map((m) => name(m, s)).join(' and ');
    const keep = n.rh.filter((m) => x.rh.includes(m));
    const go = n.rh.filter((m) => !x.rh.includes(m));
    const to = x.rh.filter((m) => !n.rh.includes(m));
    const left = n.lh.length && x.lh.length && n.lh[0] !== x.lh[0]
      ? ` Left hand: ${n.lh.map((m) => name(m, n)).join(' ')} → ${x.lh.map((m) => name(m, x)).join(' ')}.`
      : '';
    if (!go.length) return left ? `Right hand stays.${left}` : 'Same chord again -- keep holding.';
    if (!keep.length) return `Right hand moves to ${x.rh.map((m) => name(m, x)).join(' ')}.${left}`;
    const crossed = keep.some((m) => n.rh.indexOf(m) !== x.rh.indexOf(m));
    const moves = crossed
      ? `lift ${list(go, n)}, add ${list(to, x)}`
      : `move ${go.map((m, i) => `${name(m, n)} → ${name(to[i], x)}`).join(', ')}`;
    return `Right hand: keep ${list(keep, n)} down, ${moves}.${left}`;
  }

  function paintKeys() {
    const kb = ctx.kb;
    if (!kb || !now) return;
    const lo = kb.low, hi = kb.high;
    const every = (pcs) => {
      const out = [];
      for (let m = lo; m <= hi; m++) if (pcs.includes(m % 12)) out.push(m);
      return out;
    };
    const target = [...now.lh, ...now.rh];
    kb.setMarks('scale', every(now.scale));
    kb.setMarks('tone', every(now.chord.pcs));
    kb.setMarks('target', target);
    kb.setMarks('next', previewing && next ? [...next.lh, ...next.rh] : []);
    kb.clearLabels();
    for (const m of target) kb.setKeyLabel(m, pretty(now.byPc.get(m % 12)));
    if (previewing && next) {
      for (const m of [...next.lh, ...next.rh]) {
        if (!target.includes(m)) kb.setKeyLabel(m, pretty(next.byPc.get(m % 12)));
      }
    }
    kb.setMode('flow', true);
    drawn = true;
  }

  function paintStats() {
    const all = tally.chord + tally.key + tally.out;
    el.stats.replaceChildren(all
      ? h('span', null,
        h('b.flow__ok', null, String(tally.chord)), ' chord notes · ',
        h('b', null, String(tally.key)), ' other notes from the key · ',
        h('b.flow__spicy', null, String(tally.out)), ' outside it',
        tally.chords ? ` · caught ${tally.hit} of ${tally.chords} chords` : '')
      : h('span', null, 'play anything lit -- your notes are counted here, not saved'));
  }

  function idle() {
    el.nowSym.textContent = '';
    el.nowRole.replaceChildren(h('span.flow__muted', null, 'Press Start when your hands are on the keys.'));
    el.nowHands.replaceChildren(h('span', null,
      'The keyboard lights up with a chord. Play the solid keys, or any tinted one -- they all fit.'));
    el.nextSym.textContent = '';
    el.nextRole.replaceChildren();
    el.tip.textContent = '';
    el.phraseName.textContent = plan ? plan.title : '';
    el.phraseBlurb.textContent = 'Each progression plays twice, then flows into another one. These are the chords songs are made of.';
    el.chips.replaceChildren();
    el.status.textContent = '';
    el.go.textContent = 'Start';
    paintClock(0, 0);
    paintStats();
  }

  /* ── the pad ───────────────────────────────────────────────────────────── */
  function sendPad() {
    clearTimeout(padTimer);
    if (!playing || !now) return;
    if (opt.sound === 'off') { stopPad(); return; }
    // The bass an octave under your left hand, the chord where your right hand is. On
    // its own channel, so it never fights you for a key.
    const notes = [now.lhRoot - 12, ...now.rh];
    api.post('/api/flow/backing', { notes, sound: opt.sound, volume: opt.volume, hold: 30 })
      .then(() => { padWarned = false; })
      .catch((err) => {
        // Once per failure, not once per chord: no engine means every chord would say so.
        if (!padWarned) toast('backing: ' + err.message, 'bad');
        padWarned = true;
      });
    // Renewed while a chord lasts longer than the pad's safety hold ("wait for me").
    padTimer = setTimeout(sendPad, 20000);
  }

  function stopPad() {
    clearTimeout(padTimer);
    api.post('/api/flow/backing', { notes: [] }).catch(() => {});
  }

  /* ── persistence ───────────────────────────────────────────────────────── */
  let saveTimer = 0;
  function save() {
    // Into the cached state too, or coming back to this tab rebuilds the panel from
    // the settings as they were when the app opened.
    const s = ctx.state?.settings;
    if (s) s.ui = { ...(s.ui || {}), flow: { ...opt } };
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => api.post('/api/settings', { ui: { flow: { ...opt } } }).catch(() => {}), 400);
  }

  // Esc. The server's panic silences the pad and reports it, so this only stops the
  // clock -- returning true here would make the app report a stopped video.
  const offPanic = onPanic(() => { pause(true); return false; });

  loadPlan(opt.key).then((p) => {
    if (destroyed || plan) return;
    plan = p;
    prog = createProgression({ mode: modeOf(plan), scale: plan.pcs });
    prefetchNeighbours();
    if (!now) idle();
  }).catch(() => {});
  idle();

  return {
    el: root,
    frame,
    /* The 1 Hz status frame: re-assert the marks, which a keyboard range change wipes. */
    status() { if (playing) paintKeys(); },
    /* Pause and hand the keyboard back -- an exercise is about to draw on it. A plain
       pause leaves the chord lit, so you can study it. */
    stop() {
      pause();
      release();
    },
    get playing() { return playing; },
    destroy() {
      if (destroyed) return;
      pause();
      destroyed = true;
      cancelAnimationFrame(raf);
      raf = 0;
      offPanic();
      release();
    },
  };
}
