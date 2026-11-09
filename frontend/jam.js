/* Jam -- the band plays the chords, you play the tune.
 *
 * Keys plays a backing band (backend/band.py: drums, bass and a comping keyboard)
 * through progressions that real songs use. A one-octave strip (and the keyboard under
 * it) lights the notes that sound good over the chord playing now: the chord's notes
 * bright, the notes that add colour dim, nothing lit for a note that rubs (harmony.js
 * palette()). The set holds still for the whole chord -- you play, it does not chase you.
 *
 * Two beats before the chord changes, it shows the way across: the next chord outlined,
 * the notes the two chords share marked "both", and one key pulsing where to be when it
 * changes (harmony.js crossing()). That is decided once per change, from the last note
 * you played, so it never jumps under your fingers.
 *
 * Who owns what. harmony.js composes; this file turns chords into a chart of bars and
 * keeps the band's copy topped up; the band owns the clock. Where the band is comes in
 * on the 1 Hz status frame and is interpolated in between, so the keys change on the bar
 * line you hear, not on a timer of our own that would drift away from it.
 *
 * Spelling comes from the server (/api/theory/scale): the frontend owns no mode table.
 * Nothing here is graded or saved; the counts are for this sitting only.
 */

import { api, h, info, mod, slider, toast, toggle } from './ui.js';
import { ghostArmed, onPanic } from './app.js';
import { createProgression, crossing, palette } from './harmony.js';

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
const STYLES = [
  { id: 'ballad', label: 'Ballad', bpm: 72 },
  { id: 'pop', label: 'Pop', bpm: 96 },
  { id: 'lofi', label: 'Lo-fi', bpm: 80 },
  { id: 'rock', label: 'Rock', bpm: 112 },
];
const LENGTHS = [{ id: 1, label: '1 bar' }, { id: 2, label: '2 bars' }];
const PC = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };
const pcOf = (name) => (PC[name[0]] + (name.match(/#/g) || []).length
  - (name.slice(1).match(/b/g) || []).length + 12) % 12;
const BLACK = new Set([1, 3, 6, 8, 10]);
const pretty = (s) => s.replace(/#/g, '♯').replace(/(?<=[A-G])b/g, '♭');
const DEGREE = { I: 0, ii: 1, iii: 2, IV: 3, V: 4, vi: 5, i: 0, III: 2, iv: 3, v: 4, VI: 5, VII: 6 };
const ROLE_TEXT = { home: 'Home', away: 'Away', pull: 'Pull' };
const ROMAN_HELP = 'The numbers count up the key: I is the chord built on its 1st note, '
  + 'IV on its 4th, V on its 5th. Capitals are major chords (brighter), small letters minor (sadder).';
const BEATS = 4;
const WARN = 0.5;          // bars before a change that the way across is shown (2 beats)
const AHEAD = 12;          // bars of chart the band always has in hand
const LEAD_MS = 200;       // the band's lead-in before bar 0 (metronome LEAD_IN_MS + 80)
const DEFAULTS = { key: 'C-major', style: 'pop', bpm: 96, bars: 1, volume: 0.7, drums: true,
  coach: true, wander: false };

export function createJam(ctx, { onStart } = {}) {
  const saved = ctx.state?.settings?.ui?.jam || {};
  const opt = { ...DEFAULTS, ...saved };
  if (!KEYS.some((k) => k.id === opt.key)) opt.key = DEFAULTS.key;
  if (!STYLES.some((s) => s.id === opt.style)) opt.style = DEFAULTS.style;

  const plans = new Map();     // key id -> loaded plan
  const loading = new Map();   // key id -> its request, so two callers share one
  let plan = null;
  let prog = null;
  let chart = [];              // bar -> entry (see entry()); an entry spans opt.bars bars
  let playing = false, starting = false, destroyed = false;
  let drawn = false, yielded = false;
  let clock = null;            // { pos, at, barMs }: bar `pos` was heard at performance.now() `at`
  let shownBar = -1, shownCross = '';
  let raf = 0;
  let phrasesInKey = 0;
  let lastNote = null;         // the last note you played (MIDI)
  let cross = null;            // the change ahead: { bar, to, keep, land, why, hit }
  let ahead = false;           // inside the two beats before that change
  let heard = null;            // the last note you played and how it fit
  const tally = { chord: 0, color: 0, rub: 0, out: 0, landed: 0 };

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

  const keySelect = h('select.jam__select', { onchange: (e) => setKey(e.target.value) },
    KEYS.map((k) => h('option', { value: k.id, selected: k.id === opt.key }, keyLabel(k))));
  const bpmValue = h('span.field__value', null, `${opt.bpm}`);
  const bpm = slider({
    min: 50, max: 160, value: opt.bpm,
    oninput: (v) => { bpmValue.textContent = `${v}`; },
    onchange: (v) => { opt.bpm = v; save(); if (playing) configure({ bpm: v }); },
  });
  const volValue = h('span.field__value', null, `${Math.round(opt.volume * 100)}%`);
  const volume = slider({
    min: 0, max: 100, value: Math.round(opt.volume * 100),
    oninput: (v) => { volValue.textContent = `${v}%`; },
    onchange: (v) => { opt.volume = v / 100; save(); if (playing) configure({ volume: opt.volume }); },
  });
  const styleSeg = seg(STYLES, opt.style, (id) => {
    opt.style = id;
    opt.bpm = STYLES.find((s) => s.id === id).bpm;
    bpm.value = opt.bpm; bpm.dispatchEvent(new Event('input'));
    save();
    if (playing) configure({ style: id, bpm: opt.bpm });
  });

  const el = {
    go: h('button.btn.btn--lg.btn--primary.jam__go', { onclick: () => (playing ? stop() : start()) }, 'Start the band'),
    nowSym: h('div.jam__sym'), nowRole: h('div.jam__role'),
    nextSym: h('div.jam__sym.jam__sym--next'), nextRole: h('div.jam__role'),
    beats: h('div.jam__beats', null, Array.from({ length: BEATS }, () => h('span.jam__beat'))),
    oct: h('div.strip'),
    coach: h('div.jam__coach'),
    heard: h('div.jam__heard'),
    phrase: h('div.jam__phrase', { title: ROMAN_HELP }),
    stats: h('div.jam__stats'),
  };

  const root = h('div.col-12', null, mod('Jam', 'the band plays, you solo',
    info('Press ', h('strong', null, 'Start the band'), ' and Keys plays drums, bass and chords. ',
      'The strip is one octave of the piano, and what it lights is true in every octave: play it ',
      'high, low, or both hands at once. ', h('strong', null, 'Bright'), ' keys are the chord: rest ',
      'on them. ', h('strong', null, 'Dim'), ' keys add colour: move through them between the bright ',
      'ones. A key with no light rubs against this chord. The set stays put until the chord changes.'),
    info('Two beats before a change, the strip shows the way across. ', h('strong', null, 'Outlined'),
      ' keys are the next chord, ', h('strong', null, 'both'), ' marks a note the two chords share, and ',
      'the ', h('strong', null, 'pulsing'), ' key is where to be when it changes. Holding a shared ',
      'note, or stepping to the next chord\'s nearest note, is how good players make a change sound ',
      'smooth.'),
    info('The progressions are the ones songs are built on (the pop four, the fifties, ii–V–I, ',
      'Pachelbel...), each played twice, then flowing into the next. ', h('strong', null, 'Wander'),
      ' moves to a neighbouring key now and then, a key that changes only one note.'),
    h('div.jam', null,
      h('div.jam__top', null,
        el.go,
        h('button.btn.btn--lg.jam__focus', { onclick: () => ctx.toggleFocus?.(undefined, root),
          title: 'Just Jam and the keys, full screen (G). Esc comes back; the band keeps playing.' }, 'Focus'),
        h('label.jam__opt', null, h('span.jam__lbl', null, 'Key'), keySelect),
        h('div.jam__opt', null, h('span.jam__lbl', null, 'Style'), styleSeg),
        h('div.jam__opt', null, h('span.jam__lbl', null, 'Each chord'),
          seg(LENGTHS, opt.bars, (id) => { opt.bars = id; save(); if (playing) rechart(); }))),

      h('div.jam__stage', null,
        h('div.jam__card', null, h('div.jam__lbl', null, 'Now'), el.nowSym, el.nowRole),
        h('div.jam__mid', null, el.beats, el.oct, el.coach, el.heard),
        h('div.jam__card.jam__card--next', null, h('div.jam__lbl', null, 'Next'), el.nextSym, el.nextRole)),

      el.phrase,

      h('div.jam__mix', null,
        h('label.field.jam__field', null, h('span.field__label', null, h('span', null, 'Tempo'), bpmValue), bpm),
        h('label.field.jam__field', null, h('span.field__label', null, h('span', null, 'Band volume'), volValue), volume),
        toggle('Drums', opt.drums, (on) => { opt.drums = on; save(); if (playing) configure({ drums: on }); }),
        toggle('Show the change', opt.coach, (on) => { opt.coach = on; save(); shownCross = '?'; }),
        toggle('Wander', opt.wander, (on) => { opt.wander = on; save(); }),
        h('button.btn', { onclick: () => newPhrase() }, 'New progression')),
      el.stats)));

  function keyLabel(k) {
    const name = `${pretty(k.key)} ${k.mode === 'major' ? 'major' : 'minor'}`;
    const black = scalePcsGuess(k).filter((pc) => BLACK.has(pc)).length;
    const keys = black === 0 ? 'all white keys' : black === 5 ? 'all five black keys + 2 white'
      : `${black} black key${black === 1 ? '' : 's'}`;
    return `${name} — ${keys}${k.note ? ` (${k.note})` : ''}`;
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

  /* Neighbouring keys differ by one note -- or by none, for the relative major/minor. */
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
    return { symbol: pretty(symbol), byPc: new Map(names.map((n) => [pcOf(n), n])) };
  }

  /* One chord, with everything the drawing and the coach need, fixed when it is charted
     -- including its key, so a wander cannot respell a chord already on its way. The
     scale it is judged against: over minor V the raised third replaces the natural
     seventh, because G sharp is the chord and G natural is the clash. */
  function entry(chord, p = plan) {
    const scale = p.pcs.slice();
    if (chord.roman === 'V' && modeOf(p) === 'minor') scale[6] = chord.pcs[1];
    const sp = spell(chord.roman, p);
    const byPc = new Map(sp.byPc);
    for (const pc of scale) if (!byPc.has(pc)) byPc.set(pc, p.chords.flatMap((c) => c.names).find((n) => pcOf(n) === pc));
    return { chord, plan: p, scale, symbol: sp.symbol, byPc, pal: palette(chord.pcs, scale) };
  }

  /* The next `n` bars of music, continuing the progression. A wander is decided at a
     phrase boundary, so the new key starts on a phrase's first chord. */
  function compose(n) {
    const out = [];
    while (out.length < n) {
      const upcoming = prog.peek();
      if (opt.wander && upcoming.step === 0 && upcoming.pass === 1) {
        phrasesInKey++;
        if (phrasesInKey >= 3 && Math.random() < 0.5) wander();
      }
      const e = entry(prog.next());
      for (let i = 0; i < opt.bars; i++) out.push(e);
    }
    return out;
  }

  const wire = (bars) => bars.map((e) => ({ root: e.chord.pcs[0], pcs: e.chord.pcs, bass: e.chord.pcs[0] }));

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
    toast(`Coming up: ${plan.title} — ${what}`, 'good');
    prefetchNeighbours();
  }

  function prefetchNeighbours() {
    for (const x of neighbours(opt.key)) loadPlan(x.k.id).catch(() => {});
  }

  async function setKey(id) {
    opt.key = id;
    save();
    let p;
    try {
      p = await loadPlan(id);
    } catch (err) {
      toast('could not load that key: ' + err.message, 'bad');
      return;
    }
    if (destroyed || opt.key !== id) return;
    plan = p;
    phrasesInKey = 0;
    prog = createProgression({ mode: modeOf(plan), scale: plan.pcs });
    prefetchNeighbours();
    if (playing) rechart(true);
    else idle();
  }

  /* ── the band ──────────────────────────────────────────────────────────── */
  async function start() {
    if (starting || playing || destroyed) return;
    starting = true;
    try {
      if (!plan) await setKey(opt.key);
      if (!plan || destroyed) return;
      onStart?.();
      chart = compose(AHEAD + 4);
      const r = await api.post('/api/band/start', {
        chart: wire(chart), style: opt.style, bpm: opt.bpm, volume: opt.volume, drums: opt.drums,
      });
      if (destroyed) { api.post('/api/band/stop').catch(() => {}); return; }
      clock = { pos: -LEAD_MS / r.bar_ms, at: performance.now(), barMs: r.bar_ms };
      playing = true;
      yielded = false;
      lastNote = null;
      cross = null;
      ahead = false;
      heard = null;
      shownBar = -1;
      el.go.textContent = 'Stop';
      el.go.classList.remove('btn--primary');
      ctx.kb.setMode('flow', true);
      if (!raf) raf = requestAnimationFrame(tick);
    } catch (err) {
      toast('the band could not start: ' + err.message, 'bad');
    } finally {
      starting = false;
    }
  }

  function stop(quietly = false) {
    if (!playing) return;
    playing = false;
    if (!quietly) api.post('/api/band/stop').catch(() => {});
    release();
    idle();
  }

  function configure(patch) {
    api.post('/api/band/config', patch)
      .then((r) => { if (playing && r.pos != null) clock = { pos: r.pos, at: performance.now(), barMs: r.bar_ms }; })
      .catch((err) => toast('band: ' + err.message, 'bad'));
  }

  /* Replace the music from the next bar the band has not queued yet. `fresh` starts a
     new progression (a new key, or "new progression"); otherwise the same chords are
     re-laid at a new length. */
  async function rechart(fresh = false) {
    if (!playing) return;
    const bar = Math.max(0, Math.floor(position()) + 1);
    let bars;
    if (fresh) {
      bars = compose(AHEAD);
    } else {
      const seen = [];
      for (const e of chart.slice(bar)) if (seen[seen.length - 1] !== e) seen.push(e);
      bars = seen.flatMap((e) => Array(opt.bars).fill(e));
    }
    try {
      const r = await api.post('/api/band/chart', { from: bar, chart: wire(bars) });
      chart = chart.slice(0, r.from).concat(bars);
      while (chart.length < r.from) chart.push(chart[chart.length - 1]);
      // A way across worked out for the old chart may point at a chord that is gone.
      if (cross && cross.bar >= r.from) cross = null;
      shownBar = -1;
    } catch (err) {
      toast('band: ' + err.message, 'bad');
    }
  }

  function newPhrase() {
    if (!prog) return;
    prog.skipPhrase();
    if (playing) rechart(true);
    else start();
  }

  /* Keep the band's copy of the chart AHEAD bars past where you are. */
  let topping = false;
  function topUp(bar) {
    if (topping || chart.length > bar + AHEAD) return;
    topping = true;
    const from = chart.length;
    const more = compose(8);
    api.post('/api/band/chart', { from, chart: wire(more) })
      .then((r) => { chart = chart.slice(0, r.from).concat(more); })
      .catch(() => {})
      .finally(() => { topping = false; });
  }

  const position = () => (clock ? clock.pos + (performance.now() - clock.at) / clock.barMs : -1);

  /* ── the clock ─────────────────────────────────────────────────────────── */
  function tick() {
    if (destroyed) return;
    raf = requestAnimationFrame(tick);
    if (!playing) return;
    // A song on the roll owns the keyboard. Step aside once rather than paint over it.
    if (ghostArmed()) {
      if (!yielded) { stop(); yielded = true; }
      return;
    }
    const pos = position();
    if (pos < 0) { paintBeats(-1); return; }
    const bar = Math.floor(pos);
    paintBeats(Math.floor((pos - bar) * BEATS));
    topUp(bar);
    track(pos, bar);
    const key = cross ? `${cross.bar}${ahead ? '<' : '>'}` : '';
    if (bar !== shownBar || key !== shownCross) {
      const changed = bar !== shownBar && chart[bar] !== chart[shownBar];
      shownBar = bar;
      shownCross = key;
      if (changed) paintChord();
      paintCoach();
      paintKeys();
    }
  }

  /* The change ahead. The way across is worked out once, when the two-beat warning
     starts, and kept through the first beat of the new chord -- the beat you land on. */
  function track(pos, bar) {
    if (!opt.coach) { cross = null; ahead = false; return; }
    let k = bar + 1;
    while (k < chart.length && chart[k] === chart[bar]) k++;
    const to = chart[k];
    if (to && k - pos <= WARN && (!cross || cross.bar !== k)) {
      const c = crossing(chart[bar].pal.chord, to.pal.chord, lastNote,
        { low: ctx.kb?.low ?? 21, high: ctx.kb?.high ?? 108 });
      cross = { bar: k, to, ...c, hit: false };
    }
    if (cross && pos >= cross.bar + 1 / BEATS) cross = null;
    ahead = !!cross && pos < cross.bar;
  }

  function paintBeats(beat) {
    [...el.beats.children].forEach((b, i) => {
      b.classList.toggle('is-on', i === beat);
      b.classList.toggle('is-down', i === 0 && beat === 0);
    });
  }

  /* ── what you play ─────────────────────────────────────────────────────── */
  function frame(f) {
    if (!playing || !f.on) return;
    const now = chart[Math.max(0, shownBar)];
    if (!now) return;
    for (const [n] of f.on) {
      const pc = n % 12;
      const fit = now.pal.chord.includes(pc) ? 'chord' : now.pal.color.includes(pc) ? 'color'
        : now.scale.includes(pc) ? 'rub' : 'out';
      tally[fit]++;
      if (cross && !cross.hit && pc === cross.land % 12) { cross.hit = true; tally.landed++; }
      heard = { n, fit, name: pretty(now.byPc.get(pc) || noteName(n)) };
      lastNote = n;
      flash(pc, fit);
    }
    paintHeard();
    paintStats();
  }

  const NAMES = ['C', 'C#', 'D', 'Eb', 'E', 'F', 'F#', 'G', 'Ab', 'A', 'Bb', 'B'];
  const noteName = (n) => NAMES[n % 12];

  /* ── drawing ───────────────────────────────────────────────────────────── */

  /* One octave, C to B, built once; painting only flips classes and labels. */
  const WHITE = [0, 2, 4, 5, 7, 9, 11];
  const TONE_ROLE = ['root', '3rd', '5th'];
  const octKeys = Array.from({ length: 12 }, (_, pc) => {
    const black = BLACK.has(pc);
    const k = h('div.strip__key' + (black ? '.strip__key--black' : ''), null,
      h('span.strip__both', null, 'both'), h('span.strip__name'), h('span.strip__role'));
    if (black) k.style.left = `calc(${((WHITE.indexOf(pc - 1) + 1) * 100) / 7}% - var(--strip-bw) / 2)`;
    // Drop the flash once it has played, or its finished animation would hold the slot
    // the landing pulse needs.
    k.addEventListener('animationend', (e) => { if (e.animationName === 'strip-hit') k.classList.remove('is-hit'); });
    el.oct.append(k);
    return k;
  });

  function paintOctave() {
    const now = playing ? chart[Math.max(0, shownBar)] : null;
    const next = ahead ? cross.to : null;
    octKeys.forEach((k, pc) => {
      const tone = now ? now.pal.chord.indexOf(pc) : -1;
      const color = !!now && now.pal.color.includes(pc);
      const inNext = !!next && next.pal.chord.includes(pc);
      k.classList.toggle('is-chord', tone >= 0);
      k.classList.toggle('is-color', color);
      k.classList.toggle('is-next', inNext);
      k.classList.toggle('is-keep', !!next && cross.keep.includes(pc));
      k.classList.toggle('is-land', !!cross && cross.land % 12 === pc);
      const named = !now || tone >= 0 || color || inNext;
      k.children[1].textContent = named
        ? pretty((inNext && next.byPc.get(pc)) || now?.byPc.get(pc) || NAMES[pc]) : '';
      k.children[2].textContent = tone >= 0 ? TONE_ROLE[tone] : '';
    });
  }

  /* What you just played, on the strip: a short flash in the colour of how it fit. */
  function flash(pc, fit) {
    const k = octKeys[pc];
    k.dataset.fit = fit;
    k.classList.remove('is-hit');
    void k.offsetWidth;          // restart the animation on a repeated note
    k.classList.add('is-hit');
  }

  function paintChord() {
    const now = chart[shownBar];
    if (!now) return;
    let k = shownBar + 1;
    while (chart[k] === now) k++;
    const nxt = chart[k];
    el.nowSym.textContent = now.symbol;
    el.nowRole.replaceChildren(role(now.chord));
    el.nextSym.textContent = nxt ? nxt.symbol : '';
    el.nextRole.replaceChildren(nxt ? role(nxt.chord) : '');
    const ph = now.chord.phrase;
    el.phrase.replaceChildren(
      h('span.jam__pname', null, ph.name),
      ...ph.seq.map((r, i) => {
        const live = i === now.chord.step;
        const roman = live ? now.chord.roman : r;
        return h('span.jam__chip' + (live ? '.is-on' : ''), null,
          h('b', null, spell(roman, now.plan).symbol), h('small', null, roman));
      }),
      h('span.jam__pblurb', null, ph.blurb));
  }

  function role(chord) {
    return h('span.jam__pill.jam__pill--' + chord.role, { title: ROMAN_HELP },
      `${chord.roman} · ${ROLE_TEXT[chord.role]}`);
  }

  function paintCoach() {
    const now = chart[Math.max(0, shownBar)];
    if (!playing || !now) return;
    const say = (lead, name, why) => el.coach.replaceChildren(
      h('span.jam__try', null, lead, h('b', null, name)), h('span.jam__why', null, why));
    if (cross) {
      const name = pretty(cross.to.byPc.get(cross.land % 12) || noteName(cross.land));
      const kept = cross.keep.includes(cross.land % 12);
      if (ahead) say(kept ? 'Hold ' : 'Head for ', name, `${cross.to.symbol} is next: ${cross.why}.`);
      else say('Land on ', name, `${cross.to.symbol}, right on the change.`);
      return;
    }
    say('Rest on ', now.pal.chord.map((pc) => pretty(now.byPc.get(pc) || NAMES[pc])).join(' · '),
      'Every lit key sounds good. Bright is the chord, dim adds colour.');
  }

  function paintHeard() {
    if (!heard) { el.heard.replaceChildren(); return; }
    const words = { chord: 'in the chord', color: 'adds colour', rub: 'rubs against this chord: step off it',
      out: 'outside the key — spicy' };
    el.heard.replaceChildren(h('span.jam__fit.jam__fit--' + heard.fit, null,
      `You played ${heard.name}: ${words[heard.fit]}`));
  }

  function paintKeys() {
    paintOctave();
    const kb = ctx.kb;
    const now = chart[Math.max(0, shownBar)];
    if (!kb || !now || !playing) return;
    const every = (pcs) => {
      const out = [];
      for (let m = kb.low; m <= kb.high; m++) if (pcs.includes(m % 12)) out.push(m);
      return out;
    };
    kb.setMarks('scale', every(now.pal.color));
    kb.setMarks('tone', every(now.pal.chord));
    kb.setMarks('suggest', cross ? [cross.land] : []);
    kb.setMarks('suggest2', ahead ? every(cross.to.pal.chord) : []);
    kb.clearLabels();
    if (cross) kb.setKeyLabel(cross.land, pretty(cross.to.byPc.get(cross.land % 12) || noteName(cross.land)));
    kb.setMode('flow', true);
    drawn = true;
  }

  function paintStats() {
    const all = tally.chord + tally.color + tally.rub + tally.out;
    const s = (n) => (n === 1 ? '' : 's');
    el.stats.replaceChildren(all
      ? h('span', null,
        h('b.jam__ok', null, String(tally.chord)), ` chord note${s(tally.chord)} · `,
        h('b', null, String(tally.color)), ' colour · ',
        h('b.jam__rub', null, String(tally.rub)), ` rub${s(tally.rub)} · `,
        h('b.jam__spicy', null, String(tally.out)), ' spicy',
        opt.coach ? ` · ${tally.landed} change${s(tally.landed)} landed` : '')
      : h('span', null, 'Counted for this sitting only, never saved.'));
  }

  /* Hand the keyboard back: something else is about to draw on it. */
  function release() {
    if (!drawn) return;
    drawn = false;
    ctx.kb?.clearMarks();
    ctx.kb?.clearLabels();
    ctx.kb?.setMode('flow', false);
  }

  function idle() {
    el.go.textContent = 'Start the band';
    el.go.classList.add('btn--primary');
    el.nowSym.textContent = plan ? pretty(plan.key) : '';
    el.nowRole.replaceChildren(h('span.jam__muted', null, plan ? plan.title : ''));
    el.nextSym.textContent = '';
    el.nextRole.replaceChildren();
    el.coach.replaceChildren(h('span.jam__muted', null,
      'Start the band, then play any lit key, in any octave.'));
    el.heard.replaceChildren();
    el.phrase.replaceChildren();
    cross = null;
    ahead = false;
    paintOctave();
    paintBeats(-1);
    paintStats();
  }

  /* ── persistence ───────────────────────────────────────────────────────── */
  let saveTimer = 0;
  function save() {
    // Into the cached state too, or coming back to this tab rebuilds the panel from
    // the settings as they were when the app opened.
    const s = ctx.state?.settings;
    if (s) s.ui = { ...(s.ui || {}), jam: { ...opt } };
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => api.post('/api/settings', { ui: { jam: { ...opt } } }).catch(() => {}), 400);
  }

  // Esc. The server's panic stops the band and reports it, so this only stops drawing
  // -- returning true here would make the app report a stopped video.
  const offPanic = onPanic(() => { stop(true); return false; });

  loadPlan(opt.key).then((p) => {
    if (destroyed || plan) return;
    plan = p;
    prog = createProgression({ mode: modeOf(plan), scale: plan.pcs });
    prefetchNeighbours();
    if (!playing) idle();
  }).catch(() => {});
  idle();

  return {
    el: root,
    frame,
    /* The 1 Hz status frame: where the band really is, and a re-assert of the marks,
       which a keyboard range change wipes. */
    status(s) {
      const b = s?.band;
      if (playing && b) {
        if (!b.running) { stop(true); return; }
        if (b.pos != null) {
          const mine = position();
          // Small disagreements are frame jitter; a real one (a tempo change, a hitch) wins.
          if (Math.abs(mine - b.pos) * b.bar_ms > 25 || clock.barMs !== b.bar_ms) {
            clock = { pos: b.pos, at: performance.now(), barMs: b.bar_ms };
          }
        }
        paintKeys();
      }
    },
    /* Stop and hand the keyboard back -- an exercise is about to draw on it. */
    stop() { stop(); },
    /* The dock changed size (focus): put the marks straight back. */
    repaint() { if (playing) paintKeys(); },
    get playing() { return playing; },
    destroy() {
      if (destroyed) return;
      stop();
      destroyed = true;
      cancelAnimationFrame(raf);
      raf = 0;
      offPanic();
      release();
    },
  };
}
