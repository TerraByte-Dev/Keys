/* Ghost mode -- the piece, falling, with no sound of its own.
 *
 * This module is the CLOCK and the RULES. roll.js is the pixels. The split matters:
 * everything here is arithmetic over a note list and can be checked without a canvas
 * (tools/ghost_check.py does exactly that), and everything there is drawing that can
 * be looked at without a piano.
 *
 * Four things worth knowing.
 *
 * **There is one clock, and it is the frame loop.** roll.js calls advance(dt) once
 * per RAF tick with the dt it already computes. No interval, no second timer, no
 * sequencer. A backgrounded tab therefore stops the piece rather than letting it run
 * away in the dark, which is the behaviour you want from something you are playing
 * along with.
 *
 * **No audio, and no backend call, deliberately.** Ghost mode never touches
 * FluidSynth and never posts to /api/scores/{id}/transport/*. That endpoint returns
 * 503 when the engine is down; this works with no SoundFont, no audio device and the
 * headphones unplugged, because the only thing making sound is you. If anyone ever
 * "helpfully" wires this to the score transport, that property is gone.
 *
 * **The held set comes from `f.held` and nothing else.** Never `f.off`: the drain
 * puts pedal-decay fades in `off` alongside real key releases (server.py, the
 * decay_tick branch), so a pedalled passage read through `off` looks like you let go
 * of everything. `held` is the engine's own key-down set and is the only honest
 * answer. The 1 Hz heartbeat re-asserts it, which is what un-sticks a gate after a
 * dropped batch -- and in wait mode a stale held set is indistinguishable from a
 * crashed app, so that resync is not optional.
 *
 * **Waiting means waiting.** There is no timer that gives up on you.
 *
 * There used to be one: a gate blocked longer than eight seconds released itself, on
 * the theory that an unsatisfiable chord would otherwise freeze the clock forever.
 * That reasoning was wrong twice over. It is wrong about the failure -- the app is
 * fully responsive while it waits, the now-line brightens to say so, and Play, the
 * scrub and Close song are all right there -- and it is catastrophically wrong about
 * the normal case, because taking more than eight seconds to find a chord is not an
 * error, it is *the entire activity*. Learning a piece you cannot play means sitting
 * on one bar hunting for a note, and an app that walks off mid-hunt has stopped being
 * a practice tool and gone back to being a video.
 *
 * The two things it was actually insuring against are handled where they belong:
 *
 *   * **A note you physically cannot play.** A file can ask for pitches off the end of
 *     an 88-key board, and no amount of waiting produces one. Those are filtered out
 *     of a gate's requirements below, so they cannot stall it.
 *   * **A note-off that never arrived**, leaving a key stuck down. The 1 Hz heartbeat
 *     re-asserts the engine's own held set, which un-sticks it within a second -- so
 *     the timer was redundant for this one all along.
 *
 * Anything else, you skip on purpose.
 */

/* The 88 keys, as the fallback when nobody says otherwise. A file may name pitches
   outside the player's keyboard -- an orchestral reduction, a bass line written an
   octave low, junk from a bad export, or simply a piano piece opened by someone with
   61 keys -- and the roll cannot draw those because there is no column for them.
   Asking you to play a key that does not exist is the one gate that can never open,
   so it is never asked. */
const PLAYABLE_LO = 21;
const PLAYABLE_HI = 108;

/* How far the auto-fit will move a piece, in octaves each way. Beyond this you are no
   longer playing the piece in a different register, you are playing a different piece. */
const MAX_FIT = 3;

/**
 * The whole-piece octave shift that puts the most of it under your hands.
 *
 * Whole octaves only, and one shift for the WHOLE piece -- both of those are the point.
 * Moving individual notes into range would keep every pitch class and destroy the
 * music: a bass line that leaps up an octave mid-phrase is not the bass line. Moving
 * the whole piece keeps every interval, every shape and every leap exactly as written,
 * and only changes which register you play it in -- which is a thing pianists do to
 * each other's music all the time and think nothing of.
 *
 * Ties go to the smaller shift, and then to shifting DOWN: a piece that does not fit
 * a short keyboard is usually one with a bass line under it, and a short keyboard is
 * usually missing its bottom end rather than its top.
 */
