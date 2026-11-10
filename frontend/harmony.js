/* Jam's composer -- an endless chord progression that sounds like songs.
 *
 * The problem with "random chords in the key" is that it is technically never wrong and
 * musically always wrong: nothing repeats, so nothing sounds like a song, and the ear
 * never learns where home is. The problem with a looped progression is the opposite --
 * four chords forever is a ringtone.
 *
 * So the unit here is not the chord, it is the PHRASE: a real progression that real
 * songs are built on (the pop four, the doo-wop turn, ii-V-I, Pachelbel, the anime
 * "royal road"...). Each one is played twice, because that is how songs work and how
 * hands learn a shape. The second time through it may swap one chord for a neighbour --
 * a chord a third away, which shares two of its three notes and therefore does the
 * same job in a different colour. Then the next phrase is picked by how well its first
 * chord follows the last one: a chord that pulls toward home goes home.
 *
 * Nothing in here touches the DOM or the clock. Chords come in as pitch classes (the
 * spelling comes from the server -- see jam.js), randomness comes in as a function,
 * and tools/jam_check.py drives this file under node with a seeded one.
 */

// Six of the seven chords in a major key. vii° is left out: it is the one chord in the
// key that sounds unstable on its own, and a beginner landing on it hears "wrong".
// Minor keys add V -- the major five borrowed from harmonic minor -- because that is
// how minor songs actually pull home; plain v shrugs.
export const PALETTE = {
  major: ['I', 'ii', 'iii', 'IV', 'V', 'vi'],
  minor: ['i', 'III', 'iv', 'v', 'V', 'VI', 'VII'],
};

// Where each chord sits in the scale (0 = the first note).
const DEGREE = { I: 0, ii: 1, iii: 2, IV: 3, V: 4, vi: 5, i: 0, III: 2, iv: 3, v: 4, VI: 5, VII: 6 };

/* What the chord does, in words a beginner can use. "Home" sounds finished, "away"
   sounds like travelling, "pull" sounds like it wants to go home. */
export const ROLE = {
  major: { I: 'home', vi: 'home', iii: 'home', IV: 'away', ii: 'away', V: 'pull' },
  minor: { i: 'home', III: 'away', iv: 'away', VI: 'away', v: 'pull', V: 'pull', VII: 'pull' },
};

