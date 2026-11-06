/* Chord Flow's composer -- an endless chord progression that sounds like songs.
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
 * spelling comes from the server -- see flow.js), randomness comes in as a function,
 * and tools/flow_check.py drives this file under node with a seeded one.
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
      blurb: 'The wedding classic -- and the middle of the Memories chorus.' },
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
      blurb: 'Down two steps and back up -- All Along the Watchtower rocks on this.' },
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

/* ── where to put your hands ─────────────────────────────────────────────── */

const mean = (xs) => xs.reduce((a, b) => a + b, 0) / xs.length;

/**
 * The right-hand shape: three notes, close together, as near as possible to where your
 * hand already is. This is the whole trick of playing chords smoothly -- C to Am to F
 * is one finger moving at a time, not the hand jumping -- and a beginner never finds it
 * alone, because every chord chart draws every chord from its root.
 */
export function voiceRight(pcs, prev = null, { low = 55, high = 79, center = 66 } = {}) {
  const shapes = [];
  for (let inv = 0; inv < 3; inv++) {
    const order = [pcs[inv % 3], pcs[(inv + 1) % 3], pcs[(inv + 2) % 3]];
    for (let base = low; base <= high; base++) {
      if (base % 12 !== order[0]) continue;
      const notes = [base];
      for (let k = 1; k < 3; k++) {
        let n = notes[k - 1] + 1;
        while (n % 12 !== order[k]) n++;
        notes.push(n);
      }
      if (notes[2] <= high) shapes.push({ notes, inv });
    }
  }
  const cost = (s) => {
    // A little pull toward the middle stops a long run of smooth moves from walking the
    // hand off the end of the keyboard.
    const drift = Math.abs(mean(s.notes) - center) * (prev ? 0.35 : 1);
    if (!prev) return drift;
    return s.notes.reduce((a, n, i) => a + Math.abs(n - prev[i]), 0) + drift;
  };
  // The first chord is the textbook shape, root at the bottom: it is the one you can
  // check against a chord chart. Every chord after it moves as little as it can.
  const pool = prev ? shapes : shapes.filter((s) => s.inv === 0 && s.notes[0] < low + 12);
  pool.sort((a, b) => cost(a) - cost(b));
  return pool[0].notes;
}

/* The left hand: the chord's root, low, and as close to the last one as it can be. */
export function voiceLeft(rootPc, prev = null, { low = 40, high = 51 } = {}) {
  const options = [];
  for (let n = low; n <= high; n++) if (n % 12 === rootPc) options.push(n);
  const target = prev ?? 46;
  options.sort((a, b) => Math.abs(a - target) - Math.abs(b - target) || a - b);
  return options[0];
}

/* The keys the left hand is shown: one note, root and fifth, or none. Root and fifth
   can reach up into the right hand's lowest key, so the pair drops an octave when it
   would -- `root` itself never moves, because the next bass leads from it. */
export function leftKeys(root, style, rhLow) {
  if (style === 'off') return [];
  if (style !== 'fifth') return [root];
  const base = root + 7 >= rhLow ? root - 12 : root;
  return [base, base + 7];
}
