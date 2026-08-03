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
 * **Every gate can be escaped.** A ten-note gate, a doubled track or a note-off that
 * never arrived would otherwise freeze the clock forever with no way out but the
 * mouse. A gate blocked longer than ESCAPE_S releases itself. It is a code constant
 * and not a setting, because the number only exists to stop a hang.
 */

/* Two notes this close together are one chord, not two gates. Engraved MIDI puts a
   chord's notes on the same tick; a performance MIDI spreads them by a few ms, and
   0.03 quarters is about 15 ms at 120 bpm -- wide enough to catch the spread, narrow
   enough that a real grace note stays its own event. */
const GATE_EPS = 0.03;

/* The hang escape. Long enough that it never fires while someone is hunting for a
   note, short enough that a stuck note-off does not end the session. */
const ESCAPE_S = 8;

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
  const notes = (payload.notes || [])
    .map((n) => ({
      onset: +n.onset || 0,
      duration: Math.max(0.01, +n.duration || 0),
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

    // --- read by roll.js every frame ---
    nowQ: 0,
    qps: DEFAULT_BPM / 60,
    hands: 'both',
    waiting: false,
    seq: 0,              // bumped on every seek, so the roll rebuilds its cursors

    // --- read by the control bar ---
    bpm: DEFAULT_BPM,
    playing: false,
    wait: opts.wait !== false,
    finished: false,
  };

  let gi = 0;                       // index of the gate the clock is walking toward
  let blocked = 0;                  // seconds this gate has held the clock
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
    blocked = 0;
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
        blocked += dt;
        if (blocked >= ESCAPE_S) {
          // Something is wrong -- an unplayable gate, a dropped note-off, a cable
          // pulled. Move on rather than look hung.
          arm(gi + 1);
          if (model.waiting) { model.waiting = false; onChange(); }
          continue;
        }
        // Genuinely frozen, so the remainder of the step is NOT spent. That is the one
        // place dropping it is correct: the clock has stopped.
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

    play() {
      if (model.finished) seek(0);
      model.playing = true;
      blocked = 0;
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
      blocked = 0;
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
      blocked = 0;
      onChange();
      return model.wait;
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

    /* Which bar the playhead is in, 1-based, for the readout. */
    bar() {
      if (!measures.length) return 0;
      let lo = 0;
      let hi = measures.length - 1;
      while (lo < hi) {
        const mid = (lo + hi + 1) >> 1;
        if (measures[mid].onset <= model.nowQ) lo = mid; else hi = mid - 1;
      }
      return measures[lo].number;
    },
    bars() { return measures.length; },
  });
}