/* The phrases. Every one of these is a progression you have heard a hundred times. */
export const PHRASES = {
  major: [
    { id: 'pop', name: 'The pop progression', seq: ['I', 'V', 'vi', 'IV'], w: 3,
      blurb: 'Under hundreds of hit songs. If a song sounds familiar, it is probably this.' },
    { id: 'sad-pop', name: 'Pop, starting sad', seq: ['vi', 'IV', 'I', 'V'], w: 2.5,
      blurb: 'The same four chords as the pop progression, started on the sad one.' },
    { id: 'fifties', name: 'The fifties', seq: ['I', 'vi', 'IV', 'V'], w: 2,
      blurb: 'Doo-wop and slow-dance ballads. Ends pulling back to the start.' },
    { id: 'royal-road', name: 'The royal road', seq: ['IV', 'V', 'iii', 'vi'], w: 2,
      blurb: 'The anime and J-pop progression. Climbs, then lands somewhere bittersweet.' },
    { id: 'three-chord', name: 'Three chords', seq: ['I', 'IV', 'V', 'I'], w: 2,
      blurb: 'Blues, folk and rock were built on these three. Away, pull, home.' },
    { id: 'hopeful', name: 'Hopeful', seq: ['I', 'IV', 'vi', 'V'], w: 1.5,
      blurb: 'A lift that never quite settles, so it keeps you moving.' },
    { id: 'two-five-one', name: 'Two-five-one', seq: ['ii', 'V', 'I', 'I'], w: 1.5,
      blurb: "Jazz's favourite way home: away, pull, then rest." },
    { id: 'circle', name: 'Round the circle', seq: ['vi', 'ii', 'V', 'I'], w: 1.2,
      blurb: 'Each chord falls five notes to the next. The strongest pull there is.' },
    { id: 'canon', name: "Pachelbel's Canon", seq: ['I', 'V', 'vi', 'iii', 'IV', 'I', 'IV', 'V'], w: 1.2,
      blurb: 'The wedding classic — and the middle of the Memories chorus.' },
    { id: 'climb', name: 'The climb', seq: ['I', 'iii', 'IV', 'V'], w: 1,
      blurb: 'Leaps up from home, then climbs a step at a time to the pull.' },
  ],
  minor: [
    { id: 'epic', name: 'Epic minor', seq: ['i', 'VI', 'III', 'VII'], w: 3,
      blurb: 'Film trailers and power ballads. The pop progression, heard from the dark side.' },
    { id: 'andalusian', name: 'The Andalusian walk', seq: ['i', 'VII', 'VI', 'V'], w: 2.5,
      blurb: 'Four chords walking down a step at a time. Flamenco, and Hit the Road Jack.' },
    { id: 'drama', name: 'Drama', seq: ['i', 'VI', 'iv', 'V'], w: 2,
      blurb: 'Sinks, then pulls hard back to the start.' },
    { id: 'watchtower', name: 'Rocking', seq: ['i', 'VII', 'VI', 'VII'], w: 2,
      blurb: 'Down two steps and back up — All Along the Watchtower rocks on this.' },
    { id: 'minor-three', name: 'Minor three chords', seq: ['i', 'iv', 'v', 'i'], w: 1.5,
      blurb: 'The three-chord song in a minor key. Darker, softer pull.' },
    { id: 'rise', name: 'The rise', seq: ['VI', 'VII', 'i', 'i'], w: 1.5,
      blurb: 'Climbs two steps into home. The end of a film cue.' },
    { id: 'minor-circle', name: 'Minor circle', seq: ['i', 'iv', 'VII', 'III'], w: 1.2,
      blurb: 'Falls five notes each time and lands on the bright chord.' },
    { id: 'home-and-back', name: 'Home and back', seq: ['i', 'iv', 'i', 'V'], w: 1,
      blurb: 'The simplest minor phrase: leave, return, then pull.' },
  ],
};

/* Pitch classes for one chord of a key. `scale` is the key's seven pitch classes. */
export function chordPcs(scale, roman) {
  const d = DEGREE[roman];
  const pcs = [scale[d], scale[(d + 2) % 7], scale[(d + 4) % 7]];
  // Minor V: the scale's own fifth chord is minor, so raise its third a half step.
  if (roman === 'V' && (pcs[1] - pcs[0] + 12) % 12 === 3) pcs[1] = (pcs[1] + 1) % 12;
  return pcs;
}

/* Chords sharing two of three notes do the same job in a different colour -- that is
   why I and vi, or IV and ii, can stand in for each other. Computed, not tabled. */
export function siblings(scale, mode, roman) {
  const mine = chordPcs(scale, roman);
  return PALETTE[mode].filter((r) => r !== roman
    && chordPcs(scale, r).filter((pc) => mine.includes(pc)).length === 2);
}

// How well one chord follows another, by what they do. Pull goes home; away goes on.
const FOLLOW = {
  home: { home: 0.7, away: 1, pull: 0.8 },
  away: { home: 1, away: 0.6, pull: 1 },
  pull: { home: 1.4, away: 0.35, pull: 0.15 },
};

function pickWeighted(items, weight, rng) {
  const ws = items.map(weight);
  const total = ws.reduce((a, b) => a + b, 0);
  if (!(total > 0)) return items[0];
  let r = rng() * total;
  for (let i = 0; i < items.length; i++) {
    r -= ws[i];
    if (r < 0) return items[i];
  }
  return items[items.length - 1];
}

/**
 * An endless stream of chords in one key.
 *
 *   const p = createProgression({ mode: 'major', scale: [0,2,4,5,7,9,11], rng });
 *   p.next()  ->  { roman, pcs, role, phrase, step, pass, varied }
 *
 * `phrase` is the PHRASES entry being played, `step` is the chord's place in it, and
 * `pass` is 1 or 2. `varied` marks a chord swapped in on the second pass.
 */
