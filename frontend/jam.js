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
 * What the band plays is the Music setting, and what you do over it the Play setting.
 * Every combination goes through the same chart, strip, guide and seat:
 *   Pop, Minor  the progressions above, in a major or a minor key, at a Level you pick
 *               (harmony.js createProgression; 4 changes key every second phrase).
 *   Blues       the 12-bar form, judged against the blues rather than the key, so the blue
 *               note (ringed) counts as colour instead of "outside".
 *   Modal       a two-chord vamp in a mode, with the note that makes the mode ringed.
 *   My chords   a loop you write: each chord judged against the key when it is in it, or
 *               against its own scale (the server's) when it is not.
 * Play, for Pop and Minor:
 *   Solo    you play over the band.
 *   Answer  the band plays a two-bar question; you answer for two and end at home.
 *   Echo    the band plays a one-bar lick; you play it back in the next bar. The licks
 *           grow a note after two clean echoes and shrink one after two misses.
 * Internally the two fold into one play() id: songs, modal, blues, mine, answer, echo.
 *
 * Scale pins one scale over the whole key (Key, Pentatonic or Blues): an outline on every
 * key of it, constant until the key changes, under the per-chord fill -- outline is "in
 * your scale", fill is "good on this chord". Under a Pentatonic or Blues pin the
 * progression only uses chords the pin does not rub (harmony.js pinSafe), so every
 * outlined key is good on every chord. Its notes come from the server like every scale.
 * Answer and Echo are the band's own melody, on a lead voice (band.py). Each charted
 * entry can carry `lead` and `turns` per bar it spans: who plays that bar, the band
 * ('call') or you ('you'). Notes you play in your bars are collected on the entry's
 * `ask`, one object per exchange, and judged once your bars are over.
 */

import { api, fill, focusBtn, h, info, mod, slider, toast, toggle } from './ui.js';
import { ghostArmed, onPanic } from './app.js';
import {
  ROOMS, SCALE_KEYS, bluesChord, bluesNote, bluesScale, createAsks, createBlues, createLoop, createProgression,
  createVamp, crossing, echoScore, fitOf, guideLevel, keyMoves, keysIn, lick, loopRole, loopRoman, loopScale, palette,
  pinSafe, question, roomScale, roomSig, seatTarget, seatZone, spellRoot, spellUp, wanderDue,
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
  // A fifth from each of the two above, so level 4 and Wander move by a fifth from them too,
  // and each other's relative, so Pop and Minor can switch between them.
  { id: 'Db-major', key: 'Db', mode: 'major' },
  { id: 'Bb-minor', key: 'Bb', mode: 'natural_minor' },
];
const STYLES = [
  { id: 'ballad', label: 'Ballad', bpm: 72 },
  { id: 'pop', label: 'Pop', bpm: 96 },
  { id: 'lofi', label: 'Lo-fi', bpm: 80 },
  { id: 'rock', label: 'Rock', bpm: 112 },
  { id: 'shuffle', label: 'Shuffle', bpm: 84 },
];
const MUSICS = [{ id: 'pop', label: 'Pop' }, { id: 'minor', label: 'Minor' }, { id: 'blues', label: 'Blues' },
  { id: 'modal', label: 'Modal' }, { id: 'mine', label: 'My chords' }];
const GAMES = [{ id: 'solo', label: 'Solo' }, { id: 'answer', label: 'Answer' }, { id: 'echo', label: 'Echo' }];
// What each level is, by music: the buttons' tooltips.
const LEVEL_NAMES = {
  songs: ['One loop', 'Common loops', 'Songbook', 'Key changes'],
  blues: ['Two chords', 'Plain 12-bar', 'Mixed 12-bars', 'Quick change and turnaround'],
};
const LEVELS = [1, 2, 3, 4].map((n) => ({ id: n, label: String(n) }));
const PINS = [{ id: 'off', label: 'Off' }, { id: 'key', label: 'Key' }, { id: 'penta', label: 'Pentatonic' },
  { id: 'blues', label: 'Blues' }];
// General MIDI programs; null is the style's own.
const COMP_SOUNDS = [{ id: null, label: "Style's own" }, { id: 0, label: 'Piano' }, { id: 4, label: 'E-piano' },
  { id: 16, label: 'Organ' }, { id: 25, label: 'Guitar' }, { id: 48, label: 'Strings' }, { id: 89, label: 'Pad' }];
const BASS_SOUNDS = [{ id: null, label: "Style's own" }, { id: 32, label: 'Upright' }, { id: 33, label: 'Electric' },
  { id: 38, label: 'Synth' }];
// My chords' qualities, as the server's quality ids (checked against /api/theory).
const QUALS = [{ id: '', label: 'maj' }, { id: 'm', label: 'min' }, { id: '7', label: '7' }, { id: 'm7', label: 'm7' },
  { id: 'maj7', label: 'maj7' }, { id: 'sus4', label: 'sus4' }, { id: 'dim', label: 'dim' }];
const MINE_MAX = 8, MINE_MIN = 2;
// Where the band plays its own melody, there is no seat to take: you are the other soloist.
const DUET = new Set(['answer', 'echo']);
// A note played this much of a bar early counts toward the bar it is reaching for.
const EARLY = 0.06;
const FLATS = ['C', 'Db', 'D', 'Eb', 'E', 'F', 'Gb', 'G', 'Ab', 'A', 'Bb', 'B'];
const LENGTHS = [{ id: 1, label: '1 bar' }, { id: 2, label: '2 bars' }, { id: 4, label: '4 bars' }];
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
  coach: true, wander: false, guide: 'full', seat: 'solo', room: 'dorian',
  music: 'pop', game: 'solo', level: 1, pin: 'off', bass: true, comp: true, bassSound: null, compSound: null,
  mine: [] };
const isMajor = (k) => k.mode === 'major';
const keyOf = (id) => KEYS.find((k) => k.id === id);
/* The key with the same notes and the other home: C major and A minor. */
const relative = (k) => KEYS.find((c) => isMajor(c) !== isMajor(k)
  && pcOf(c.key) === (pcOf(k.key) + (isMajor(k) ? 9 : 3)) % 12);

/* The saved options, every field checked: an unknown value is the default, never a crash.
   Settings from before 0.14 have one `mode` (songs/modal/blues/answer/echo) where there
   are now two, Music and Play; they keep what they were playing, at level 3 -- the
   behaviour they had. A fresh settings file starts at level 1. */