function fitShift(notes, lo, hi) {
  /* Outliers are not a register problem. A file can name a pitch two octaves below the
     bottom A -- an orchestral reduction, junk from a bad export, one stray note in five
     hundred -- and transposing somebody's whole piece to rescue it would be the cure
     doing more damage than the disease. So a handful of strays is left where it is, and
     only a piece that genuinely sits outside your hands gets moved. */
  let out0 = 0;
  for (let i = 0; i < notes.length; i++) {
    const m = notes[i].midi;
    if (m < lo || m > hi) out0++;
  }
  if (out0 <= Math.max(2, notes.length * 0.02)) return 0;

  let best = 0;
  let bestIn = -1;
  for (let k = -MAX_FIT; k <= MAX_FIT; k++) {
    let inRange = 0;
    for (let i = 0; i < notes.length; i++) {
      const m = notes[i].midi + k * 12;
      if (m >= lo && m <= hi) inRange++;
    }
    if (inRange > bestIn || (inRange === bestIn && Math.abs(k) < Math.abs(best))) {
      best = k;
      bestIn = inRange;
    }
  }
  return best;
}

/* Two notes this close together are one chord, not two gates. Engraved MIDI puts a
   chord's notes on the same tick; a performance MIDI spreads them by a few ms, and
   0.03 quarters is about 15 ms at 120 bpm -- wide enough to catch the spread, narrow
   enough that a real grace note stays its own event. */
const GATE_EPS = 0.03;

/* The shortest section the loop will take seriously, in quarter notes.
   This is a guard rather than a taste: the wrap below subtracts the distance from A to
   B out of the frame's remaining music, so a section of length zero subtracts nothing,
   `left` never falls, and the while loop hangs the tab. roll.js's dt clamp does not
   save you -- it bounds the step, not the number of laps. A quarter note is shorter
   than any section worth grinding and far enough above zero to bound the laps. */
const MIN_LOOP_Q = 0.25;

const DEFAULT_BPM = 100;
const MIN_BPM = 20;
const MAX_BPM = 240;

/**
 * Build a play-along model from the backend's note timeline.
 *
 * `payload` is the body of GET /api/scores/{id}/notes -- the same call sheet.js
 * already makes. `notes` carry onset and duration in QUARTER NOTES, which is why
 * tempo is a separate knob here and not baked into the geometry.
 */