export function createProgression({ mode = 'major', scale, rng = Math.random }) {
  const phrases = PHRASES[mode];
  const roles = ROLE[mode];
  const recent = [];          // the last few phrase ids, so none comes straight back
  let queue = [];
  let last = null;            // the chord before the queue

  function plan() {
    const avoid = new Set(recent.slice(-3));
    let pool = phrases.filter((p) => !avoid.has(p.id));
    if (!pool.length) pool = phrases;
    // The very first phrase starts on the key's own chord, so the ear learns where home
    // is before anything leaves it. vi is "home" too, but it is not THE home.
    if (!last) pool = pool.filter((p) => p.seq[0] === PALETTE[mode][0]);
    const phrase = pickWeighted(pool, (p) => {
      if (!last) return p.w;
      let w = p.w * FOLLOW[roles[last]][roles[p.seq[0]]];
      if (p.seq[0] === last) w *= 0.5;     // the same chord across the seam stalls
      return w;
    }, rng);
    recent.push(phrase.id);
    if (recent.length > 6) recent.shift();

    const passes = phrase.seq.length >= 8 ? 1 : 2;
    const out = [];
    for (let pass = 1; pass <= passes; pass++) {
      const seq = phrase.seq.slice();
      const varied = new Array(seq.length).fill(false);
      if (pass === 2 && rng() < 0.45) {
        // Never the first chord (it names the phrase) and never a final pull (it is
        // what sends you on). Never a swap that repeats a neighbour.
        const spots = [];
        for (let i = 1; i < seq.length; i++) {
          if (i === seq.length - 1 && roles[seq[i]] === 'pull') continue;
          spots.push(i);
        }
        const i = spots[Math.floor(rng() * spots.length)];
        if (i !== undefined) {
          const options = siblings(scale, mode, seq[i])
            .filter((r) => r !== seq[i - 1] && r !== seq[i + 1]);
          if (options.length) {
            seq[i] = options[Math.floor(rng() * options.length)];
            varied[i] = true;
          }
        }
      }
      seq.forEach((roman, step) => out.push({
        roman, pcs: chordPcs(scale, roman), role: roles[roman],
        phrase, step, pass, varied: varied[step],
      }));
    }
    return out;
  }

  return {
    next() {
      if (!queue.length) queue = plan();
      const c = queue.shift();
      last = c.roman;
      return c;
    },
    /* The chord that will come after the current one, without consuming it. */
    peek() {
      if (!queue.length) queue = plan();
      return queue[0];
    },
    /* Drop the rest of this phrase and start a fresh one. */
    skipPhrase() { queue = []; },
  };
}

/* ── what sounds good, and how to cross a change ─────────────────────────── */

/**
 * The notes that sound good over one chord, as pitch classes -- true in every octave.
 *
 *   chord: the chord's own notes. Rest on these; nothing in the band rubs against them.
 *   color: the rest of the key that sounds good held over this chord -- a 2nd, a 6th, a
 *          7th. Use them to move between chord notes.
 *   rub:   the key's notes a half step ABOVE a chord note. F over a C chord leans on E
 *          and wants to fall to it; held, it sounds like a mistake. Passing through is
 *          fine, landing is not, so these are left unlit.
 *
 * `scale` is the key as this chord is judged against it (minor V's raised third already
 * in place -- see entry() in jam.js).
 */
export function palette(chord, scale) {
  const tones = [...new Set(chord)];
  const above = new Set(tones.map((pc) => (pc + 1) % 12));
  const rest = scale.filter((pc) => !tones.includes(pc));
  return {
    chord: tones,
    color: rest.filter((pc) => !above.has(pc)),
    rub: rest.filter((pc) => above.has(pc)),
  };
}

/**
 * How to cross into the next chord the way a good player does, decided once per change.
 *
 *   keep: the notes in both chords. Hold one through the change and the new chord
 *         arrives underneath a note that does not move -- the smoothest change there is.
 *   land: the one key to be on when the chord changes: a kept note if one is about as
 *         close to your hand as anything else, otherwise the next chord's nearest note,
 *         which is usually a step away.
 *
 * `from` is the last note you played (MIDI), or null before you have played. Returns
 * { keep, land, why }, `land` a MIDI number between `low` and `high`.
 */
