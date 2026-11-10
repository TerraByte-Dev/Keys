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
 * You can also take a seat in the band (Seat: Solo | Bass | Chords). The band drops that
 * part and the keys light what its player would play: the root low down, or the chord's
 * notes as a set across the middle -- never one shape to copy. The seat decides which
 * notes are the target; the guide decides how much of them is drawn. Both go through
 * one paint path (harmony.js seatTarget / seatZone, then paintKeys / paintOctave).
 *
 * The guide can be turned down (harmony.js guideLevel): Fading takes the lights away
 * a step each time through a progression, Dark plays by ear from the start. The marks
 * stay on the keys either way and only stop being drawn, because the keyboard says how
 * a held note fit by reading them -- see kb--dark in keyboard.js.
 *
 * Spelling comes from the server (/api/theory/scale): the frontend owns no mode table.
 * Nothing here is graded or saved; the counts are for this sitting only.
 *
 * What the band plays is the Play setting, and every one of them goes through the same
 * chart, strip, guide and seat:
 *   Songs   the progressions above.
 *   Modal   a two-chord vamp in a mode, with the note that makes the mode ringed.
 *   Blues   the 12-bar form, judged against the blues rather than the key, so the blue
 *           note (ringed) counts as colour instead of "outside".
 *   Answer  the band plays a two-bar question; you answer for two and end at home.
 *   Echo    the band plays a one-bar lick; you play it back in the next bar. The licks
 *           grow a note after two clean echoes and shrink one after two misses.
 * Answer and Echo are the band's own melody, on a lead voice (band.py). Each charted
 * entry can carry `lead` and `turns` per bar it spans: who plays that bar, the band
 * ('call') or you ('you'). Notes you play in your bars are collected on the entry's
 * `ask`, one object per exchange, and judged once your bars are over.
 */

import { api, focusBtn, h, info, mod, slider, toast, toggle } from './ui.js';
import { ghostArmed, onPanic } from './app.js';
import {
  ROOMS, bluesChord, bluesNote, bluesScale, createAsks, createBlues, createProgression, createVamp,
  crossing, echoScore, guideLevel, keysIn, lick, palette, question, roomScale, roomSig, seatTarget, seatZone,
} from './harmony.js';

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
  { id: 'shuffle', label: 'Shuffle', bpm: 84 },
];
const MODES = [{ id: 'songs', label: 'Songs' }, { id: 'modal', label: 'Modal' }, { id: 'blues', label: 'Blues' },
  { id: 'answer', label: 'Answer' }, { id: 'echo', label: 'Echo' }];
// Where the band plays its own melody, there is no seat to take: you are the other soloist.
const DUET = new Set(['answer', 'echo']);
// A note played this much of a bar early counts toward the bar it is reaching for.
const EARLY = 0.06;
const FLATS = ['C', 'Db', 'D', 'Eb', 'E', 'F', 'Gb', 'G', 'Ab', 'A', 'Bb', 'B'];
const LENGTHS = [{ id: 1, label: '1 bar' }, { id: 2, label: '2 bars' }];
const GUIDES = [{ id: 'full', label: 'Full' }, { id: 'fading', label: 'Fading' }, { id: 'dark', label: 'Dark' }];
const SEATS = [{ id: 'solo', label: 'Solo' }, { id: 'bass', label: 'Bass' }, { id: 'chords', label: 'Chords' }];
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
  coach: true, wander: false, guide: 'full', seat: 'solo', mode: 'songs', room: 'dorian' };