function loadOptions(saved) {
  const opt = { ...DEFAULTS, ...saved };
  const pickOf = (list, v, d) => (list.some((x) => x.id === v) ? v : d);
  opt.key = keyOf(opt.key) ? opt.key : DEFAULTS.key;
  opt.style = pickOf(STYLES, opt.style, DEFAULTS.style);
  opt.guide = pickOf(GUIDES, opt.guide, DEFAULTS.guide);
  opt.seat = pickOf(SEATS, opt.seat, DEFAULTS.seat);
  opt.room = pickOf(ROOMS, opt.room, DEFAULTS.room);
  if (saved.music === undefined && (saved.mode ?? saved.play) !== undefined) {
    const old = saved.mode ?? saved.play;
    const minor = !isMajor(keyOf(opt.key));
    opt.music = old === 'modal' || old === 'blues' ? old : minor ? 'minor' : 'pop';
    opt.game = old === 'answer' || old === 'echo' ? old : 'solo';
    if (saved.level === undefined) opt.level = 3;
  }
  delete opt.mode;
  delete opt.play;
  opt.music = pickOf(MUSICS, opt.music, DEFAULTS.music);
  opt.game = opt.music === 'pop' || opt.music === 'minor' ? pickOf(GAMES, opt.game, 'solo') : 'solo';
  opt.level = pickOf(LEVELS, opt.level, DEFAULTS.level);
  opt.pin = pickOf(PINS, opt.pin, DEFAULTS.pin);
  opt.bars = pickOf(LENGTHS, opt.bars, DEFAULTS.bars);
  opt.bpm = Number.isFinite(opt.bpm) ? Math.round(Math.min(160, Math.max(50, opt.bpm))) : DEFAULTS.bpm;
  opt.volume = Number.isFinite(opt.volume) ? Math.min(1, Math.max(0, opt.volume)) : DEFAULTS.volume;
  for (const b of ['drums', 'coach', 'wander', 'bass', 'comp']) if (typeof opt[b] !== 'boolean') opt[b] = DEFAULTS[b];
  opt.compSound = pickOf(COMP_SOUNDS, opt.compSound, null);
  opt.bassSound = pickOf(BASS_SOUNDS, opt.bassSound, null);
  const good = (c) => !!c && typeof c.root === 'string' && /^[A-G](#|b)?$/.test(c.root)
    && QUALS.some((q) => q.id === c.quality);
  opt.mine = Array.isArray(opt.mine) && opt.mine.length >= MINE_MIN && opt.mine.length <= MINE_MAX
    && opt.mine.every(good) ? opt.mine.map((c) => ({ root: c.root, quality: c.quality })) : [];
  // Pop is the major keys and Minor the minor ones: a key from the other side moves to its relative.
  const k = keyOf(opt.key);
  if ((opt.music === 'pop' && !isMajor(k)) || (opt.music === 'minor' && isMajor(k))) opt.key = relative(k)?.id ?? opt.key;
  return opt;
}

export function createJam(ctx, { onStart } = {}) {
  const opt = loadOptions(ctx.state?.settings?.ui?.jam || {});

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
  // Phrases finished in this key, for a key change (harmony.js wanderDue). -1 until the
  // stream's first phrase starts (newGen).
  let phrasesInKey = -1;
  // Times through a progression since the lights came back (harmony.js guideLevel).
  // -1 until the first one starts. Not phrasesInKey: that only counts Pop and Minor's Solo.
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
  let countAt = -1;            // the beat the Next chip's countdown was last painted on
  const pins = new Map();      // `${key id}:${pin}` -> { pcs, names, title, blue }
  const fetched = new Map();   // url -> its request: chords and scales for My chords
  let mine = null;             // My chords, loaded: { sig, chords: [...] } (loadMine)
  let vocab = null;            // /api/theory: the root names and quality ids on offer
  let editing = -1;            // the My chords slot open in the picker

  /* Music and Play, folded into the one id the chart, the guide and the coach go by. */
  const play = () => (opt.music === 'modal' || opt.music === 'blues' || opt.music === 'mine' ? opt.music
    : opt.game === 'solo' ? 'songs' : opt.game);

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
  // The options only some music uses, so applyMode() can show the right ones.
  const only = {};
  const tag = (name, node) => { only[name] = node; node.classList.add(`jam__opt--${name}`); return node; };
  const option = (label, control, el = 'div') => h(`${el}.jam__opt`, null, h('span.jam__lbl', null, label), control);
  const group = (name, id, ...kids) => h(`div.jam__group.jam__group--${id}`, null,
    h('div.jam__gname', null, name), h('div.jam__gbody', null, ...kids));
  const lightSeg = (row, items, id) => items.forEach((it, i) => row.children[i].classList.toggle('is-on', it.id === id));
  const musicSeg = seg(MUSICS, opt.music, (id) => setMusic(id));
  const gameSeg = seg(GAMES, opt.game, (id) => setGame(id));
  const levelSeg = seg(LEVELS, opt.level, (id) => setLevel(id));
  const sound = (items, current, onpick) => h('select.jam__select.jam__select--sound', {
    onchange: (e) => onpick(e.target.value === '' ? null : Number(e.target.value)) },
  items.map((it) => h('option', { value: it.id ?? '', selected: it.id === current }, it.label)));
  const wanderBox = toggle('Wander', opt.wander, (on) => { opt.wander = on; save(); });
  // My chords: a row of slots, and the picker for the one being changed (paintMine).
  const mineEl = { slots: h('div.jam__slots'), picker: h('div.jam__picker') };
  // In a narrow panel the Band group folds to one line that says what the band is set to
  // (style.css); this opens it. Wide, the line is hidden and the group always shows.
  const bandFold = h('button.jam__fold', { type: 'button', 'aria-expanded': 'false',
    onclick: (e) => {
      const open = jamEl.classList.toggle('is-band-open');
      e.currentTarget.setAttribute('aria-expanded', String(open));
    } });
  const paintFold = () => {
    bandFold.textContent = `${STYLES.find((st) => st.id === opt.style)?.label ?? ''} · ${opt.bpm} bpm · `
      + `${Math.round(opt.volume * 100)}%`;
  };

  const el = {
    go: h('button.btn.btn--lg.btn--primary.jam__go', { onclick: () => (playing ? stop() : start()) }, 'Start the band'),
    nowSym: h('div.jam__sym'), nowRole: h('div.jam__role'), note: h('div.jam__note'),
    nextSym: h('div.jam__sym.jam__sym--next'), nextRole: h('div.jam__role'),
    count: h('span.jam__count', { title: 'Beats until the chord changes' }),
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
    info(h('strong', null, 'Music'), ' is what the band plays. ', h('strong', null, 'Pop'), ' and ',
      h('strong', null, 'Minor'), ' are the progressions songs are built on (the pop four, the fifties, ',
      'ii–V–I, Pachelbel...), in a major or a minor key. ', h('strong', null, 'Level'), ' is how much of ',
      'them: 1 is one common loop over and over (New progression picks another), 2 rotates the four-chord ',
      'loops, 3 is the whole songbook, each played twice and coloured the second time, and 4 is the ',
      'songbook with a key change every second phrase, announced on the Now card before it lands. ',
      'Key changes are Solo\'s: Echo\'s level 4 is the songbook, and Answer asks the same questions ',
      'at every level, so it has none. Pick any level any time. ', h('strong', null, 'Wander'), ' moves to a neighbouring key now and ',
      'then, a key that changes only one note.'),
    info(h('strong', null, 'Scale'), ' pins one scale over the whole key. Its keys get an ',
      h('strong', null, 'outline'), ' across the whole keyboard that stays put until the key changes; ',
      'the fill still says what sounds good on this chord. Outline: in your scale. Fill: good right now. ',
      h('strong', null, 'Key'), ' is the key\'s seven notes. ', h('strong', null, 'Pentatonic'), ' is the ',
      'five-note scale (major in a major key, minor in a minor one), and ', h('strong', null, 'Blues'),
      ' adds the blue note to it. Under those two the band only plays chords the scale fits all the way ',
      'through, so every outlined key is good on every chord. Dark hides the outline.'),
    info(h('strong', null, 'My chords'), ' plays a loop you write: two to eight chords, picked by root ',
      'and kind, or from the key\'s own row. A chord outside the key is marked, and judged against its ',
      'own scale, which the coach names (over D7: D mixolydian).'),
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
      'seat starts at the next bar the band has not already lined up. The Band switches for bass and ',
      'chords turn a part off whatever your seat.'),
    info(h('strong', null, 'Modal'), ' is a room: two chords back and ',
      'forth forever, all on the white keys, with a different note as home. The ', h('strong', null, 'ringed'),
      ' key is the one note that gives the mode its colour, the ♮6 of Dorian or the ♭2 of Phrygian: ',
      'find it and lean on it. ', h('strong', null, 'Blues'), ' is the blues in the key you pick, ',
      'best with the Shuffle style: level 1 is two chords, 2 the plain twelve bars, 3 mixes in the quick ',
      'change, 4 adds a turnaround that moves. Its ringed key is the blue note, and here it counts as ',
      'colour, not as outside the key. With Pop or Minor, ', h('strong', null, 'Play'), ' can also be ',
      h('strong', null, 'Answer'), ', a conversation: the band plays a two-bar ',
      'question that ends up in the air, then the next two bars are yours. End on a home note and the ',
      'phrase sounds finished. Or ', h('strong', null, 'Echo'), ', ear training: the band plays a one-bar ',
      'lick and you play it back in the next bar, in any octave. Two clean echoes in a row and the licks ',
      'grow a note; two misses and they shrink one. With the guide on Full, the band\'s notes light up ',
      'as it plays them. In Answer and Echo you are the other soloist, so there is no seat to take.'),
    h('div.jam', null,
      h('div.jam__top', null,
        el.go,
        // Laid out as part of the row; in focus, one line across the top that takes no room
        // (style.css), holding only Music, Level, Key or Room, Scale and Guide.
        h('div.jam__opts', null,
          group('Music', 'music',
            option('Music', musicSeg),
            tag('level', option('Level', levelSeg)),
            tag('key', option('Key', keySelect, 'label')),
            tag('room', option('Room', roomSelect, 'label')),
            tag('pin', option('Scale', seg(PINS, opt.pin, (id) => setPin(id))))),
          group('You', 'you',
            tag('play', option('Play', gameSeg)),
            tag('seat', option('Seat', seg(SEATS, opt.seat, setSeat))),
            tag('guide', option('Guide', seg(GUIDES, opt.guide, setGuide)))))),

      h('div.jam__stage', null,
        h('div.jam__card.jam__card--now', null, h('div.jam__lbl', null, 'Now'),
          h('div.jam__line', null, el.nowSym, el.nowRole), el.note),
        h('div.jam__mid', null, el.beats, el.oct, el.coach, el.heard),
        h('div.jam__card.jam__card--next', null, h('div.jam__lbl', null, 'Next'), el.nextSym, el.nextRole, el.count)),

      el.phrase,

      h('div.jam__more', null,
        group('Band', 'band',
          bandFold,
          option('Style', styleSeg),
          tag('bars', option('Each chord', seg(LENGTHS, opt.bars, (id) => setBars(id)))),
          h('label.field.jam__field', null, h('span.field__label', null, h('span', null, 'Tempo'), bpmValue), bpm),
          h('label.field.jam__field', null, h('span.field__label', null, h('span', null, 'Band volume'), volValue), volume),
          h('div.jam__parts', null,
            h('div.jam__part', null,
              toggle('Drums', opt.drums, (on) => { opt.drums = on; save(); if (playing) configure({ drums: on }); })),
            h('div.jam__part', null,
              toggle('Bass', opt.bass, (on) => { opt.bass = on; save(); if (playing) configure(parts()); }),
              sound(BASS_SOUNDS, opt.bassSound, (v) => { opt.bassSound = v; save(); if (playing) configure({ bass_program: v }); })),
            h('div.jam__part', null,
              toggle('Chords', opt.comp, (on) => { opt.comp = on; save(); if (playing) configure(parts()); }),
              sound(COMP_SOUNDS, opt.compSound, (v) => { opt.compSound = v; save(); if (playing) configure({ comp_program: v }); }))),
          h('div.jam__switches', null,
            tag('wander', wanderBox),
            toggle('Show the change', opt.coach, (on) => { opt.coach = on; save(); shownCross = '?'; }),
            tag('fresh', h('button.btn', { onclick: () => newPhrase() }, 'New progression')))),
        tag('mine', group('My chords', 'mine', mineEl.slots, mineEl.picker))),
      el.stats)));
  root.querySelector('.mod__head').append(focusBtn(() => ctx.toggleFocus?.(undefined, root),
    'Just Jam and the keys, full screen (G). Esc comes back; the band keeps playing.'));
  const jamEl = root.querySelector('.jam');
  paintFold();

  /* Which options this music uses. The rest are hidden, not disabled: an option that does
     nothing here is noise. */
  const USES = {
    // Answer asks the same questions at every level (setLevel), so it has none to show.
    level: () => ['pop', 'minor', 'blues'].includes(opt.music) && play() !== 'answer',
    key: () => opt.music !== 'modal', room: () => opt.music === 'modal', pin: () => opt.music !== 'modal',
    play: () => opt.music === 'pop' || opt.music === 'minor', guide: () => true,
    seat: () => !DUET.has(play()), bars: () => ['songs', 'modal', 'mine'].includes(play()),
    wander: () => play() === 'songs', fresh: () => play() === 'songs' || play() === 'echo',
    mine: () => opt.music === 'mine',
  };
  /* `heard` is the key the band is playing in now. A wander is written a few bars ahead,
     so the Key and Music controls show the key you hear and follow it when it lands. */
  let shownKey = null;
  function applyMode(heard = null) {
    shownKey = heard ? heard.id : opt.key;
    const music = heard && (opt.music === 'pop' || opt.music === 'minor') ? (isMajor(heard) ? 'pop' : 'minor') : opt.music;
    jamEl.dataset.mode = play();
    for (const [name, node] of Object.entries(only)) node.style.display = USES[name]() ? '' : 'none';
    lightSeg(musicSeg, MUSICS, music);
    lightSeg(gameSeg, GAMES, opt.game);
    lightSeg(levelSeg, LEVELS, opt.level);
    // Pop offers the major keys, Minor the minor ones; Blues and My chords every key.
    const want = music === 'pop' ? true : music === 'minor' ? false : null;
    for (const o of keySelect.options) {
      const off = want !== null && isMajor(keyOf(o.value)) !== want;
      o.hidden = off;
      o.disabled = off;
    }
    keySelect.value = shownKey;
    const names = LEVEL_NAMES[opt.music === 'blues' ? 'blues' : 'songs'];
    // Key changes are Solo's (compose): in Echo, 4 is the songbook that 3 is.
    [...levelSeg.children].forEach((b, i) => {
      b.title = `Level ${i + 1}: ${play() === 'echo' && i === 3 ? 'Songbook (key changes are Solo only)' : names[i]}`;
    });
    // Level 1 is one loop and never wanders; level 4 always does. Shown as such rather
    // than hidden: it is still the switch you set for 2 and 3.
    const locked = opt.level === 1 || opt.level === 4;
    const box = wanderBox.querySelector('input');
    box.disabled = locked;
    box.checked = opt.level === 4 || (opt.level !== 1 && opt.wander);
    wanderBox.classList.toggle('is-locked', locked);
    wanderBox.title = opt.level === 1 ? 'Level 1 is one loop: it stays in its key'
      : opt.level === 4 ? 'Level 4 changes key every second phrase' : '';
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

  /* Neighbouring keys differ by one note -- or by none, for the relative major/minor.
     Which ones, and how likely, by level: harmony.js keyMoves. */
  function neighbours(id, level = 0) {
    const k = keyOf(id);
    const find = (pc, major) => KEYS.find((c) => pcOf(c.key) === pc && isMajor(c) === major);
    return keyMoves(pcOf(k.key), isMajor(k), level, (pc, major) => !!find(pc, major))
      .map((x) => ({ ...x, k: find(x.pc, x.major) }));
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

  const keyNames = (p) => p.chords.flatMap((c) => c.names);
  // One request per url, shared by every caller; a failed one is forgotten so it can be retried.
  const getJson = (url) => {
    if (!fetched.has(url)) fetched.set(url, api.get(url).catch((err) => { fetched.delete(url); throw err; }));
    return fetched.get(url);
  };

  /* ── the scale pin ─────────────────────────────────────────────────────── */
  /* The pinned scale for a key, from the server: Key is the key's seven notes; Pentatonic
     the major or minor pentatonic on the tonic; Blues the minor blues scale on the tonic,
     or in a major key its relative minor's -- the major pentatonic with the flat 3rd. The
     blue note is the one that is colour, not a rub (harmony.js bluesNote). */
  function loadPin(p, kind) {
    const id = `${p.id}:${kind}`;
    if (pins.has(id)) return Promise.resolve(pins.get(id));
    if (kind === 'key') {
      const pin = { pcs: p.pcs, names: keyNames(p), title: `${p.title} scale`, blue: null };
      pins.set(id, pin);
      return Promise.resolve(pin);
    }
    const tonic = pcOf(p.key);
    const major = modeOf(p) === 'major';
    const on = kind === 'blues' && major ? relative(keyOf(p.id))?.key ?? FLATS[(tonic + 9) % 12] : p.key;
    const mode = kind === 'penta' ? (major ? 'major_pentatonic' : 'minor_pentatonic') : 'blues';
    return getJson(`/api/theory/scale?key=${encodeURIComponent(on)}&mode=${mode}`).then((sc) => {
      const pin = { pcs: sc.pcs, names: sc.names, title: sc.title,
        blue: kind === 'blues' ? bluesNote(tonic, !major) : null };
      pins.set(id, pin);
      return pin;
    });
  }

  /* The pin over a key, if one is set and loaded. Modal is its own scale: no pin there. */
  const pinOf = (p) => (p && opt.pin !== 'off' && opt.music !== 'modal' ? pins.get(`${p.id}:${opt.pin}`) || null : null);

  /* Under a Pentatonic or Blues pin, the chords the pin fits all the way through. */
  const safeChords = (p) => {
    const pin = pinOf(p);
    return pin && (opt.pin === 'penta' || opt.pin === 'blues') ? pinSafe(p.pcs, modeOf(p), pin.pcs, pin.blue) : null;
  };

  /* ── my chords ─────────────────────────────────────────────────────────── */
  /* Until you write one, the key's I-V-vi-IV (i-VI-III-VII in minor), so it follows the key. */
  const defaultMine = (p) => (modeOf(p) === 'major' ? [0, 4, 5, 3] : [0, 5, 2, 6])
    .map((d) => ({ root: p.chords[d].root, quality: p.chords[d].quality }));
  /* A root named the way the key spells it (harmony.js spellRoot): F♯7 in E major and G♯°
     in A minor, never G♭7 or A♭°. */
  const rootIn = (p, pc, quality) => spellRoot(pc, quality, p.pcs, p.chords.map((c) => c.root));
  /* Your chords in a key, each root named for it: a chord keeps its pitch, not its name. */
  const mineFor = (p) => (opt.mine.length >= MINE_MIN ? opt.mine : defaultMine(p))
    .map((c) => ({ root: rootIn(p, pcOf(c.root), c.quality), quality: c.quality }));
  const mineList = () => (plan ? mineFor(plan) : opt.mine.map((c) => ({ ...c })));
  const mineSig = (p, list) => JSON.stringify([p.id, opt.pin, list]);

  /* Your chords, spelled by the server, each with the scale it is judged against
     (harmony.js loopScale): the pin if one is on, the key if the chord is in it (in a
     minor key, its harmonic minor for V and the leading-tone chord), else its own scale
     by quality, fetched like every other scale. A scale that cannot be had leaves the
     chord judged against the key: one odd chord never stops the band. */
  async function loadMine(p) {
    const list = mineFor(p);
    const sig = mineSig(p, list);
    if (mine?.sig === sig) return mine;
    const pin = pinOf(p);
    const scaleOf = (key, mode) => getJson(`/api/theory/scale?key=${encodeURIComponent(key)}&mode=${mode}`)
      .catch(() => null);
    const chords = await Promise.all(list.map(async (c) => {
      const ch = await getJson(`/api/theory/chord?root=${encodeURIComponent(c.root)}&quality=${encodeURIComponent(c.quality)}`);
      const own = loopScale(ch.pcs, c.quality, p.pcs);
      let scale = p.pcs, title = p.title, names = keyNames(p);
      if (pin) ({ pcs: scale, title, names } = pin);
      else if (own === 'harmonic') {
        const sc = await scaleOf(p.key, 'harmonic_minor');
        if (sc) {
          ({ pcs: scale, names } = sc);
          title = pretty(sc.title);
        }
      } else if (own !== 'key') {
        // Asked for by a name the server knows, then spelled from the chord's own letter.
        const sc = await scaleOf(SCALE_KEYS[ch.pcs[0]], own);
        if (sc) {
          scale = sc.pcs;
          names = spellUp(ch.root, sc.pcs);
          title = pretty(ch.root + sc.title.slice(sc.root.length));
        }
      }
      const root = ch.pcs[0];
      return { roman: loopRoman(root, c.quality, p.pcs), pcs: ch.pcs, root, role: loopRole(ch.pcs, c.quality, p.pcs),
        symbol: pretty(ch.title), names: ch.names, scale, scaleTitle: title, scaleNames: names,
        out: own !== 'key' && own !== 'harmonic' };
    }));
    const out = { sig, chords };
    // A newer edit may have landed while this one was loading: keep the newest.
    if (mineSig(p, mineFor(p)) === sig) mine = out;
    return out;
  }

  /* Everything the music needs before a chord can be charted: the key, its pin, your
     chords -- and for Pop and Minor the neighbouring keys with the same pin, because a
     key change (level 4, Wander) is written into the chart the moment it is decided and
     can only go to a key already here. Shared by Start, a new key, a new pin and every
     restart; only the newest call wins. */
  let readyTok = 0;
  async function ready() {
    const tok = ++readyTok;
    try {
      const p = await loadPlan(opt.key);
      if (opt.pin !== 'off') await loadPin(p, opt.pin);
      if (opt.music === 'mine') await loadMine(p);
      if (play() === 'songs') await prefetchNeighbours();
      if (destroyed || tok !== readyTok) return false;
      plan = p;
      return true;
    } catch (err) {
      if (!destroyed && tok === readyTok) toast('could not load that: ' + err.message, 'bad');
      return false;
    }
  }

  /* ── the modes ─────────────────────────────────────────────────────────── */
  const room = () => ROOMS.find((r) => r.id === opt.room) || ROOMS[0];
  const seat = () => (DUET.has(play()) ? 'solo' : opt.seat);
  const bassOf = (e) => e.chord.bass ?? e.chord.pcs[0];

  /* The chord stream for the music. Every one but Modal needs the key loaded first, and
     My chords its chords. The Level picks the pool; Answer keeps its own frames at every
     level. A Pentatonic or Blues pin narrows Solo to the chords it fits (safeChords). */
  function freshGen() {
    const m = play();
    if (m === 'modal') return createVamp(room());
    if (!plan) return null;
    const mode = modeOf(plan);
    if (m === 'blues') return createBlues({ tonic: pcOf(plan.key), minor: mode === 'minor', level: opt.level });
    if (m === 'answer') return createAsks({ mode, scale: plan.pcs });
    if (m === 'mine') return mine && mine.sig === mineSig(plan, mineList()) ? createLoop(mine.chords) : null;
    return createProgression({ mode, scale: plan.pcs, level: opt.level,
      allowed: m === 'songs' ? safeChords(plan) : null });
  }

  // A name for a note outside the key: the blues' flat 7ths and blue notes are flats.
  const nameIn = (p, pc) => p.chords.flatMap((c) => c.names).find((n) => pcOf(n) === pc) || FLATS[pc];
  /* A stacked chord's notes spelled up from its root a letter-third at a time. */
  const stack = (root, pcs) => spellUp(root, pcs, 2);

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
    // The chord's own notes by letter, a third apart: A7's third is C sharp, not D flat.
    stack(nameIn(p, c.pcs[0]), c.pcs).forEach((n, i) => byPc.set(c.pcs[i], n));
    const suffix = (r) => (r.startsWith('i') ? 'm7' : '7');
    return { chord: c, plan: p, scale, symbol: pretty(nameIn(p, c.pcs[0])) + c.suffix, byPc,
      pal: palette(c.pcs, scale), sig: [bluesNote(tonic, minor)], sigName: 'blue', kind: 'blues', minor,
      chips: c.phrase.seq.map((r) => pretty(nameIn(p, bluesChord(tonic, r)[0])) + suffix(r)) };
  }

  /* My chords: judged against the scale loadMine() found for the chord. */
  function mineEntry(c, p = plan) {
    const byPc = new Map();
    for (const n of [...c.names, ...c.scaleNames]) if (!byPc.has(pcOf(n))) byPc.set(pcOf(n), n);
    for (const pc of c.scale) if (!byPc.has(pc)) byPc.set(pc, nameIn(p, pc));
    return { chord: c, plan: p, scale: c.scale, symbol: c.symbol, byPc, pal: palette(c.pcs, c.scale), kind: 'mine',
      scaleName: c.scaleTitle, chips: mine.chords.map((x) => x.symbol), outs: mine.chords.map((x) => x.out) };
  }

  function entryFor(c) {
    if (play() === 'modal') return modalEntry(c);
    if (play() === 'blues') return bluesEntry(c);
    if (play() === 'mine') return mineEntry(c);
    const e = entry(c);
    if (play() === 'echo') {
      const notes = lick({ pal: e.pal, size: echo.size, from: echo.from });
      echo.from = notes[notes.length - 1].key;
      e.ask = { lick: notes, played: [], gen, entry: e };
      e.lead = [notes, []];
      e.turns = ['call', 'you'];
    } else if (play() === 'answer') {
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

  // Bars per chord: Echo's chord spans the lick and your echo; Blues and Answer count bars;
  // Pop, Minor, Modal and My chords take Each chord.
  const barsFor = () => (play() === 'echo' ? 2 : play() === 'blues' || play() === 'answer' ? 1 : opt.bars);
  // How far ahead the band's chart is kept. Echo keeps it short, so a lick is written only
  // a couple of exchanges before it is played and its length can follow how you are doing.
  const ahead_ = () => (play() === 'echo' ? 4 : AHEAD);

  /* The next `n` bars of music, continuing the progression. A wander is decided at a
     phrase boundary, so the new key starts on a phrase's first chord -- marked, so Stop
     can tell a key that was written ahead from one that was heard. */
  function compose(n) {
    const out = [];
    while (out.length < n) {
      const upcoming = prog.peek();
      let moved = false;
      if (play() === 'songs' && upcoming.step === 0 && upcoming.pass === 1) {
        // The phrases finished in this key: the stream's first phrase starting makes it 0.
        phrasesInKey++;
        if (wanderDue(opt.level, opt.wander, phrasesInKey)) moved = wander();
      }
      const c = prog.next();
      // A vamp is two chords: Modal counts a time through every fourth time round it.
      if (c.step === 0 && (c.cycle ?? 0) % 4 === 0) times++;
      const e = entryFor(c);
      if (moved) e.wandered = true;
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

  /* A new key at a phrase boundary: Wander's, or level 4's every second phrase. Only to a
     key already loaded (with its pin), because the chart is being written now. Pop and
     Minor follow it to the relative key's side. True if it moved. */
  function wander() {
    const choices = neighbours(opt.key, opt.level).filter((x) => plans.has(x.k.id)
      && (opt.pin === 'off' || pins.has(`${x.k.id}:${opt.pin}`)));
    if (!choices.length) return false;
    const total = choices.reduce((a, x) => a + x.w, 0);
    let r = Math.random() * total;
    const pick = choices.find((x) => (r -= x.w) < 0) || choices[0];
    const before = new Set(plan.pcs);
    plan = plans.get(pick.k.id);
    opt.key = pick.k.id;
    if (opt.music === 'pop' || opt.music === 'minor') opt.music = isMajor(plan) ? 'pop' : 'minor';
    times = -1;
    fadeFrom = 0;
    newGen();
    phrasesInKey = 0;            // the new key's first phrase is starting now
    const added = plan.pcs.filter((pc) => !before.has(pc));
    const names = plan.chords.flatMap((c) => c.names);
    const what = added.length
      ? `new note: ${added.map((pc) => pretty(names.find((n) => pcOf(n) === pc) || '')).join(', ')}`
      : 'same notes, new home';
    toast(`Coming up: ${plan.title} — ${what}`, 'good');
    prefetchNeighbours();
    return true;
  }

  /* A neighbour that will not load is one fewer place to go, never an error. */
  function prefetchNeighbours() {
    const pin = opt.pin;
    return Promise.all(neighbours(opt.key).map((x) => loadPlan(x.k.id)
      .then((p) => (pin !== 'off' ? loadPin(p, pin) : null)).catch(() => {})));
  }

  async function setKey(id) {
    opt.key = id;
    keySelect.value = id;
    save();
    if (!(await ready()) || destroyed || opt.key !== id) return;
    times = -1;
    fadeFrom = 0;
    newGen();
    paintMine();
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
      if (!(await ready()) || destroyed) return;
      // A conversation starts with the band's turn, not halfway through the last one; and a
      // stream made for other settings (a click on the way to Start) is made again.
      if (DUET.has(play()) || !prog || progFor !== genKey()) newGen();
      if (!prog) return;
      onStart?.();
      times = -1;
      fadeFrom = 0;
      pending = verdict = null;
      overCall = false;
      Object.assign(echo, { size: 3, streak: 0, misses: 0, from: 72 });
      chart = compose(ahead_() + 4);
      const r = await api.post('/api/band/start', {
        chart: wire(chart), style: opt.style, bpm: opt.bpm, volume: opt.volume, drums: opt.drums,
        ...parts(), comp_program: opt.compSound, bass_program: opt.bassSound,
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
    settle();
    idle();
  }

  /* Stopped. A key change is written into the chart bars before it is heard, so the key
     the composer is in may be one nobody heard: go back to the key before the first such
     change still ahead -- the one you heard, or one you picked that had not come round
     yet -- and save it, so the controls, the settings and the next Start agree. The next
     Start opens a fresh progression in it, on I. */
  function settle() {
    const at = Math.max(0, shownBar);
    const j = chart.findIndex((e, i) => i > at && e.wandered);
    const p = j > 0 ? chart[j - 1].plan : null;
    if (p && p.id !== opt.key) {
      plan = p;
      opt.key = p.id;
      if (opt.music === 'pop' || opt.music === 'minor') opt.music = isMajor(p) ? 'pop' : 'minor';
      save();
    }
    newGen();
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
      // `ahead` goes with it: paintOctave reads cross.to whenever ahead is set.
      if (cross && cross.bar >= r.from) { cross = null; ahead = false; }
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

  /* Pop / Minor / Blues / Modal / My chords. A new chord stream from the next bar the band
     has not queued, with the lights back on and the count started again. Pop and Minor
     each have their own keys, so the key moves to its relative (the same notes, the other
     home); only they have Answer and Echo, so anything else plays Solo. Blues brings the
     Shuffle with it; any style can be picked after. */
  function setMusic(id) {
    opt.music = id;
    if (id !== 'pop' && id !== 'minor') opt.game = 'solo';
    const k = keyOf(opt.key);
    if ((id === 'pop' && !isMajor(k)) || (id === 'minor' && isMajor(k))) opt.key = relative(k)?.id ?? opt.key;
    save();
    applyMode();
    if (id === 'blues' && opt.style !== 'shuffle') pickStyle('shuffle');
    restart();
    if (playing) configure(parts());
  }

  /* Solo / Answer / Echo: what you do over Pop or Minor. */
  function setGame(id) {
    opt.game = id;
    save();
    applyMode();
    restart();
    if (playing) configure(parts());
  }

  /* A level is a preset you pick: a new stream at it, from the next free bar. Answer asks
     the same questions at every level, so there it changes nothing and drops nothing. */
  function setLevel(n) {
    opt.level = n;
    save();
    applyMode();
    if (play() !== 'answer') restart();
  }

  /* Off / Key / Pentatonic / Blues. A Pentatonic or Blues pin changes which chords Solo
     plays, and any pin the scale your chords are judged by: those start the stream again.
     Everything else -- Off and Key, and any pin in Answer, Echo and Blues -- only changes
     the drawing, and keeps the exchange in progress and the count. */
  function setPin(id) {
    const filtered = (x) => opt.music === 'mine' || (play() === 'songs' && (x === 'penta' || x === 'blues'));
    const was = opt.pin;
    opt.pin = id;
    save();
    if (filtered(was) || filtered(id)) { restart(); return; }
    ready().then((ok) => {
      if (!ok) return;
      shownBar = -1;
      if (!playing) idle();
    });
  }

  function setBars(id) {
    opt.bars = id;
    save();
    if (playing) rechart();
  }

  function setRoom(id) {
    opt.room = id;
    save();
    restart();
  }

  /* A fresh stream for the music, the lights back on, the count from zero -- once the
     key, its pin and your chords are in. */
  async function restart() {
    gen++;
    times = -1;
    fadeFrom = 0;
    resetCount();
    if (!(await ready())) return;
    newGen();
    paintMine();
    if (!prog) return;
    if (playing) rechart(true);
    else if (starting) stale = true;       // start() charted the old one: it recharts when up
    else idle();
  }

  /* What the stream was made for, so Start can tell a stale one. */
  const genKey = () => [play(), plan?.id, opt.level, opt.pin, opt.room, mine?.sig].join('|');
  let progFor = '';
  function newGen() {
    prog = freshGen();
    progFor = genKey();
    phrasesInKey = -1;           // no phrase of it started yet
  }

  function resetCount() {
    for (const k of Object.keys(tally)) tally[k] = 0;
    rootedFor = madeFor = countedFor = null;
    oneBar = -1;
    heard = holding = pending = verdict = null;
    overCall = false;
    Object.assign(echo, { size: 3, streak: 0, misses: 0, from: 72 });
    cross = null;
    ahead = false;
    shownCross = '?';
    paintHeard();
    paintStats();
  }

  /* Which of the band's parts play: the ones switched on, less the one you sit in for. */
  const parts = () => ({ bass: opt.bass && seat() !== 'bass', comp: opt.comp && seat() !== 'chords' });

  /* Solo / Bass / Chords. The band hears about it from its next unqueued bar; the keys
     change on the next repaint, with the way across worked out again for the new hand.
     The seat's count starts again, from the chord playing now. */
  function setSeat(id) {
    opt.seat = id;
    save();
    if (playing) configure(parts());
    cross = null;
    ahead = false;
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
    const more = compose(play() === 'echo' ? 2 : 8);
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
    paintCount(pos, bar);
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
    if (!opt.coach || play() === 'echo') { cross = null; ahead = false; return; }
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

  /* Next's countdown: the beats left until the chord changes, painted once a beat. */
  function paintCount(pos, bar) {
    const beat = Math.floor(pos * BEATS);
    if (beat === countAt) return;
    countAt = beat;
    let k = bar + 1;
    while (chart[k] && same(chart[k], chart[bar])) k++;
    el.count.textContent = chart[k] && chart[bar] ? `in ${k * BEATS - beat}` : '';
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
      // A note of the pinned scale is never "outside" (harmony.js fitOf).
      const pin = pinOf(now.plan);
      const fit = fitOf(pc, { pal: now.pal, scale: now.scale, sig: now.sig || [], pin: pin?.pcs, blue: pin?.blue });
      tally[fit]++;
      if (sig) tally.sig++;
      if (cross && !cross.hit && pc === cross.land % 12) { cross.hit = true; tally.landed++; }
      heard = { n, fit, sig, name: pretty(now.byPc.get(pc) || noteName(n)) };
      // Answer and Echo: whose turn is it? A note a hair early counts toward the bar it
      // reaches for, and one a hair late still belongs to the turn that just ended.
      if (now.ask || DUET.has(play())) {
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
    // The pinned scale: constant for the key, shown at every level but Dark; when stopped,
    // the key you are about to play in.
    const pin = opt.guide === 'dark' ? null : pinOf(now ? now.plan : plan);
    if (pin) el.oct.dataset.pin = pin.title;
    else delete el.oct.dataset.pin;
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
      const pinned = !!pin && pin.pcs.includes(pc);
      k.classList.toggle('is-pin', pinned);
      const named = !now || tone >= 0 || color || inNext || sig || pinned;
      k.children[1].textContent = named
        ? pretty((nt?.tone.includes(pc) && next.byPc.get(pc)) || now?.byPc.get(pc)
          || (pinned && pin.names.find((n) => pcOf(n) === pc)) || NAMES[pc]) : '';
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
    paintNote(now);
    if (now.plan && now.plan.id !== shownKey) applyMode(now.plan);
    const ph = now.chord.phrase;
    el.phrase.replaceChildren(
      h('span.jam__pname', null, ph.name),
      ...ph.seq.map((r, i) => {
        const live = i === now.chord.step;
        const roman = live ? now.chord.roman : r;
        // Answer marks whose bars are whose: the band's two, then yours.
        const who = now.ask?.home ? (i < 2 ? 'band' : 'you') : roman;
        const out = !!now.outs?.[i];
        return h('span.jam__chip' + (live ? '.is-on' : '') + (out ? '.is-out' : ''), out ? { title: 'Outside the key' } : null,
          h('b', null, now.chips?.[i] ?? spell(roman, now.plan).symbol), h('small', null, who));
      }),
      h('span.jam__pblurb', null, ph.blurb));
  }

  /* Under the Now card: the pinned scale's name -- or, a few bars before the key changes
     (level 4, Wander), where it is going, so the change is never a surprise. */
  function paintNote(now) {
    const p = now ? now.plan : plan;
    let to = null;
    if (now && p) {
      // Two chords' warning (four bars at least): enough to get ready, not a phrase early.
      for (let k = shownBar + 1; k < chart.length && k <= shownBar + Math.max(4, 2 * barsFor()); k++) {
        if (chart[k].plan && chart[k].plan !== p) { to = chart[k].plan; break; }
      }
    }
    const pin = opt.guide === 'dark' ? null : pinOf(to || p);
    el.note.textContent = to ? `Next: ${to.title}${pin ? ` · ${pin.title}` : ''}` : pin ? pin.title : '';
    el.note.classList.toggle('is-next', !!to);
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
    } else if (now.kind === 'mine') {
      say('Rest on ', names(now.pal.chord), `Over ${now.symbol}: ${now.scaleName}. ${level === 1
        ? 'Only the chord is lit this time round.' : 'Bright is the chord, dim adds colour.'}`);
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
    if (DUET.has(play()) && playing && turnAt(Math.max(0, shownBar)) !== 'you') {
      if (overCall) { line('rub', play() === 'echo' ? 'Listen first: copy it in the next bar.' : 'Let the question finish, then answer.'); return; }
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
    // The pinned scale over the whole keyboard: an outline (keyboard.js), kept at every
    // level so a held pinned key is never painted spicy, and not drawn in Dark.
    const pin = pinOf(now.plan);
    kb.setMarks('pin', pin ? every(pin.pcs) : []);
    kb.setMode('nopin', opt.guide === 'dark');
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
    if (play() === 'answer' && tally.asked) {
      el.stats.replaceChildren(h('span', null,
        h('b.jam__ok', null, String(tally.home)), ` of ${tally.asked} answer${s(tally.asked)} ended home · `,
        h('b', null, String(tally.root)), ' on the root'));
      return;
    }
    if (play() === 'echo' && tally.asked) {
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
        play() === 'modal' || play() === 'blues'
          ? ` · ${tally.sig} ${play() === 'blues' ? 'blue note' + s(tally.sig) : `on the ${room().sigName}`}` : '',
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
    ctx.kb?.setMode('nopin', false);
  }

  function idle() {
    el.go.textContent = 'Start the band';
    el.go.classList.add('btn--primary');
    if (shownKey !== opt.key) applyMode();       // stopped: the controls show the key you set
    const modal = play() === 'modal';
    el.nowSym.textContent = modal ? NAMES[room().tonic] : plan ? pretty(plan.key) : '';
    el.nowRole.replaceChildren(h('span.jam__muted', null, modal ? room().name
      : plan ? `${plan.title}${play() === 'blues' ? ' blues' : ''}` : ''));
    leadShown = '';
    for (const k of octKeys) k.classList.remove('is-lead');
    el.nextSym.textContent = '';
    el.nextRole.replaceChildren();
    el.count.textContent = '';
    countAt = -1;
    el.coach.replaceChildren(h('span.jam__muted', null, {
      songs: 'Start the band, then play any lit key, in any octave.',
      modal: `${room().mood} Start the band and find the ringed key.`,
      blues: 'Start the band: twelve bars, round and round. Shuffle suits it.',
      answer: 'Start the band. It asks a two-bar question; you answer it.',
      echo: 'Start the band. It plays a short lick; you play it back.',
      mine: 'Start the band: it plays your chords, round and round.',
    }[play()]));
    el.heard.replaceChildren();
    el.phrase.replaceChildren();
    cross = null;
    ahead = false;
    paintNote(null);
    paintOctave();
    paintBeats(-1);
    paintStats();
  }

  /* ── my chords: the editor ─────────────────────────────────────────────── */
  /* A slot per chord, and under the one being changed a picker: the twelve roots, the
     kind of chord, and the key's own chords for one click. Rebuilt on a click, never on
     the frame path. No browser dialogs. */
  function paintMine() {
    if (opt.music !== 'mine') return;
    const list = mineList();
    if (editing >= list.length) editing = -1;
    const known = mine && plan && mine.sig === mineSig(plan, list) ? mine.chords : null;
    fill(mineEl.slots,
      ...list.map((c, i) => h('div.jam__slot' + (i === editing ? '.is-on' : '') + (known?.[i].out ? '.is-out' : ''), null,
        h('button.jam__slotbtn', { onclick: () => { editing = editing === i ? -1 : i; paintMine(); },
          title: known?.[i].out ? 'Outside the key -- click to change' : 'Click to change' },
        h('b', null, pretty(c.root + c.quality)), h('small', null, known ? known[i].roman : '')),
        list.length > MINE_MIN ? h('button.jam__slotx', { onclick: () => editSlots('remove', i),
          title: 'Remove this chord', 'aria-label': 'Remove this chord' }, '×') : null)),
      list.length < MINE_MAX ? h('button.btn.jam__slotadd', { onclick: () => editSlots('add'),
        title: 'Add a chord' }, '+') : null,
      h('span.jam__muted', null, `${list.length} chords, ${opt.bars} bar${opt.bars === 1 ? '' : 's'} each`));
    if (editing < 0) { fill(mineEl.picker); return; }
    const c = list[editing];
    // Index is pitch class; each named the way this key spells it, for this kind of chord.
    const roots = (vocab?.roots || FLATS).map((r, pc) => (plan ? rootIn(plan, pc, c.quality) : r));
    const quals = QUALS.filter((q) => !vocab || vocab.qualities.some((v) => v.id === q.id));
    const chip = (on, onclick, ...kids) => h('button.jam__pick' + (on ? '.is-on' : ''), { onclick }, ...kids);
    fill(mineEl.picker,
      h('div.jam__prow', null, h('span.jam__lbl', null, 'Root'),
        roots.map((r) => chip(pcOf(r) === pcOf(c.root), () => editSlots('set', editing, { root: r, quality: c.quality }), pretty(r)))),
      h('div.jam__prow', null, h('span.jam__lbl', null, 'Kind'),
        quals.map((q) => chip(q.id === c.quality, () => editSlots('set', editing, { root: c.root, quality: q.id }), q.label))),
      plan ? h('div.jam__prow', null, h('span.jam__lbl', null, `In ${plan.title}`),
        quickRow(plan).map((d) => chip(pcOf(d.root) === pcOf(c.root) && d.quality === c.quality,
          () => editSlots('set', editing, { root: d.root, quality: d.quality }), h('b', null, pretty(d.symbol)),
          h('small', null, d.roman)))) : null,
      h('div.jam__prow', null, h('button.btn', { onclick: () => { editing = -1; paintMine(); } }, 'Done')));
  }

  /* The key's own chords, one click each. In a minor key, after v, the V and V7 of
     harmonic minor: the dominant minor songs actually use, and the one the Minor band
     itself plays (harmony.js PALETTE). It is judged in the key, not out of it. */
  function quickRow(p) {
    if (modeOf(p) !== 'minor') return p.chords;
    return p.chords.flatMap((d, i) => (i !== 4 ? [d] : [d,
      { ...d, quality: '', symbol: d.root, roman: 'V' },
      { ...d, quality: '7', symbol: `${d.root}7`, roman: 'V7' }]));
  }

  /* Change the loop: set one slot, add one (the key's I, open to change), or remove one.
     Playing, it is re-charted from the next bar the band has not queued. */
  async function editSlots(how, i, chord) {
    const list = mineList();
    if (how === 'set') list[i] = chord;
    else if (how === 'add' && list.length < MINE_MAX) {
      const home = plan?.chords[0];
      list.push(home ? { root: home.root, quality: home.quality } : { ...list[list.length - 1] });
      editing = list.length - 1;
    } else if (how === 'remove' && list.length > MINE_MIN) {
      list.splice(i, 1);
      editing = -1;
    } else return;
    opt.mine = list;
    save();
    paintMine();
    if (!(await ready())) return;
    newGen();
    paintMine();
    if (playing) rechart(true);
    else if (starting) stale = true;
    else idle();
  }

  /* ── persistence ───────────────────────────────────────────────────────── */
  let saveTimer = 0;
  function save() {
    paintFold();
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

  ready().then((ok) => {
    if (!ok || destroyed || starting || playing) return;
    newGen();
    paintMine();
    idle();
  });
  // The roots and qualities My chords offers come from the server, like every scale.
  getJson('/api/theory').then((v) => { vocab = v; paintMine(); }).catch(() => {});
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