export function crossing(chord, next, from = null, { low = 57, high = 84 } = {}) {
  const keep = chord.filter((pc) => next.includes(pc));
  const at = Math.min(high, Math.max(low, from ?? 66));
  const nearest = (pcs) => {
    for (let d = 0; d <= 12; d++) {
      for (const m of [at - d, at + d]) {
        if (m >= low && m <= high && pcs.includes(((m % 12) + 12) % 12)) return m;
      }
    }
    return null;
  };
  const held = keep.length ? nearest(keep) : null;
  const moved = nearest(next);
  if (held !== null && Math.abs(held - at) <= Math.abs(moved - at) + 2) {
    return { keep, land: held, why: 'it is in both chords, so hold it and let the change happen under it' };
  }
  const dist = Math.abs(moved - at);
  return { keep, land: moved,
    why: dist <= 2 ? 'one step from where you are, into the next chord' : 'the next chord\'s nearest note' };
}

/* ── sitting in: what the seat you take plays ───────────────────────────── */

// Where each seat's hand plays, as MIDI: the bass under middle C, the chords either side of it.
export const SEAT_ZONE = { bass: [36, 55], chords: [48, 72] };

/**
 * What the player in `seat` plays over one chord, as pitch classes: `tone` is drawn
 * bright, `color` dim. `pal` is the chord's palette(). Solo: the chord and the notes that
 * add colour. Bass: the root, and its fifth. Chords: the chord's own notes -- a set to
 * choose from in any octave, never one shape to copy.
 */
export function seatTarget(seat, pal) {
  if (seat === 'bass') {
    const root = pal.chord[0];
    const five = (root + 7) % 12;
    return { tone: [root], color: pal.chord.includes(five) ? [five] : [] };
  }
  if (seat === 'chords') return { tone: pal.chord, color: [] };
  return { tone: pal.chord, color: pal.color };
}

/**
 * The keys a seat lights, { low, high } in MIDI: the whole keyboard for Solo, the seat's
 * register otherwise, clamped to the keyboard -- or, when the keyboard does not reach
 * that register, its bottom end the same span wide. Never less than an octave, so every
 * note of a chord has a key.
 */
export function seatZone(seat, low, high) {
  const z = SEAT_ZONE[seat];
  if (!z) return { low, high };
  const lo = Math.max(low, z[0]);
  const hi = Math.min(high, z[1]);
  return hi - lo >= 11 ? { low: lo, high: hi } : { low, high: Math.min(high, low + z[1] - z[0]) };
}

/* Every key from `low` to `high` whose pitch class is in `pcs`. */
export function keysIn(pcs, { low, high }) {
  const out = [];
  for (let m = low; m <= high; m++) if (pcs.includes(m % 12)) out.push(m);
  return out;
}

/* ── Lights Out: how much of the guide to draw ───────────────────────────── */

/**
 * How much of Jam's guide to draw over one chord, from 0 (all of it) to 3 (the beat).
 *
 *   0: everything -- the chord bright, the colour dim, the way across each change.
 *   1: the chord's own notes and nothing else.
 *   2: the chord's name and what it does (home, away, pull), no keys lit.
 *   3: the beat.
 *
 * `guide` is 'full' (always 0), 'dark' (always 3) or 'fading'. Fading goes down one
 * level each time through a progression: `times` counts those since the lights last
 * came back (a new key, a new progression, Start), 0 the first time. Counted by times
 * through and not by pass, because a long phrase like the Canon is played once, and
 * keyed on pass it would skip the second level.
 */
export function guideLevel(times, guide) {
  if (guide === 'dark') return 3;
  if (guide !== 'fading') return 0;
  return Math.max(0, Math.min(3, Math.floor(times) || 0));
}

/* ── the modal room: one vamp, one note that makes it ───────────────────── */

/* Each room is two chords and a home note, all on the white keys -- the same seven notes
   as C major every time. What changes is where home is, and that alone changes the
   mood. `sig` is the note that makes the mode (semitones above home): the one a C major
   ear does not expect. A vamp is the two chords back and forth, forever: nothing to
   follow, so all your attention goes to colour. */