export function createJam(ctx, { onStart } = {}) {
  const saved = ctx.state?.settings?.ui?.jam || {};
  const opt = { ...DEFAULTS, ...saved };
  if (!KEYS.some((k) => k.id === opt.key)) opt.key = DEFAULTS.key;
  if (!STYLES.some((s) => s.id === opt.style)) opt.style = DEFAULTS.style;
  if (!GUIDES.some((g) => g.id === opt.guide)) opt.guide = DEFAULTS.guide;
  if (!SEATS.some((s) => s.id === opt.seat)) opt.seat = DEFAULTS.seat;
  if (!MODES.some((m) => m.id === opt.mode)) opt.mode = DEFAULTS.mode;
  if (!ROOMS.some((r) => r.id === opt.room)) opt.room = DEFAULTS.room;

  const plans = new Map();     // key id -> loaded plan
  const loading = new Map();   // key id -> its request, so two callers share one
  let plan = null;
  let prog = null;
  let chart = [];              // bar -> entry (see entry()); an entry spans opt.bars bars
  let playing = false, starting = false, destroyed = false, stale = false;
  let drawn = false, yielded = false;
  let clock = null;            // { pos, at, barMs }: bar `pos` was heard at performance.now() `at`
  let shownBar = -1, shownCross = '';
  let raf = 0;
  let phrasesInKey = 0;
  // Times through a progression since the lights came back (harmony.js guideLevel).
  // -1 until the first one starts. Not phrasesInKey: that only counts with Wander on.
  let times = -1;
  // Where Fading started counting from: picking Fading mid-song starts it at full.
  let fadeFrom = 0;
  let lastNote = null;         // the last note you played (MIDI)
  let lastBass = null;         // ...and the last one in the bass seat's register
  let cross = null;            // the change ahead: { bar, to, keep, land, why, hit }
  let ahead = false;           // inside the two beats before that change
  let heard = null;            // the last note you played and how it fit
  let holding = null;          // chords seat: what your held keys make of this chord
  let held = { notes: [], sus: false };   // the keys down, from the last frame
  // Counted once per chord, so holding a root or a chord is not a stream of points.
  let rootedFor = null, madeFor = null, countedFor = null, oneBar = -1;
  const tally = { chord: 0, color: 0, rub: 0, out: 0, landed: 0,
    chords: 0, rooted: 0, one: 0, made: 0, sig: 0, asked: 0, home: 0, root: 0, exact: 0 };
  // Answer and Echo: an exchange whose bars are over and is waiting to be judged, the
  // last verdict, and how long Echo's licks are now.
  let pending = null, verdict = null, overCall = false;
  let gen = 0;                 // bumped by restart(): asks from before it are not judged
  const echo = { size: 3, streak: 0, misses: 0, from: 72 };
  let leadShown = '';

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
  const pickStyle = (id) => {
    opt.style = id;
    opt.bpm = STYLES.find((s) => s.id === id).bpm;
    bpm.value = opt.bpm; bpm.dispatchEvent(new Event('input'));
    STYLES.forEach((s, i) => styleSeg.children[i].classList.toggle('is-on', s.id === id));
    save();
    if (playing) configure({ style: id, bpm: opt.bpm });
  };
  const styleSeg = seg(STYLES, opt.style, pickStyle);
  const roomSelect = h('select.jam__select', { onchange: (e) => setRoom(e.target.value) },
    ROOMS.map((r) => h('option', { value: r.id, selected: r.id === opt.room }, `${r.name} — ${r.sigName}`)));
  // The options that only some modes use, so applyMode() can show the right ones.
  const only = {};
  const tag = (name, node) => { only[name] = node; node.classList.add(`jam__opt--${name}`); return node; };

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
    info(h('strong', null, 'Guide'), ' sets how much is lit. ', h('strong', null, 'Full'),
      ' shows everything, every time. ', h('strong', null, 'Fading'), ' turns the lights down ',
      'each time round, so your ear does a little more each pass: the first time through a ',
      'progression everything is lit, the second time only the chord, the third time only its ',
      'name, and after that just the beat. A new key or a new progression brings the lights back. ',
      h('strong', null, 'Dark'), ' is the beat alone from the start. Whatever you play still ',
      'flashes on the strip in the colour of how it fit, and the count still runs.'),
    info(h('strong', null, 'Seat'), ' is which player in the band you are. ', h('strong', null, 'Solo'),
      ' is the whole band, with you on the tune. ', h('strong', null, 'Bass'), ' sends the band\'s ',
      'bass player home and your left hand takes over: each chord\'s root is lit low on the keyboard, ',
      'with its fifth dim. Play the root on the first beat of the bar and the band sounds whole again. ',
      h('strong', null, 'Chords'), ' sends the keyboard player home: every note of the chord is lit ',
      'either side of middle C, and any of them, in any order and any octave there, is right. In ',
      'Rock the band\'s chords are low power chords, and those are what you are replacing. A new ',
      'seat starts at the next bar the band has not already lined up.'),
    info(h('strong', null, 'Play'), ' is what the band plays. ', h('strong', null, 'Songs'),
      ' is the progressions above. ', h('strong', null, 'Modal'), ' is a room: two chords back and ',
      'forth forever, all on the white keys, with a different note as home. The ', h('strong', null, 'ringed'),
      ' key is the one note that gives the mode its colour, the ♮6 of Dorian or the ♭2 of Phrygian: ',
      'find it and lean on it. ', h('strong', null, 'Blues'), ' is the 12-bar blues in the key you pick, ',
      'best with the Shuffle style. Its ringed key is the blue note, and here it counts as colour, not ',
      'as outside the key. ', h('strong', null, 'Answer'), ' is a conversation: the band plays a two-bar ',
      'question that ends up in the air, then the next two bars are yours. End on a home note and the ',
      'phrase sounds finished. ', h('strong', null, 'Echo'), ' is ear training: the band plays a one-bar ',
      'lick and you play it back in the next bar, in any octave. Two clean echoes in a row and the licks ',
      'grow a note; two misses and they shrink one. With the guide on Full, the band\'s notes light up ',
      'as it plays them. In Answer and Echo you are the other soloist, so there is no seat to take.'),
    h('div.jam', null,
      h('div.jam__top', null,
        el.go,
        // Laid out as part of the row; in focus, an overlay that takes no room (style.css).
        h('div.jam__opts', null,
          h('div.jam__opt', null, h('span.jam__lbl', null, 'Play'), seg(MODES, opt.mode, setMode)),
          tag('key', h('label.jam__opt', null, h('span.jam__lbl', null, 'Key'), keySelect)),
          tag('room', h('label.jam__opt', null, h('span.jam__lbl', null, 'Room'), roomSelect)),
          h('div.jam__opt', null, h('span.jam__lbl', null, 'Style'), styleSeg),
          tag('bars', h('div.jam__opt', null, h('span.jam__lbl', null, 'Each chord'),
            seg(LENGTHS, opt.bars, (id) => { opt.bars = id; save(); if (playing) rechart(); }))),
          tag('seat', h('div.jam__opt', null, h('span.jam__lbl', null, 'Seat'), seg(SEATS, opt.seat, setSeat))),
          h('div.jam__opt', null, h('span.jam__lbl', null, 'Guide'), seg(GUIDES, opt.guide, setGuide)))),

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
        tag('wander', toggle('Wander', opt.wander, (on) => { opt.wander = on; save(); })),
        tag('fresh', h('button.btn', { onclick: () => newPhrase() }, 'New progression'))),
      el.stats)));
  root.querySelector('.mod__head').append(focusBtn(() => ctx.toggleFocus?.(undefined, root),
    'Just Jam and the keys, full screen (G). Esc comes back; the band keeps playing.'));
  const jamEl = root.querySelector('.jam');

  /* Which options this mode uses. The rest are hidden, not disabled: an option that does
     nothing here is noise. */
  const USES = {
    key: (m) => m !== 'modal', room: (m) => m === 'modal', bars: (m) => m === 'songs' || m === 'modal',
    seat: (m) => !DUET.has(m), wander: (m) => m === 'songs', fresh: (m) => m === 'songs' || m === 'echo',
  };
  function applyMode() {
    jamEl.dataset.mode = opt.mode;
    for (const [name, node] of Object.entries(only)) node.style.display = USES[name](opt.mode) ? '' : 'none';
  }
  applyMode();

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

  /* ── the modes ─────────────────────────────────────────────────────────── */
  const room = () => ROOMS.find((r) => r.id === opt.room) || ROOMS[0];
  const seat = () => (DUET.has(opt.mode) ? 'solo' : opt.seat);
  const bassOf = (e) => e.chord.bass ?? e.chord.pcs[0];

  /* The chord stream for the mode. Every mode but Modal needs the key loaded first. */
  function freshGen() {
    if (opt.mode === 'modal') return createVamp(room());
    if (!plan) return null;
    const mode = modeOf(plan);
    if (opt.mode === 'blues') return createBlues({ tonic: pcOf(plan.key), minor: mode === 'minor' });
    if (opt.mode === 'answer') return createAsks({ mode, scale: plan.pcs });
    return createProgression({ mode, scale: plan.pcs });
  }

  // A name for a note outside the key: the blues' flat 7ths and blue notes are flats.
  const nameIn = (p, pc) => p.chords.flatMap((c) => c.names).find((n) => pcOf(n) === pc) || FLATS[pc];

  /* Modal: every room is on the white keys, so the names are the letters. */
  function modalEntry(c) {
    const r = room();
    const scale = roomScale(r);
    const sym = (root, suffix, bass) => NAMES[root] + suffix + (bass !== root ? `/${NAMES[bass]}` : '');
    return { chord: c, plan: null, scale, symbol: sym(c.root, c.suffix, c.bass),
      byPc: new Map(scale.map((pc) => [pc, NAMES[pc]])), pal: palette(c.pcs, scale),
      sig: [roomSig(r)], sigName: r.sigName, kind: 'modal', room: r,
      chips: r.vamp.map((v) => {
        const root = (r.tonic + v.at) % 12;
        return sym(root, v.suffix, v.bass === undefined ? root : (r.tonic + v.bass) % 12);
      }) };
  }

  /* Blues: judged against the chord's own scale and the blues scale (harmony.js). */
  function bluesEntry(c, p = plan) {
    const tonic = pcOf(p.key);
    const minor = modeOf(p) === 'minor';
    const scale = bluesScale(tonic, c.roman, minor);
    const byPc = new Map(scale.map((pc) => [pc, nameIn(p, pc)]));
    const suffix = (r) => (r.startsWith('i') ? 'm7' : '7');
    return { chord: c, plan: p, scale, symbol: pretty(nameIn(p, c.pcs[0])) + c.suffix, byPc,
      pal: palette(c.pcs, scale), sig: [bluesNote(tonic, minor)], sigName: 'blue', kind: 'blues', minor,
      chips: c.phrase.seq.map((r) => pretty(nameIn(p, bluesChord(tonic, r)[0])) + suffix(r)) };
  }

  function entryFor(c) {
    if (opt.mode === 'modal') return modalEntry(c);
    if (opt.mode === 'blues') return bluesEntry(c);
    const e = entry(c);
    if (opt.mode === 'echo') {
      const notes = lick({ pal: e.pal, size: echo.size, from: echo.from });
      echo.from = notes[notes.length - 1].key;
      e.ask = { lick: notes, played: [], gen, entry: e };
      e.lead = [notes, []];
      e.turns = ['call', 'you'];
    } else if (opt.mode === 'answer') {
      const a = c.ask;
      if (!a.call) {
        // The question needs the second chord too: it is the next one out.
        const home = [plan.pcs[0], plan.pcs[2], plan.pcs[4]];
        a.call = question({ pals: [e.pal, entry(prog.peek()).pal], home });
        a.bars = [a.call.filter((n) => n.step < 16), a.call.filter((n) => n.step >= 16)
          .map((n) => ({ ...n, step: n.step - 16 }))];
        a.home = home;
        a.played = [];
        a.gen = gen;
      }
      a.entry = e;                // spelled from the last chord of the exchange, home
      e.ask = a;
      e.lead = [c.turn === 'call' ? a.bars[c.step] : []];
      e.turns = [c.turn];
    }
    return e;
  }

  // Bars per chord: Echo's chord spans the lick and your echo; Blues and Answer count bars.
  const barsFor = () => (opt.mode === 'echo' ? 2 : opt.mode === 'blues' || opt.mode === 'answer' ? 1 : opt.bars);
  // How far ahead the band's chart is kept. Echo keeps it short, so a lick is written only
  // a couple of exchanges before it is played and its length can follow how you are doing.
  const ahead_ = () => (opt.mode === 'echo' ? 4 : AHEAD);

  /* The next `n` bars of music, continuing the progression. A wander is decided at a
     phrase boundary, so the new key starts on a phrase's first chord. */
  function compose(n) {
    const out = [];
    while (out.length < n) {
      const upcoming = prog.peek();
      if (opt.mode === 'songs' && opt.wander && upcoming.step === 0 && upcoming.pass === 1) {
        phrasesInKey++;
        if (phrasesInKey >= 3 && Math.random() < 0.5) wander();
      }
      const c = prog.next();
      // A vamp is two chords: Modal counts a time through every fourth time round it.
      if (c.step === 0 && (c.cycle ?? 0) % 4 === 0) times++;
      const e = entryFor(c);
      // Stamped now, so a bar re-laid by rechart() keeps the level it was charted with.
      e.times = Math.max(0, times);
      e.level = guideLevel(e.times - fadeFrom, opt.guide);
      for (let i = 0, k = barsFor(); i < k; i++) out.push(e);
    }
    return out;
  }

  /* The same chord, charted twice in a row -- the blues' first four bars -- is no change:
     there is nothing to cross to and nothing new to show as Next. */
  const same = (a, b) => !!a && !!b && (a === b || a.symbol === b.symbol);

  /* Which of its bars `bar` is, for an entry that spans several. */
  const offsetIn = (bars, i) => {
    let o = 0;
    while (i - o - 1 >= 0 && bars[i - o - 1] === bars[i]) o++;
    return o;
  };
  const turnAt = (bar) => chart[bar]?.turns?.[offsetIn(chart, bar)] ?? null;

  // The shuffle's walking bass climbs to a sixth: the one in the chord's own scale (A in C,
  // A flat over A minor's i), or its fifth again where the scale has neither.
  const sixth = (e) => [9, 8, 7].map((s) => (bassOf(e) + s) % 12)
    .find((pc) => e.scale.includes(pc) || e.pal.chord.includes(pc)) ?? (bassOf(e) + 7) % 12;
  const wire = (bars) => bars.map((e, i) => ({ root: e.chord.pcs[0], pcs: e.chord.pcs, bass: bassOf(e),
    six: sixth(e),
    lead: (e.lead?.[offsetIn(bars, i)] || []).map((n) => [n.step, n.len, n.key, 84]) }));

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
    times = -1;
    fadeFrom = 0;
    prog = freshGen();
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
    times = -1;
    fadeFrom = 0;
    prog = freshGen();
    prefetchNeighbours();
    if (playing) rechart(true);
    else if (starting) stale = true;
    else idle();
  }

  /* ── the band ──────────────────────────────────────────────────────────── */
  async function start() {
    if (starting || playing || destroyed) return;
    starting = true;
    stale = false;
    try {
      if (!plan) await setKey(opt.key);
      if (!plan || destroyed) return;
      onStart?.();
      times = -1;
      fadeFrom = 0;
      // A conversation starts with the band's turn, not halfway through the last one.
      if (DUET.has(opt.mode) || !prog) prog = freshGen();
      pending = verdict = null;
      overCall = false;
      Object.assign(echo, { size: 3, streak: 0, misses: 0, from: 72 });
      chart = compose(ahead_() + 4);
      const r = await api.post('/api/band/start', {
        chart: wire(chart), style: opt.style, bpm: opt.bpm, volume: opt.volume, drums: opt.drums,
        ...parts(),
      });
      if (destroyed) { api.post('/api/band/stop').catch(() => {}); return; }
      clock = { pos: -LEAD_MS / r.bar_ms, at: performance.now(), barMs: r.bar_ms };
      playing = true;
      yielded = false;
      lastNote = null;
      lastBass = null;
      cross = null;
      ahead = false;
      heard = null;
      holding = null;
      shownBar = -1;
      el.go.textContent = 'Stop';
      el.go.classList.remove('btn--primary');
      ctx.kb.setMode('flow', true);
      if (!raf) raf = requestAnimationFrame(tick);
      if (stale) { stale = false; rechart(true); }
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
    // A top-up in flight was written from the old stream: let it land first, so this one
    // overwrites it rather than the other way round.
    recharting = true;
    if (inflight) await inflight;
    recharting = false;
    if (!playing) return;
    const bar = Math.max(0, Math.floor(position()) + 1);
    let bars;
    if (fresh) {
      bars = compose(ahead_());
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
    times = -1;
    fadeFrom = 0;
    if (playing) rechart(true);
    else start();
  }

  /* Full / Fading / Dark. The chart already composed is re-levelled from the times
     through each chord was stamped with, so the change shows from the next repaint.
     Fading counts from the time through playing now, so picking it mid-song starts lit. */
  function setGuide(id) {
    opt.guide = id;
    save();
    if (id === 'fading') fadeFrom = playing ? chart[Math.max(0, shownBar)]?.times ?? 0 : 0;
    for (const e of new Set(chart)) e.level = guideLevel(e.times - fadeFrom, opt.guide);
    shownBar = -1;
  }

  /* Songs / Modal / Blues / Answer / Echo. A new chord stream from the next bar the band
     has not queued, with the lights back on and the count started again. Blues brings the
     Shuffle with it; any style can be picked after. */
  function setMode(id) {
    opt.mode = id;
    save();
    applyMode();
    if (id === 'blues' && opt.style !== 'shuffle') pickStyle('shuffle');
    restart();
    if (playing) configure(parts());
  }

  function setRoom(id) {
    opt.room = id;
    save();
    restart();
  }

  /* A fresh stream for the mode, the lights back on, the count from zero. */
  function restart() {
    gen++;
    prog = freshGen();
    times = -1;
    fadeFrom = 0;
    resetCount();
    if (!prog) return;
    if (playing) rechart(true);
    else if (starting) stale = true;       // start() charted the old one: it recharts when up
    else idle();
  }

  function resetCount() {
    for (const k of Object.keys(tally)) tally[k] = 0;
    rootedFor = madeFor = countedFor = null;
    oneBar = -1;
    heard = holding = pending = verdict = null;
    overCall = false;
    Object.assign(echo, { size: 3, streak: 0, misses: 0, from: 72 });
    cross = null;
    shownCross = '?';
    paintHeard();
    paintStats();
  }

  /* Which of the band's parts play: the one you sit in for does not. */
  const parts = () => ({ bass: seat() !== 'bass', comp: seat() !== 'chords' });

  /* Solo / Bass / Chords. The band hears about it from its next unqueued bar; the keys
     change on the next repaint, with the way across worked out again for the new hand.
     The seat's count starts again, from the chord playing now. */
  function setSeat(id) {
    opt.seat = id;
    save();
    if (playing) configure(parts());
    cross = null;
    heard = null;
    holding = null;
    tally.chords = tally.rooted = tally.one = tally.made = 0;
    rootedFor = madeFor = countedFor = null;
    oneBar = -1;
    shownCross = '?';
    paintHeard();
    paintStats();
  }

  /* The seat's notes over one chord, and the keys its hand plays (harmony.js). */
  // A slash chord's bass is not its root: F under G is what the bass seat plays in Lydian.
  const target = (e) => (seat() === 'bass' && bassOf(e) !== e.chord.pcs[0]
    ? { tone: [bassOf(e)], color: [] } : seatTarget(seat(), e.pal));
  const zone = () => seatZone(seat(), ctx.kb?.low ?? 21, ctx.kb?.high ?? 108);

  /* Keep the band's copy of the chart AHEAD bars past where you are. */
  let inflight = null, recharting = false;
  function topUp(bar) {
    if (inflight || recharting || chart.length > bar + ahead_()) return;
    const from = chart.length;
    const more = compose(opt.mode === 'echo' ? 2 : 8);
    inflight = api.post('/api/band/chart', { from, chart: wire(more) })
      .then((r) => { chart = chart.slice(0, r.from).concat(more); })
      .catch(() => {})
      .finally(() => { inflight = null; });
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
    if (bar !== shownBar) turned(bar);
    if (pending && pos >= pending.at) { judgeAsk(pending.ask); pending = null; }
    paintLead(pos, bar);
    const key = cross ? `${cross.bar}${ahead ? '<' : '>'}` : '';
    if (bar !== shownBar || key !== shownCross) {
      const changed = bar !== shownBar && chart[bar] !== chart[shownBar];
      const turn = bar !== shownBar && turnAt(bar) !== turnAt(shownBar);
      shownBar = bar;
      shownCross = key;
      if (changed) paintChord();
      if (turn) paintHeard();
      // By entry, not by repaint: a rechart or a Guide change repaints the same chord.
      // Not only on a change: a new seat clears countedFor and counts the chord it starts in.
      const fresh = countedFor !== chart[bar];
      if (fresh) { countedFor = chart[bar]; tally.chords++; }
      // The keys you are holding are judged against the chord now playing.
      if (changed && seat() === 'chords') { judgeHold(); paintHeard(); }
      if (changed || fresh) paintStats();
      paintCoach();
      paintKeys();
    }
  }

  /* Answer and Echo: the bar line from `shownBar` into `bar`. Leaving your last bar of an
     exchange queues it to be judged -- a moment into the next bar, so a last note played
     a hair late still counts. */
  function turned(bar) {
    const was = chart[shownBar];
    // An exchange from before a mode, key or room change is dropped, not judged as this one.
    if (was?.ask?.gen === gen && turnAt(shownBar) === 'you' && !(turnAt(bar) === 'you' && chart[bar]?.ask === was.ask)) {
      pending = { ask: was.ask, at: bar + EARLY };
    }
    if (turnAt(bar) !== turnAt(shownBar)) overCall = false;
  }

  /* The band's own notes, lit as it plays them: on the keyboard and on the strip. Only
     with the guide all the way up -- past that, the melody is for your ear. */
  function paintLead(pos, bar) {
    const e = chart[bar];
    const notes = e?.lead?.[offsetIn(chart, bar)] || [];
    const s = (pos - bar) * 16;
    const keys = notes.length && levelOf(e) === 0
      ? notes.filter((n) => s >= n.step && s < n.step + n.len).map((n) => n.key) : [];
    const k = keys.join(',');
    if (k === leadShown) return;
    leadShown = k;
    ctx.kb?.setMarks('lead', keys);
    octKeys.forEach((key, pc) => key.classList.toggle('is-lead', keys.some((m) => m % 12 === pc)));
  }

  /* An exchange is over: how did it go? Playing nothing is sitting one out, not a miss. */
  function judgeAsk(a) {
    const got = a.played;
    if (a.lick) {
      const want = a.lick.map((n) => n.key);
      const names = (ms, e) => ms.map((m) => pretty(e.byPc.get(m % 12) || NAMES[m % 12])).join(' ');
      const e = a.entry;
      if (!got.length) {
        verdict = { fit: 'color', text: `Sat that one out. It was ${names(want, e)}` };
      } else {
        tally.asked++;
        const sc = echoScore(want, got);
        if (sc.exact) {
          tally.exact++;
          echo.misses = 0;
          if (++echo.streak >= 2 && echo.size < 6) { echo.size++; echo.streak = 0; toast(`Licks are ${echo.size} notes now`, 'good'); }
          verdict = { fit: 'chord', text: `${names(want, e)}: note for note` };
        } else {
          echo.streak = 0;
          if (sc.matched / sc.of < 0.6 && ++echo.misses >= 2 && echo.size > 2) { echo.size--; echo.misses = 0; }
          verdict = { fit: sc.matched / sc.of >= 0.6 ? 'color' : 'rub',
            text: `${sc.matched} of ${sc.of}: it was ${names(want, e)}, you played ${names(got.slice(0, 8), e)}` };
        }
      }
    } else if (a.home) {
      const e = a.entry;
      const homeNames = a.home.map((pc) => pretty(e.byPc.get(pc) || NAMES[pc])).join(' · ');
      if (!got.length) {
        verdict = { fit: 'color', text: 'No answer that time. The two bars after the question are yours.' };
      } else {
        tally.asked++;
        const last = got[got.length - 1] % 12;
        const name = pretty(e.byPc.get(last) || NAMES[last]);
        if (last === a.home[0]) {
          tally.home++; tally.root++;
          verdict = { fit: 'chord', text: `You ended on ${name}: home, on the root. That answer finishes it.` };
        } else if (a.home.includes(last)) {
          tally.home++;
          verdict = { fit: 'chord', text: `You ended on ${name}: home. The root, ${homeNames.split(' · ')[0]}, would close it all the way.` };
        } else {
          verdict = { fit: 'rub', text: `You ended on ${name}: still open, it wants to go on. Home is ${homeNames}.` };
        }
      }
    }
    paintHeard();
    paintStats();
  }

  /* The change ahead. The way across is worked out once, when the two-beat warning
     starts, and kept through the first beat of the new chord -- the beat you land on. */
  function track(pos, bar) {
    // Echo's job is copying the lick, and a "head for" pointing elsewhere would fight it.
    if (!opt.coach || opt.mode === 'echo') { cross = null; ahead = false; return; }
    let k = bar + 1;
    while (k < chart.length && same(chart[k], chart[bar])) k++;
    const to = chart[k];
    if (to && k - pos <= WARN && (!cross || cross.bar !== k)) {
      // In the seat's own hand and register: the bass crosses root to root.
      const z = zone();
      const c = crossing(target(chart[bar]).tone, target(to).tone,
        seat() === 'bass' ? lastBass ?? z.low + 7 : lastNote, z);
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
    if (!playing) return;
    const now = chart[Math.max(0, shownBar)];
    if (!now) return;
    if (f.held) held = { notes: f.held, sus: !!f.sus };
    if (seat() === 'chords' && f.held) judgeHold();
    for (const [n] of f.on || []) {
      const pc = n % 12;
      // The ringed note is the mode's colour, even where palette() calls it a rub:
      // Phrygian's F over E minor is the whole point of Phrygian.
      const sig = !!now.sig?.includes(pc) && !now.pal.chord.includes(pc);
      const fit = now.pal.chord.includes(pc) ? 'chord' : now.pal.color.includes(pc) || sig ? 'color'
        : now.scale.includes(pc) ? 'rub' : 'out';
      tally[fit]++;
      if (sig) tally.sig++;
      if (cross && !cross.hit && pc === cross.land % 12) { cross.hit = true; tally.landed++; }
      heard = { n, fit, sig, name: pretty(now.byPc.get(pc) || noteName(n)) };
      // Answer and Echo: whose turn is it? A note a hair early counts toward the bar it
      // reaches for, and one a hair late still belongs to the turn that just ended.
      if (now.ask || DUET.has(opt.mode)) {
        const pos = position();
        const b = Math.floor(pos + EARLY);
        const t = turnAt(b);
        if (pending && pos < pending.at) pending.ask.played.push(n);
        else if (t === 'you' && chart[b].ask) { chart[b].ask.played.push(n); verdict = null; overCall = false; }
        else if (t === 'call') overCall = true;
      }
      lastNote = n;
      // Judged in the register the seat lights, which on a small keyboard reaches past middle C.
      if (seat() === 'bass' && n <= zone().high) judgeBass(n, now);
      flash(pc, fit);
    }
    if (!f.on && seat() !== 'chords') return;
    paintHeard();
    paintStats();
  }

  /* Bass seat: a low note against the root. Within half a beat of a bar line it is
     judged against the chord of the bar it is nearest -- a root played a hair early is
     the next chord's root, on the next one, not off this one. A count for this sitting,
     not a grade. */
  function judgeBass(n, now) {
    lastBass = n;
    const pc = n % 12;
    const pos = position();
    const b = Math.round(pos);
    const near = Math.abs(pos - b) <= 0.5 / BEATS && chart[b];
    const e = near || now;
    const root = bassOf(e);
    heard.name = pretty(e.byPc.get(pc) || noteName(n));
    heard.bass = pc === root ? 'root' : pc === (root + 7) % 12 ? 'fifth' : 'other';
    heard.root = pretty(e.byPc.get(root) || NAMES[root]);
    if (pc === root && rootedFor !== e) { rootedFor = e; tally.rooted++; }
    if (near && pc === root && b !== oneBar) {
      oneBar = b;
      tally.one++;
    }
  }

  /* Chords seat: what the keys you are holding make of this chord. f.held is the keys
     physically down, so with the pedal down a chord you have let go of is still ringing
     and is still the chord you made: the last full one stands until the pedal comes up. */
  function judgeHold() {
    const now = chart[Math.max(0, shownBar)];
    if (!now) return;
    const pcs = [...new Set(held.notes.map((n) => n % 12))];
    const want = now.pal.chord;
    const missing = want.filter((pc) => !pcs.includes(pc));
    if (held.sus && holding?.entry === now && holding.full && (missing.length || !pcs.length)) return;
    if (!pcs.length) { holding = null; return; }
    holding = { entry: now, full: !missing.length, missing,
      have: want.filter((pc) => pcs.includes(pc)), extra: pcs.filter((pc) => !want.includes(pc)) };
    if (holding.full && madeFor !== now) { madeFor = now; tally.made++; }
  }

  const NAMES = ['C', 'C#', 'D', 'Eb', 'E', 'F', 'F#', 'G', 'Ab', 'A', 'Bb', 'B'];
  const noteName = (n) => NAMES[n % 12];

  /* ── drawing ───────────────────────────────────────────────────────────── */

  /* One octave, C to B, built once; painting only flips classes and labels. */
  const WHITE = [0, 2, 4, 5, 7, 9, 11];
  const TONE_ROLE = ['root', '3rd', '5th', '7th'];
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

  /* How much of the guide this chord gets (harmony.js guideLevel). 0 when stopped. */
  const levelOf = (e) => (playing && e ? e.level : 0);

  function paintOctave() {
    const now = playing ? chart[Math.max(0, shownBar)] : null;
    const level = levelOf(now);
    // The strip keeps its chord and colour classes at every level and style.css stops
    // drawing them; the way across is simply not shown past level 0.
    jamEl.dataset.guide = String(level);
    const next = ahead && !level ? cross.to : null;
    const t = now ? target(now) : null;
    const nt = next ? target(next) : null;
    const sigs = sigOf(now, level);
    const hint = echoHint(now, level).map((m) => m % 12);
    octKeys.forEach((k, pc) => {
      const tone = t ? t.tone.indexOf(pc) : -1;
      const color = !!t && t.color.includes(pc);
      const inNext = (!!nt && nt.tone.includes(pc)) || hint.includes(pc);
      const sig = sigs.includes(pc);
      k.classList.toggle('is-chord', tone >= 0);
      k.classList.toggle('is-color', color);
      k.classList.toggle('is-next', inNext);
      k.classList.toggle('is-sig', sig);
      k.classList.toggle('is-keep', !!next && cross.keep.includes(pc));
      k.classList.toggle('is-land', !!cross && !level && cross.land % 12 === pc);
      const named = !now || tone >= 0 || color || inNext || sig;
      k.children[1].textContent = named
        ? pretty((nt?.tone.includes(pc) && next.byPc.get(pc)) || now?.byPc.get(pc) || NAMES[pc]) : '';
      // Named by its place in the chord, so the bass seat's dim fifth says "5th" too. The
      // ringed note says what it is to the mode -- unless it is in the chord right now.
      const inChord = now ? now.pal.chord.indexOf(pc) : -1;
      k.children[2].textContent = inChord >= 0 && (tone >= 0 || color) ? TONE_ROLE[inChord] || ''
        : sig ? now.sigName : '';
    });
  }

  /* The ringed note (Modal's mode note, Blues' blue note), shown while any colour is. */
  const sigOf = (e, level) => (e?.sig && level <= 1 ? e.sig : []);

  /* Echo, your bar, guide all the way up: the lick's keys, its first note pulsing. */
  const echoHint = (e, level) => (playing && e?.ask?.lick && !level && turnAt(shownBar) === 'you'
    ? e.ask.lick.map((n) => n.key) : []);

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
    while (same(chart[k], now)) k++;
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
        // Answer marks whose bars are whose: the band's two, then yours.
        const who = now.ask?.home ? (i < 2 ? 'band' : 'you') : roman;
        return h('span.jam__chip' + (live ? '.is-on' : ''), null,
          h('b', null, now.chips?.[i] ?? spell(roman, now.plan).symbol), h('small', null, who));
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
    const names = (pcs) => pcs.map((pc) => pretty(now.byPc.get(pc) || NAMES[pc])).join(' · ');
    const bass = seat() === 'bass';
    const level = levelOf(now);
    const turn = turnAt(Math.max(0, shownBar));
    if (turn === 'call') {
      el.coach.replaceChildren(h('span.jam__try', null, 'Listen'), h('span.jam__why', null, now.ask?.lick
        ? `The band plays a lick${level ? '' : ', its keys lighting as it goes'}. Copy it in the next bar, any octave.`
        : 'The band asks a question that ends up in the air. The next two bars are yours.'));
      return;
    }
    if (turn === 'you' && now.ask?.lick) {
      if (level) say('Your turn: ', 'play it back', 'By ear, in any octave.');
      else say('Your turn: ', names(now.ask.lick.map((n) => n.key % 12)), 'The outlined keys were the lick, and the pulsing one is where it started.');
      return;
    }
    if (turn === 'you' && !(cross && !level)) {
      if (level >= 2) say('Your turn: ', 'answer it', 'End somewhere that sounds finished.');
      else say('Your turn: end on ', names(now.ask.home), 'Answer the question, then finish on a home note. The root closes it all the way.');
      return;
    }
    if (level >= 2) {
      el.coach.replaceChildren(h('span.jam__muted', null, level === 3
        ? 'Just the beat. Listen to the band for where the chord goes.'
        : bass ? 'The keys are dark. A chord\'s name starts with its root: find it low.'
          : 'The keys are dark. The name is the chord: find its notes by ear.'));
      return;
    }
    if (cross && !level) {
      const name = pretty(cross.to.byPc.get(cross.land % 12) || noteName(cross.land));
      const kept = cross.keep.includes(cross.land % 12);
      if (bass) {
        if (ahead) say(kept ? 'Stay on ' : 'Head for ', name, `${cross.to.symbol} is next: ${kept ? 'the same root' : 'its root'}.`);
        else say('Land on ', name, `${cross.to.symbol}, right on the one.`);
      } else if (ahead) say(kept ? 'Hold ' : 'Head for ', name, `${cross.to.symbol} is next: ${cross.why}.`);
      else say('Land on ', name, `${cross.to.symbol}, right on the change.`);
      return;
    }
    if (bass) {
      say('Root: ', names([bassOf(now)]), level
        ? 'the chord\'s floor. Play it low, with your left hand.'
        : 'the chord\'s floor. Play it low, with your left hand; the dim key is its fifth.');
    } else if (seat() === 'chords') {
      say('Chord: ', names(now.pal.chord),
        'Any of these, in any order, in any octave of the lit keys. All of them together is the chord.');
    } else if (now.sig) {
      const sig = names(now.sig);
      // By the chord's own kind, not the setting: after a switch, a bar or two of the old
      // mode is still on its way.
      say('Rest on ', names(now.pal.chord), now.kind === 'modal'
        ? `Ringed: ${sig}, ${now.room.name.split(' ')[1]}'s ${now.sigName}, ${now.room.sigWhy}`
        : `Ringed: ${sig}, the blue note. ${now.minor ? 'Slip through it, 4th to 5th.'
          : `Slide off it onto ${names([(now.sig[0] + 1) % 12])}.`}`);
    } else if (level === 1) {
      say('Rest on ', names(now.pal.chord),
        'Only the chord is lit this time round. The colour notes are still there: find them by ear.');
    } else {
      say('Rest on ', names(now.pal.chord), 'Every lit key sounds good. Bright is the chord, dim adds colour.');
    }
  }

  function paintHeard() {
    const line = (fit, text) => el.heard.replaceChildren(h('span.jam__fit.jam__fit--' + fit, null, text));
    if (seat() === 'chords') {
      if (!holding) { el.heard.replaceChildren(); return; }
      const e = holding.entry;
      const names = (pcs) => pcs.map((pc) => pretty(e.byPc.get(pc) || NAMES[pc])).join(' ');
      // In the dark it says whether, not which: naming the missing note is a light.
      const dark = levelOf(e) >= 2;
      const extra = holding.extra.length ? `, and ${names(holding.extra)} on top` : '';
      if (holding.full) line('chord', `${names(holding.have)}: the whole ${e.symbol} chord${extra}`);
      else line('color', dark ? 'Not the whole chord yet' : holding.have.length
        ? `${names(holding.have)}: no ${names(holding.missing)} yet${extra}`
        : `None of ${e.symbol} yet: ${names(e.pal.chord)}`);
      return;
    }
    // Answer and Echo: outside your bars, how the last exchange went -- or, if you are
    // playing over the band's turn, a nudge to let it finish.
    if (DUET.has(opt.mode) && playing && turnAt(Math.max(0, shownBar)) !== 'you') {
      if (overCall) { line('rub', opt.mode === 'echo' ? 'Listen first: copy it in the next bar.' : 'Let the question finish, then answer.'); return; }
      if (verdict) { line(verdict.fit, verdict.text); return; }
    }
    if (!heard) { el.heard.replaceChildren(); return; }
    if (heard.bass) {
      const dark = levelOf(chart[Math.max(0, shownBar)]) >= 2;
      if (heard.bass === 'root') line('chord', `${heard.name}: the root`);
      else if (heard.bass === 'fifth') line('color', `${heard.name}: the fifth. It fits; the root says the chord more clearly`);
      else line('rub', dark ? `${heard.name}: not the root` : `${heard.name}: not the root. That is ${heard.root}`);
      return;
    }
    const words = { chord: 'in the chord', color: 'adds colour', rub: 'rubs against this chord: step off it',
      out: 'outside the key — spicy' };
    const now = chart[Math.max(0, shownBar)];
    if (heard.sig && now?.sigName) {
      line('color', now.kind === 'blues' ? `You played ${heard.name}: the blue note`
        : `You played ${heard.name}: the ${now.sigName}, the note that makes ${now.room.name.split(' ')[1]}`);
      return;
    }
    line(heard.fit, `You played ${heard.name}: ${words[heard.fit]}`);
  }

  function paintKeys() {
    paintOctave();
    const kb = ctx.kb;
    const now = chart[Math.max(0, shownBar)];
    if (!kb || !now || !playing) return;
    const every = (pcs) => keysIn(pcs, kb);
    const level = levelOf(now);
    const z = zone();
    // Always the whole palette over the whole keyboard, in every seat and at every level:
    // the held-key colours read these classes, and a key without them is painted spicy.
    // What is DRAWN is narrowed afterwards -- the seat marks the rest quiet, and
    // kb--nocolor / kb--dark turn the guide down -- with the classes left in place.
    let tone = every(now.pal.chord);
    let scale = every(now.pal.color);
    let quiet = [];
    if (seat() !== 'solo') {
      const t = target(now);
      const dim = new Set(keysIn(t.color, z));             // the bass seat's fifth
      const lit = new Set([...keysIn(t.tone, z), ...dim]);
      tone = tone.filter((m) => !dim.has(m));
      // A slash chord's bass is the seat's note even though it is not in the chord.
      tone = tone.concat(keysIn(t.tone, z).filter((m) => !tone.includes(m)));
      scale = scale.concat([...dim]);
      quiet = tone.concat(scale).filter((m) => !lit.has(m));
    }
    kb.setMarks('tone', tone);
    kb.setMarks('quiet', quiet);
    // The ringed note is never spicy when held, even where it rubs (Phrygian's F over E minor).
    const sigs = every(now.sig || []).filter((m) => !tone.includes(m));
    scale = scale.concat(sigs.filter((m) => !scale.includes(m)));
    kb.setMarks('scale', scale);
    kb.setMarks('sig', every(sigOf(now, level)));
    const hint = echoHint(now, level);
    kb.setMarks('suggest', cross && !level ? [cross.land] : hint.slice(0, 1));
    kb.setMarks('suggest2', ahead && !level ? keysIn(target(cross.to).tone, z) : hint);
    kb.clearLabels();
    if (cross && !level) kb.setKeyLabel(cross.land, pretty(cross.to.byPc.get(cross.land % 12) || noteName(cross.land)));
    kb.setMode('flow', true);
    kb.setMode('nocolor', level >= 1);
    kb.setMode('dark', level >= 2);
    drawn = true;
  }

  function paintStats() {
    const all = tally.chord + tally.color + tally.rub + tally.out;
    const s = (n) => (n === 1 ? '' : 's');
    if (opt.mode === 'answer' && tally.asked) {
      el.stats.replaceChildren(h('span', null,
        h('b.jam__ok', null, String(tally.home)), ` of ${tally.asked} answer${s(tally.asked)} ended home · `,
        h('b', null, String(tally.root)), ' on the root'));
      return;
    }
    if (opt.mode === 'echo' && tally.asked) {
      el.stats.replaceChildren(h('span', null,
        h('b.jam__ok', null, String(tally.exact)), ` of ${tally.asked} lick${s(tally.asked)} note for note · `,
        `licks are ${echo.size} notes now`));
      return;
    }
    if (seat() === 'bass' && all) {
      el.stats.replaceChildren(h('span', null,
        h('b.jam__ok', null, String(tally.one)), ` root${s(tally.one)} on the one · `,
        h('b', null, String(tally.rooted)), ` of ${tally.chords} chords found their root`));
      return;
    }
    if (seat() === 'chords' && (all || tally.made)) {
      el.stats.replaceChildren(h('span', null,
        h('b.jam__ok', null, String(tally.made)), ` of ${tally.chords} chords made, every note down at once`));
      return;
    }
    el.stats.replaceChildren(all
      ? h('span', null,
        h('b.jam__ok', null, String(tally.chord)), ` chord note${s(tally.chord)} · `,
        h('b', null, String(tally.color)), ' colour · ',
        h('b.jam__rub', null, String(tally.rub)), ` rub${s(tally.rub)} · `,
        h('b.jam__spicy', null, String(tally.out)), ' spicy',
        opt.mode === 'modal' || opt.mode === 'blues'
          ? ` · ${tally.sig} ${opt.mode === 'blues' ? 'blue note' + s(tally.sig) : `on the ${room().sigName}`}` : '',
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
    ctx.kb?.setMode('nocolor', false);
    ctx.kb?.setMode('dark', false);
  }

  function idle() {
    el.go.textContent = 'Start the band';
    el.go.classList.add('btn--primary');
    const modal = opt.mode === 'modal';
    el.nowSym.textContent = modal ? NAMES[room().tonic] : plan ? pretty(plan.key) : '';
    el.nowRole.replaceChildren(h('span.jam__muted', null, modal ? room().name
      : plan ? `${plan.title}${opt.mode === 'blues' ? ' blues' : ''}` : ''));
    leadShown = '';
    for (const k of octKeys) k.classList.remove('is-lead');
    el.nextSym.textContent = '';
    el.nextRole.replaceChildren();
    el.coach.replaceChildren(h('span.jam__muted', null, {
      songs: 'Start the band, then play any lit key, in any octave.',
      modal: `${room().mood} Start the band and find the ringed key.`,
      blues: 'Start the band: twelve bars, round and round. Shuffle suits it.',
      answer: 'Start the band. It asks a two-bar question; you answer it.',
      echo: 'Start the band. It plays a short lick; you play it back.',
    }[opt.mode]));
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
    prog = freshGen();
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