export function createGhost(payload, opts = {}) {
  // The keys the player actually has. Defaulted to the 88 so a caller that does not
  // care -- and every existing check -- behaves exactly as it always did.
  const lo = Number.isFinite(opts.low) ? opts.low | 0 : PLAYABLE_LO;
  const hi = Number.isFinite(opts.high) ? opts.high | 0 : PLAYABLE_HI;

  const notes = (payload.notes || [])
    .map((n) => ({
      onset: +n.onset || 0,
      duration: Math.max(0.01, +n.duration || 0),
      // `written` is what the file says and never changes; `midi` is where the piece
      // is currently sitting. Keeping both means the shift is always re-derivable
      // from the source rather than accumulated, so nudging it up and back down
      // cannot drift the piece a semitone at a time.
      written: n.midi | 0,
      midi: n.midi | 0,
      staff: n.staff === 2 ? 2 : 1,
    }))
    .sort((a, b) => a.onset - b.onset || a.midi - b.midi);

  const measures = (payload.measures || []).map((m) => ({
    number: m.number | 0,
    onset: +m.onset || 0,
    beats: Math.max(1, m.beats | 0) || 4,
    beat_type: Math.max(1, m.beat_type | 0) || 4,
  })).sort((a, b) => a.onset - b.onset);

  const total = notes.reduce((t, n) => Math.max(t, n.onset + n.duration), 0);
  const staves = [...new Set(notes.map((n) => n.staff))].sort();

  /* Move the piece to where it can be played, before anything is built on top of it.
     A transposition changes pitch and nothing else -- no onset moves -- so this is
     safe to do here and safe to redo later: the gates below group by onset, and
     onsets are exactly what a shift does not touch. */
  function applyShift(k) {
    const bounded = Math.max(-MAX_FIT, Math.min(MAX_FIT, k | 0));
    for (const n of notes) n.midi = n.written + bounded * 12;
    let out = 0;
    for (const n of notes) if (n.midi < lo || n.midi > hi) out++;
    return { shift: bounded, unreachable: out };
  }

  const fitted = applyShift(notes.length ? fitShift(notes, lo, hi) : 0);

  /* Gates: one per distinct onset, carrying every note struck there. Built over ALL
     notes regardless of hand, and filtered at match time -- so switching hands
     mid-piece does not rebuild anything and cannot desynchronise the index. */
  const gates = [];
  for (const n of notes) {
    const last = gates[gates.length - 1];
    if (last && n.onset - last.at <= GATE_EPS) last.notes.push(n);
    else gates.push({ at: n.onset, notes: [n] });
  }

  const onChange = typeof opts.onChange === 'function' ? opts.onChange : () => {};

  const model = {
    notes,
    measures,
    total,
    staves,
    title: opts.title || payload.title || 'Untitled',
    warnings: payload.warnings || [],

    /* Where the piece is sitting relative to how it was written, and how much of it
       still cannot be reached. Both are read by the control bar: a piece that has
       been moved has to SAY it has been moved, or the first thing you learn is the
       wrong register and the app never told you. */
    shift: fitted.shift,
    unreachable: fitted.unreachable,
    lo,
    hi,

    // --- read by roll.js every frame ---
    nowQ: 0,
    qps: DEFAULT_BPM / 60,
    hands: 'both',
    waiting: false,
    seq: 0,              // bumped on every seek, so the roll rebuilds its cursors

    /* The section, in quarter notes, and whether there is one. `looping` is the only
       field that means "there is a section": both ends can be stamped and still be too
       close together to be one. */
    loopA: 0,
    loopB: 0,
    looping: false,

    // --- read by the control bar ---
    bpm: DEFAULT_BPM,
    playing: false,
    wait: opts.wait !== false,
    finished: false,
  };

  let gi = 0;                       // index of the gate the clock is walking toward
  const held = new Set();           // keys physically down, from `f.held`

  /* Notes already cashed in by a gate that has passed. This is the re-strike rule,
     and it is a SET rather than a timestamp on purpose.

     The rule has to say "you struck this note for THIS gate" and reject "you are
     still leaning on it from the last one" -- otherwise C C C passes three gates on
     one press. The obvious implementation stamps each key-down with the clock and
     compares against when the gate was armed, which needs a tolerance for the fact
     that pressing a chord is not simultaneous, and then a second tolerance because
     playing slightly ahead of the beat is normal. Two magic numbers, and both of
     them wall-clock while the playhead runs on frame dt -- so the two disagree the
     moment a frame is late, and none of it can be checked without real time passing.

     Spending a note when its gate clears needs no clock at all. A key that goes up
     is un-spent, so releasing and striking again always works; a key held through
     two gates is spent for the second. Pressing EARLY still counts, because a note
     no gate has consumed yet is simply unspent -- which is the case the timestamp
     version got wrong. */
  const spent = new Set();

  /* Which notes of a gate this hand actually has to play. A gate the muted hand owns
     entirely comes back empty and is passed straight through, which is what makes
     wait mode and hands-separate compose without either knowing about the other. */
  function required(gate) {
    const out = new Set();
    for (const n of gate.notes) {
      // Off the end of YOUR keyboard: not yours to play, so not yours to be held up
      // by. After the fit above this is a handful of notes in a wide piece rather
      // than the whole bottom half of it, and the count is on screen either way.
      if (n.midi < lo || n.midi > hi) continue;
      if (model.hands === 'both'
        || (model.hands === 'R' ? n.staff === 1 : n.staff === 2)) out.add(n.midi);
    }
    return out;
  }

  function satisfied(gate) {
    const need = required(gate);
    if (!need.size) return true;               // this hand rests here
    for (const midi of need) {
      if (!held.has(midi)) return false;
      if (spent.has(midi)) return false;        // leaning on it, not playing it
    }
    return true;
  }

  /* Cash in the notes this gate asked for, so the next one has to be struck afresh. */
  function consume(gate) {
    for (const midi of required(gate)) spent.add(midi);
  }

  function arm(index) {
    gi = index;
  }

  /* Everything currently down is treated as already used up. Called wherever the
     playhead is placed by hand -- Play, seek, turning wait on -- so a chord you
     happen to be resting on does not clear the gate you just arrived at for free. */
  function spendHeld() {
    spent.clear();
    for (const midi of held) spent.add(midi);
  }

  /* The notes the player is being asked for right now, for lighting the keys. Empty
     unless the clock is actually being held. */
  function pending() {
    if (!model.waiting || gi >= gates.length) return [];
    return [...required(gates[gi])];
  }

  /* Where A and B actually land, and they land in OPPOSITE directions: A on the bar
     line at or before the playhead, B on the first one strictly after it. Stamping A a
     beat late gives a loop that lurches on every lap, and a section that begins on a
     bar line is also the only one the readout can name honestly.

     Both ends used to snap to the NEAREST line, and that is wrong in a way only the
     clock can see. You decide a phrase has ended just AFTER its bar line has gone past
     -- hearing the end is what tells you where it was -- and nearest then stamps B
     BEHIND the playhead. advance() refuses to wrap when the playhead is already past B
     on purpose (locating outside the section plays on, the way a DAW does), so the
     section was armed, banded and named while the clock ignored it, and whether the
     feature worked at all came down to about 20 ms of click timing. Nearest also
     collapsed A onto B whenever both were stamped inside one bar, which is exactly how
     you ask for "loop this bar" -- so that asked for nothing. Ceiling B cures both, and
     turns the readout's "bars 2-3" from approximately true into literally true.

     `total` is the ceiling's fallback and also its cap: it is max(onset + duration), so
     it can sit past the last bar line -- the end of the piece has to be reachable as B
     -- and equally a B past it is a section the playhead can never get to. */
  function barFloor(q) {
    if (!measures.length) return q;          // nothing to snap to
    // Before the first bar line there is no earlier line to name, so A lands on it.
    let best = measures[0].onset;
    for (const m of measures) {
      if (m.onset <= q && m.onset > best) best = m.onset;
    }
    return best;
  }

  function barCeil(q) {
    if (!measures.length) return q;
    let best = total;
    for (const m of measures) {
      if (m.onset > q && m.onset < best) best = m.onset;
    }
    return best;
  }

  /* Setting A past B is a mistake rather than an instruction to play backwards, so
     each end pushes the other out of its way -- the same rule backing.js uses for a
     video. It pushes to the far side of the bar you stamped in rather than onto the
     line you stamped, so the pair comes out one bar apart instead of collapsed: two
     presses inside one bar are an instruction, not something markLoop has to refuse.

     A only pushes B when a section already exists, and that guard is not cosmetic. B
     starts at 0, so an unguarded push fired on the FIRST press of Set A -- one press,
     and the piece was suddenly wrapping the bar you happened to be standing in. "Set
     the start" has to mean set the start; the section arms when you say where it ends. */
  function markLoop(which, q) {
    const lo = barFloor(q);
    const hi = barCeil(q);
    if (which === 'A') {
      model.loopA = lo;
      if (model.looping && model.loopB <= lo) model.loopB = hi;
    } else { model.loopB = hi; if (model.loopA >= hi) model.loopA = lo; }
    model.looping = model.loopB - model.loopA >= MIN_LOOP_Q;
    onChange();
    return model.looping;
  }

  function advance(dt) {
    if (!model.playing) {
      if (model.waiting) { model.waiting = false; onChange(); }
      return;
    }
    /* The frame's music time, spent down to zero rather than used once.

       The obvious shape -- move up to the gate, then `return` and let the rest land
       next frame -- silently throws that rest away, because the next frame computes a
       fresh step from a fresh dt and never sees it. Every gate crossing then costs up
       to one whole frame of music, so a piece set to 120 actually runs at about 114 on
       a 60 Hz screen and about 112 on a 30 Hz one: the same file plays at a different
       tempo on a different monitor, while the readout confidently prints 120. Draining
       `left` in a loop also lets several gates pass in one frame, which is what a muted
       hand's auto-passed gates need. */
    let left = dt * model.qps;

    while (left > 0) {
      const gate = model.wait && gi < gates.length ? gates[gi] : null;
      const room = gate ? Math.max(0, gate.at - model.nowQ) : Infinity;
      /* The section's end, and only while the playhead is still under it. Locating
         PAST B has to be allowed to stay past it, which is what a DAW does: outside
         the loop you play on, inside it you wrap. Without that guard a scrub click
         past B is silently undone on the very next frame. */
      const roomB = model.looping && model.nowQ < model.loopB
        ? model.loopB - model.nowQ
        : Infinity;

      /* Ties go to B. A gate written exactly on the section's end belongs to the next
         lap; wait for it here instead and the loop deadlocks with no way out but
         Clear. */
      if (roomB <= room && left >= roomB) {
        left -= roomB;
        /* seek() IS the wrap. It re-arms the gate index, bumps `seq` so the roll's
           forward-only cursors rebuild, and spends what is held -- which is what stops
           a section that begins and ends on the same pitch from having its first gate
           opened for free by the hand you finished the last lap with. */
        seek(model.loopA);
        continue;                     // and the rest of the step is played from A
      }

      if (gate && left >= room) {
        // Land exactly on the gate rather than overshooting it, or the first note of
        // every chord would be judged against a playhead already past it.
        if (model.nowQ < gate.at) model.nowQ = gate.at;
        left -= room;

        if (satisfied(gate)) {
          consume(gate);
          arm(gi + 1);
          continue;                     // the rest of the step carries on past it
        }
        // Frozen, and it stays frozen. The remainder of the step is NOT spent -- the
        // one place dropping it is correct, because the clock has genuinely stopped.
        // Nothing here counts how long you have been here. Take all night.
        if (!model.waiting) { model.waiting = true; onChange(); }
        return;
      }

      if (model.waiting) { model.waiting = false; onChange(); }
      model.nowQ += left;
      left = 0;
      // Free-running past gates keeps the index honest, so turning wait mode on
      // mid-piece stops at the next chord rather than rewinding to an old one.
      while (gi < gates.length && gates[gi].at <= model.nowQ) gi++;
    }

    if (model.nowQ >= total) {
      model.nowQ = total;
      model.playing = false;
      model.finished = true;
      onChange();
    }
  }

  /* Put the playhead somewhere and rebuild everything that depended on where it was.
     Seeking is the one operation that can move time backwards, so it is the only one
     that bumps `seq`. */
  function seek(q) {
    model.nowQ = Math.max(0, Math.min(total, +q || 0));
    model.finished = false;
    model.waiting = false;
    model.seq++;
    let i = 0;
    while (i < gates.length && gates[i].at < model.nowQ) i++;
    arm(i);
    spendHeld();
    onChange();
  }

  return Object.assign(model, {
    advance,
    seek,
    pending,

    /* Move past the chord you are sitting on, deliberately. This is what replaced the
       eight-second timer: the same escape, but you decide when, so it can never fire
       in the middle of you working something out. */
    skip() {
      if (gi >= gates.length) return false;
      consume(gates[gi]);            // treat it as played, so held keys do not re-open it
      arm(gi + 1);
      model.waiting = false;
      // The playhead is NOT jumped to the next chord. Releasing the gate is enough:
      // time resumes from here and flows through whatever rest comes next at the
      // tempo you set, so a skipped bar still sounds like a bar. Jumping would edit
      // the piece rather than let you past one moment of it.
      onChange();
      return true;
    },

    /* The section, stamped from wherever the playhead is. Stamping B is enough to
       start it wrapping; there is no arm button, because setting an end you then have
       to switch on is a step that exists only to be forgotten. */
    setLoopA() { return markLoop('A', model.nowQ); },
    setLoopB() { return markLoop('B', model.nowQ); },
    clearLoop() {
      model.loopA = 0;
      model.loopB = 0;
      model.looping = false;
      onChange();
    },

    play() {
      if (model.finished) seek(0);
      model.playing = true;
      spendHeld();
      onChange();
    },
    pause() { model.playing = false; model.waiting = false; onChange(); },
    toggle() { if (model.playing) model.pause(); else model.play(); },

    setTempo(bpm) {
      model.bpm = Math.max(MIN_BPM, Math.min(MAX_BPM, Math.round(+bpm) || DEFAULT_BPM));
      model.qps = model.bpm / 60;
      onChange();
      return model.bpm;
    },

    setHands(which) {
      model.hands = which === 'R' || which === 'L' ? which : 'both';
      // A gate the new hand does not own must not stay latched as "waiting".
      /* And the notes that hand is resting on must not cash in a gate for free.
         consume() only spends the notes the CURRENT hand was asked for, so a key the
         muted hand has been leaning on is held but unspent -- switching to that hand
         would open the very next gate with nothing struck. */
      spendHeld();
      onChange();
      return model.hands;
    },

    setWait(on) {
      model.wait = !!on;
      if (!model.wait) model.waiting = false;
      else spendHeld();          // arriving at a gate mid-hold must not be free
      onChange();
      return model.wait;
    },

    /* Move the whole piece by octaves, by hand. The auto-fit is a guess -- it counts
       notes, and the most-notes register is not always the one you want to play in --
       so it has to be overridable, and the override is the same one-shift-for-the-
       whole-piece transposition rather than a second mechanism.

       Nothing structural is rebuilt: a shift moves pitches and no onsets, so the gates
       stay exactly as grouped. What does have to be reset is which notes have been
       spent, because the keys the current gate is asking for have just changed
       underneath the player's hands. */
    setShift(k) {
      const before = model.shift;
      const res = applyShift(k);
      model.shift = res.shift;
      model.unreachable = res.unreachable;
      if (res.shift !== before) {
        spendHeld();
        model.waiting = false;
        model.seq++;             // the roll's cursors are indexed by pitch column
      }
      onChange();
      return model.shift;
    },

    /* Live key state. `held` only -- see the header.
       A key coming UP is what un-spends it, which is what makes striking the same
       note twice in a row work and holding it through two gates not. */
    frame(f) {
      if (!f) return;

      /* A fresh strike un-spends the note, and this line is not redundant with the
         release below.
         The drain batches a whole 1/60 s window into one frame and sends the held set
         as it stands AFTERWARDS (server.py's drain_loop). Lift a key and hit it again
         inside that window -- which is exactly what a fast repeated note is -- and
         `held` comes out byte-identical, so the release is invisible and the note
         stays spent. The gate then refuses a note you demonstrably played. `on` is the
         one field that means "struck" and cannot mean anything else; `off` is the
         field that must never be trusted, because pedal-decay fades ride in it. */
      if (f.on) for (const ev of f.on) spent.delete(ev[0]);

      if (!f.held) return;
      const down = f.held;
      for (let i = 0; i < down.length; i++) held.add(down[i]);
      if (held.size !== down.length) {
        const set = new Set(down);
        for (const midi of [...held]) {
          if (!set.has(midi)) { held.delete(midi); spent.delete(midi); }
        }
      }
    },

    /* The 1 Hz heartbeat carries the engine's own held set. This is the line that
       un-sticks a gate the frame path got wrong, and it is why a missed note-off
       costs a second rather than the session. */
    resync(list) {
      this.frame({ held: Array.isArray(list) ? list : [] });
    },

    /* Which bar a position is in, 1-based, for the readout. Defaults to the playhead;
       the argument is what lets the same readout name A and B. */
    bar(q = model.nowQ) {
      if (!measures.length) return 0;
      let lo = 0;
      let hi = measures.length - 1;
      while (lo < hi) {
        const mid = (lo + hi + 1) >> 1;
        if (measures[mid].onset <= q) lo = mid; else hi = mid - 1;
      }
      return measures[lo].number;
    },
    bars() { return measures.length; },
  });
}