export const ROOMS = [
  { id: 'dorian', name: 'D Dorian', tonic: 2, steps: [0, 2, 3, 5, 7, 9, 10], sig: 9, sigName: '♮6',
    mood: 'Minor, but hopeful. Oye Como Va, So What, a lot of funk.',
    sigWhy: 'minor with a bright 6th. Lean on it.',
    vamp: [{ at: 0, iv: [0, 3, 7, 10], suffix: 'm7', roman: 'i7', role: 'home' },
      { at: 5, iv: [0, 4, 7, 10], suffix: '7', roman: 'IV7', role: 'away' }] },
  { id: 'mixolydian', name: 'G Mixolydian', tonic: 7, steps: [0, 2, 4, 5, 7, 9, 10], sig: 10, sigName: '♭7',
    mood: 'Major, but loose and bluesy. Classic rock lives here.',
    sigWhy: 'major with a flat 7th. No pull home.',
    vamp: [{ at: 0, iv: [0, 4, 7], suffix: '', roman: 'I', role: 'home' },
      { at: 10, iv: [0, 4, 7], suffix: '', roman: '♭VII', role: 'away' }] },
  { id: 'lydian', name: 'F Lydian', tonic: 5, steps: [0, 2, 4, 6, 7, 9, 11], sig: 6, sigName: '♯4',
    mood: 'Major, floating, dreamy. Film scores and space.',
    sigWhy: 'major with a raised 4th. It floats.',
    vamp: [{ at: 0, iv: [0, 4, 7, 11], suffix: 'maj7', roman: 'Imaj7', role: 'home' },
      { at: 2, iv: [0, 4, 7], suffix: '', roman: 'II', role: 'away', bass: 0 }] },
  { id: 'aeolian', name: 'A Aeolian', tonic: 9, steps: [0, 2, 3, 5, 7, 8, 10], sig: 8, sigName: '♭6',
    mood: 'Plain minor: sad and serious.',
    sigWhy: 'the dark 6th. Let it fall to the 5th.',
    vamp: [{ at: 0, iv: [0, 3, 7], suffix: 'm', roman: 'i', role: 'home' },
      { at: 8, iv: [0, 4, 7], suffix: '', roman: '♭VI', role: 'away' }] },
  { id: 'phrygian', name: 'E Phrygian', tonic: 4, steps: [0, 1, 3, 5, 7, 8, 10], sig: 1, sigName: '♭2',
    mood: 'Dark and Spanish. Metal riffs and flamenco.',
    sigWhy: 'a half step over home. Lean, then fall back.',
    vamp: [{ at: 0, iv: [0, 3, 7], suffix: 'm', roman: 'i', role: 'home' },
      { at: 1, iv: [0, 4, 7], suffix: '', roman: '♭II', role: 'away' }] },
];

export const roomScale = (room) => room.steps.map((s) => (room.tonic + s) % 12);
export const roomSig = (room) => (room.tonic + room.sig) % 12;

/* The room's vamp as an endless stream, shaped like createProgression's chords. */
export function createVamp(room) {
  const seq = room.vamp.map((c) => c.roman);
  const phrase = { id: room.id, name: `${room.name} vamp`, seq, blurb: room.mood };
  let i = 0;
  const chord = (k) => {
    const c = room.vamp[k];
    const root = (room.tonic + c.at) % 12;
    return { roman: c.roman, pcs: c.iv.map((s) => (root + s) % 12), root,
      bass: c.bass === undefined ? root : (room.tonic + c.bass) % 12, suffix: c.suffix,
      role: c.role, phrase, step: k, pass: 1, varied: false, cycle };
  };
  let cycle = 0;              // times round the vamp
  return {
    next() {
      const c = chord(i);
      i = (i + 1) % room.vamp.length;
      if (!i) cycle++;
      return c;
    },
    peek() { return chord(i); },
    skipPhrase() { i = 0; cycle = 0; },
  };
}

/* ── the blues: twelve bars, and its own idea of what sounds good ──────── */

export const BLUES_FORMS = {
  major: [
    { id: 'blues', name: '12-bar blues', w: 2,
      seq: ['I7', 'I7', 'I7', 'I7', 'IV7', 'IV7', 'I7', 'I7', 'V7', 'IV7', 'I7', 'V7'],
      blurb: 'Four bars home, two away and two home, then the turnaround: pull, away, home, pull.' },
    { id: 'quick-change', name: '12-bar blues, quick change', w: 1,
      seq: ['I7', 'IV7', 'I7', 'I7', 'IV7', 'IV7', 'I7', 'I7', 'V7', 'IV7', 'I7', 'V7'],
      blurb: 'The same twelve bars with a visit to IV in bar 2.' },
  ],
  minor: [
    { id: 'minor-blues', name: 'Minor blues', w: 1,
      seq: ['i7', 'i7', 'i7', 'i7', 'iv7', 'iv7', 'i7', 'i7', 'VI7', 'V7', 'i7', 'V7'],
      blurb: 'The blues in a minor key: The Thrill Is Gone. The turnaround sinks a half step into V.' },
  ],
};
// Root (semitones over home) and shape of each blues chord: every one a seventh chord,
// because in the blues even home has a 7th on it.
const BLUES_CHORD = { I7: [0, 0], IV7: [5, 0], V7: [7, 0], VI7: [8, 0], i7: [0, 1], iv7: [5, 1] };
const SHAPE = [[0, 4, 7, 10], [0, 3, 7, 10]];
const BLUES_ROLE = { I7: 'home', i7: 'home', IV7: 'away', iv7: 'away', VI7: 'away', V7: 'pull' };
const BLUES = [0, 3, 5, 6, 7, 10];          // the blues scale, over home

export function bluesChord(tonic, roman) {
  const [at, minor] = BLUES_CHORD[roman];
  return SHAPE[minor].map((s) => (tonic + at + s) % 12);
}

/* The blue note: the minor 3rd sung over a major key -- or, in a minor blues, where the
   3rd is already minor, the flat 5th. */
export const bluesNote = (tonic, minor) => (tonic + (minor ? 6 : 3)) % 12;

/**
 * What a blues chord is judged against: its own scale (Mixolydian over a 7th chord,
 * Dorian over a minor 7th) together with the blues scale on home. Judged against the
 * key alone, the blue note -- the most blues note there is -- came out "outside the
 * key". palette() then finds the rubs the usual way.
 */
export function bluesScale(tonic, roman, keyMinor = false) {
  const [at, minor] = BLUES_CHORD[roman];
  // In a minor blues, V7 is harmonic minor's: its own scale keeps the key's flat 3rd and 6th,
  // where Mixolydian would light the major ones. VI7 is Lydian dominant, which keeps the key's 2nd.
  const shape = minor ? [0, 2, 3, 5, 7, 9, 10]
    : keyMinor && roman === 'V7' ? [0, 1, 4, 5, 7, 8, 10]
      : keyMinor && roman === 'VI7' ? [0, 2, 4, 6, 7, 9, 10] : [0, 2, 4, 5, 7, 9, 10];
  const own = shape.map((s) => (tonic + at + s) % 12);
  return [...new Set([...own, ...BLUES.map((s) => (tonic + s) % 12)])].sort((a, b) => a - b);
}

/* Chorus after chorus, one chord a bar. */
export function createBlues({ tonic, minor = false, rng = Math.random }) {
  const forms = BLUES_FORMS[minor ? 'minor' : 'major'];
  let form = null, i = 0;
  const chord = () => {
    if (!form) form = pickWeighted(forms, (f) => f.w, rng);
    const roman = form.seq[i];
    return { roman, pcs: bluesChord(tonic, roman), role: BLUES_ROLE[roman],
      suffix: roman.startsWith('i') ? 'm7' : '7', phrase: form, step: i, pass: 1, varied: false };
  };
  return {
    next() {
      const c = chord();
      if (++i >= form.seq.length) { i = 0; form = null; }
      return c;
    },
    peek() { return chord(); },
    skipPhrase() { i = 0; form = null; },
  };
}

/* ── a melody of the band's own: the question, and the lick to echo ─────── */

// Where the notes fall in one bar, in sixteenths, by how many there are. Mostly eighths,
// starting on or near the beat, finished by beat 4 so there is a breath before your turn.
export const RHYTHMS = {
  2: [[0, 8], [0, 6], [4, 8], [0, 4]],
  3: [[0, 4, 8], [0, 2, 4], [0, 6, 8], [4, 6, 8], [0, 4, 6], [2, 4, 8]],
  4: [[0, 2, 4, 8], [0, 4, 6, 8], [0, 2, 4, 6], [0, 3, 6, 8], [2, 4, 6, 8], [0, 4, 8, 10]],
  5: [[0, 2, 4, 6, 8], [0, 2, 4, 8, 10], [0, 4, 6, 8, 10], [2, 4, 6, 8, 10], [0, 2, 3, 4, 8]],
  6: [[0, 2, 4, 6, 8, 10], [0, 2, 4, 6, 8, 12], [0, 1, 2, 4, 6, 8], [0, 2, 4, 5, 6, 8]],
};
// The second bar of a question: few notes, the last one held. It is left hanging.
const QUESTION_END = [[0, 4], [0, 2, 4], [0], [0, 6, 8], [2, 4, 8], [0, 2, 8]];
const pick = (xs, rng) => xs[Math.floor(rng() * xs.length) % xs.length];

/**
 * A singable line: mostly steps, the odd small leap, chord notes on the strong beats,
 * heading one way for a while before turning. `at(step)` gives { chord, ok } for the
 * chord sounding at that sixteenth; `end` is the pitch classes the last note must be on.
 * Returns MIDI numbers, one per onset, all between `low` and `high`.
 */
export function line({ onsets, at, end, rng = Math.random, low = 64, high = 81, from = 72 }) {
  const out = [];
  let dir = rng() < 0.5 ? -1 : 1;
  const near = (pcs, to, skip) => {
    for (let d = 0; d <= 24; d++) {
      for (const m of [to - d, to + d]) {
        if (m >= low && m <= high && m !== skip && pcs.includes(m % 12)) return m;
      }
    }
    return Math.min(high, Math.max(low, to));
  };
  onsets.forEach((s, i) => {
    const { chord, ok } = at(s);
    const last = i === onsets.length - 1;
    const pool = last ? end : i === 0 ? chord : ok;
    const prev = out[i - 1];
    if (i > 0 && rng() < 0.25) dir = -dir;
    if (prev >= high - 2) dir = -1;
    if (prev <= low + 2) dir = 1;
    const cands = [];
    for (let m = low; m <= high; m++) {
      if (!pool.includes(m % 12)) continue;
      if (i === 0) {
        const d = Math.abs(m - from);
        if (d <= 6) cands.push([m, 7 - d]);
        continue;
      }
      const d = m - prev, ad = Math.abs(d);
      if (ad === 0 || ad > (last ? 7 : 5)) continue;
      let w = ad <= 2 ? 4 : ad <= 4 ? 2 : 1;
      if (Math.sign(d) === dir) w *= 1.6;
      if (!last && s % 8 === 0 && chord.includes(m % 12)) w *= 2.5;
      // The note before the last heads for a step away from where the line has to end.
      if (i === onsets.length - 2 && end.some((pc) => [1, 2].some((k) => (m + k) % 12 === pc
        || (m - k + 12) % 12 === pc))) w *= 2;
      cands.push([m, w]);
    }
    out.push(cands.length ? pickWeighted(cands, (c) => c[1], rng)[0] : near(pool, prev ?? from, prev));
  });
  return out;
}

/**
 * Echo's lick: one bar, `size` notes, only notes that sound good over the chord, ending
 * on one of the chord's own. `pal` is the chord's palette(). Returns [{ step, len, key }].
 */
export function lick({ pal, size = 3, rng = Math.random, low = 64, high = 81, from = 72 }) {
  const n = Math.max(2, Math.min(6, size));
  const onsets = pick(RHYTHMS[n], rng);
  const ok = pal.chord.concat(pal.color);
  const keys = line({ onsets, at: () => ({ chord: pal.chord, ok }), end: pal.chord, rng, low, high, from });
  return onsets.map((step, i) => ({ step, key: keys[i],
    len: i + 1 < n ? onsets[i + 1] - step : Math.max(2, Math.min(14, step + 6) - step) }));
}

/**
 * Finish the Phrase's question: two bars over two chords, left hanging -- it ends on a
 * note of the second chord that is NOT in the home chord, so it sounds unfinished and
 * asks for an answer. `pals` are the two chords' palettes, `home` the home chord's
 * notes. Returns [{ step, len, key }], steps 0-31 across both bars.
 */
export function question({ pals, home, rng = Math.random, low = 64, high = 81, from = 72 }) {
  const [p1, p2] = pals;
  const onsets = pick(RHYTHMS[pick([3, 4, 5], rng)], rng)
    .concat(pick(QUESTION_END, rng).map((s) => s + 16));
  let end = p2.chord.filter((pc) => !home.includes(pc));
  if (!end.length) end = p2.chord.filter((pc) => pc !== home[0]);
  const at = (s) => {
    const p = s < 16 ? p1 : p2;
    return { chord: p.chord, ok: p.chord.concat(p.color) };
  };
  const keys = line({ onsets, at, end, rng, low, high, from });
  return onsets.map((step, i) => ({ step, key: keys[i],
    len: i + 1 < onsets.length ? onsets[i + 1] - step : Math.max(2, 30 - step) }));
}

/* The question-and-answer frames: two bars of question over the first pair, two of
   answer over the second -- which always ends at home, so there is somewhere to land. */
export const ASKS = {
  major: [{ call: ['I', 'V'], answer: ['V', 'I'], w: 3 }, { call: ['I', 'IV'], answer: ['V', 'I'], w: 2 },
    { call: ['vi', 'IV'], answer: ['V', 'I'], w: 1.5 }, { call: ['IV', 'V'], answer: ['IV', 'I'], w: 1 }],
  minor: [{ call: ['i', 'V'], answer: ['V', 'i'], w: 3 }, { call: ['i', 'iv'], answer: ['V', 'i'], w: 2 },
    { call: ['VI', 'VII'], answer: ['V', 'i'], w: 1.5 }, { call: ['i', 'VII'], answer: ['iv', 'i'], w: 1 }],
};

/* Four chords at a time: question, question, answer, answer. Shaped like
   createProgression's, plus `turn` ('call' | 'you') and `ask`, one object per exchange. */
export function createAsks({ mode = 'major', scale, rng = Math.random }) {
  const roles = ROLE[mode];
  let queue = [];
  const plan = () => {
    const f = pickWeighted(ASKS[mode], (x) => x.w, rng);
    const seq = f.call.concat(f.answer);
    const phrase = { id: `ask-${seq.join('-')}`, name: 'Question and answer', seq,
      blurb: 'The band asks for two bars. You answer for two, and end somewhere that sounds finished.' };
    const ask = { phrase };
    return seq.map((roman, step) => ({ roman, pcs: chordPcs(scale, roman), role: roles[roman],
      phrase, step, pass: 1, varied: false, ask, turn: step < 2 ? 'call' : 'you' }));
  };
  return {
    next() { if (!queue.length) queue = plan(); return queue.shift(); },
    peek() { if (!queue.length) queue = plan(); return queue[0]; },
    skipPhrase() { queue = []; },
  };
}

/**
 * How close an echo came: the most notes of the lick you played in its order (the
 * longest common subsequence of pitch classes, so any octave counts). `exact` is note
 * for note: the same notes, in order, and nothing else.
 */
export function echoScore(want, got) {
  const a = want.map((n) => ((n % 12) + 12) % 12);
  const b = got.map((n) => ((n % 12) + 12) % 12);
  const t = Array.from({ length: a.length + 1 }, () => new Array(b.length + 1).fill(0));
  for (let i = 1; i <= a.length; i++) {
    for (let j = 1; j <= b.length; j++) {
      t[i][j] = a[i - 1] === b[j - 1] ? t[i - 1][j - 1] + 1 : Math.max(t[i - 1][j], t[i][j - 1]);
    }
  }
  const matched = t[a.length][b.length];
  return { matched, of: a.length, exact: matched === a.length && b.length === a.length };
}
