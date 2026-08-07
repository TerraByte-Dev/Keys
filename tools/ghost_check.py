"""Ghost mode's rules, checked without a canvas and without a piano.

    .venv\\Scripts\\python.exe tools\\ghost_check.py

Ghost mode splits deliberately: `frontend/ghost.js` is the clock and the rules,
`frontend/roll.js` is the pixels. Everything on this side of that line is arithmetic
over a note list, so it can be driven from Node with no DOM, no audio device and no
MIDI cable -- which is the whole reason the split is there.

Node runs the modules directly. There is no bundler in this project and there is not
going to be one, so the test imports exactly the files the browser imports.

What this actually protects, in rough order of how badly it would hurt:

* **The frozen-gap property.** Ghost mode's one original claim is that the vertical
  distance between your bar and its target IS your timing error, and that it does not
  drift as the pair travels down the screen. That is only true while both are
  projected by the same rule from the same line. It is checked here over many frames
  rather than asserted once, because "drifts slowly" is exactly the failure a single
  assertion misses.
* **The re-strike rule.** Without it, three of the same note in a row all pass on one
  press and wait mode silently stops teaching anything.
* **The escape.** A gate that can never be satisfied must not be able to hang the
  clock forever, because it is indistinguishable from a crash.
* **Reading `held` and not `off`.** The drain puts pedal-decay fades in `off`, so a
  pedalled passage read through it looks like you let go of everything.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"

# The check runs in Node so it can import the real modules. Written as one script
# rather than a file per case: the whole point is that these rules are cheap.
DRIVER = r"""
import { createGhost } from './ghost.js';
import { noteSpan } from './roll.js';

const out = [];
const ok = (label, passed, detail = '') => out.push({ label, passed: !!passed, detail });
const near = (a, b, eps = 1e-6) => Math.abs(a - b) <= eps;

/* A note list shaped exactly like GET /api/scores/{id}/notes returns. */
const piece = (notes, measures) => ({
  notes: notes.map(([onset, duration, midi, staff]) => ({ onset, duration, midi, staff })),
  measures: measures || [{ number: 1, onset: 0, beats: 4, beat_type: 4 }],
});

/* -- 1. the falling projection ------------------------------------------- */
{
  const SPEED = 140, HIT = 56, QPS = 2;      // 120 bpm
  // A note due at quarter 4 is above the line before it is due, on it when it is,
  // and below it after. Anything else and the roll is not showing you the beat.
  const before = noteSpan(4, 1, 3, QPS, SPEED, HIT);
  const on = noteSpan(4, 1, 4, QPS, SPEED, HIT);
  const after = noteSpan(4, 1, 5, QPS, SPEED, HIT);
  ok('a note above the line before it is due', before.bottom < HIT);
  ok('its onset edge crosses the line exactly when due', near(on.bottom, HIT));
  ok('and it is past the line afterwards', after.bottom > HIT);
  ok('length is duration x rate, not a fixed size',
     near(on.bottom - on.top, (1 / QPS) * SPEED),
     `${(on.bottom - on.top).toFixed(2)}px for one quarter at ${SPEED}px/s`);

  // Roll speed changes the pixels and NOT the music: the same note is still due at
  // the same quarter, it just has further to fall.
  const slow = noteSpan(4, 1, 4, QPS, 60, HIT);
  ok('roll speed does not move when a note is due', near(slow.bottom, HIT));
}

/* -- 2. the frozen gap: the one claim ghost mode makes ------------------- */
{
  const SPEED = 140, HIT = 56, QPS = 2;
  const LATE_S = 0.12;                       // played 120 ms behind
  // Your played bar is born at the now-line when you press and falls at the same
  // rate, so it is the same projection with the onset moved by however late you were.
  let drift = 0;
  let firstGap = null;
  for (let f = 0; f < 240; f++) {            // four seconds of frames
    const nowQ = 4 + f * (1 / 60) * QPS;
    const target = noteSpan(4, 1, nowQ, QPS, SPEED, HIT);
    const played = noteSpan(4 + LATE_S * QPS, 1, nowQ, QPS, SPEED, HIT);
    const gap = played.bottom - target.bottom;
    if (firstGap === null) firstGap = gap;
    drift = Math.max(drift, Math.abs(gap - firstGap));
  }
  ok('a late note sits ABOVE its target, not below', firstGap < 0);
  ok('the gap equals the timing error x the scroll rate',
     near(Math.abs(firstGap), LATE_S * SPEED, 1e-6),
     `${Math.abs(firstGap).toFixed(2)}px for ${LATE_S * 1000}ms at ${SPEED}px/s`);
  ok('and it does not drift over four seconds of travel', near(drift, 0, 1e-9),
     `max drift ${drift.toExponential(2)}px`);

  /* On time is flush -- and this has to compare the two things that are ACTUALLY
     drawn, which are computed differently. The ghost is projected closed-form from
     nowQ; your own bar is integrated frame by frame in roll.js (`b.head += speed*dt`,
     drawn at `hitY + b.head`). Comparing noteSpan against itself would assert only
     that it is deterministic and would pass for a function returning a constant. */
  let head = 0;                                  // the played bar, integrated
  let worst = 0;
  for (let f = 1; f <= 180; f++) {
    const dt = 1 / 60;
    head += SPEED * dt;                          // exactly what roll.js's tick does
    const playedBottom = HIT + head;
    const nowQ = 4 + f * dt * QPS;               // pressed at the note's onset
    const ghostBottom = noteSpan(4, 1, nowQ, QPS, SPEED, HIT).bottom;
    worst = Math.max(worst, Math.abs(playedBottom - ghostBottom));
  }
  ok('an on-time bar tracks its ghost edge-for-edge as both fall', near(worst, 0, 1e-9),
     `worst divergence ${worst.toExponential(2)}px over three seconds`);
}

/* -- 3. gates ------------------------------------------------------------ */
{
  // Same onset is a chord. 0.02 apart is a chord someone actually played. 0.5 apart
  // is two events.
  const g = createGhost(piece([
    [0, 1, 60, 1], [0, 1, 64, 1], [0.02, 1, 67, 1],
    [1, 1, 72, 1],
  ]));
  // Gates are internal, so they are probed through behaviour: with wait on and
  // nothing held, the clock must stop at 0 and go no further.
  g.play();
  for (let f = 0; f < 60; f++) g.advance(1 / 60);
  ok('wait mode freezes at the first gate', near(g.nowQ, 0), `nowQ=${g.nowQ}`);
  ok('and says so, so it does not look hung', g.waiting === true);

  // Two of the three notes is not the chord.
  g.frame({ held: [60, 64] });
  for (let f = 0; f < 12; f++) g.advance(1 / 60);
  ok('a partial chord does not open the gate', near(g.nowQ, 0));

  // The third note collapses into the same gate despite the 0.02 offset.
  g.frame({ held: [60, 64, 67] });
  for (let f = 0; f < 12; f++) g.advance(1 / 60);
  ok('notes within the chord window are ONE gate', g.nowQ > 0, `nowQ=${g.nowQ.toFixed(3)}`);
}

/* -- 4. the re-strike rule ----------------------------------------------- */
{
  const g = createGhost(piece([[0, 0.5, 60, 1], [1, 0.5, 60, 1], [2, 0.5, 60, 1]]));
  g.setTempo(240);                            // 4 quarters per second, so this is quick
  g.play();
  g.frame({ held: [60] });
  for (let f = 0; f < 60; f++) g.advance(1 / 60);
  // One press must not satisfy three separate gates. It should clear the first and
  // then stop, still holding. This is the case a wall-clock re-strike rule cannot
  // check, because the whole loop above runs in well under any real tolerance.
  ok('one press does not walk three gates', g.nowQ < 2,
     `nowQ=${g.nowQ.toFixed(3)} -- should be stuck at the second C`);
  ok('and it is waiting there rather than running on', g.waiting === true);

  // Letting go and striking again is what moves it.
  g.frame({ held: [] });
  g.frame({ held: [60] });
  for (let f = 0; f < 30; f++) g.advance(1 / 60);
  ok('re-striking the same note opens the next gate', g.nowQ > 1);

  // Pressing a note before its gate is due is normal playing, not an error: a note
  // no gate has consumed is unspent whenever it went down.
  const early = createGhost(piece([[0, 0.5, 60, 1], [4, 0.5, 67, 1]]));
  early.play();
  early.frame({ held: [60, 67] });            // both struck at once, G is four beats early
  for (let f = 0; f < 60 * 4; f++) early.advance(1 / 60);
  ok('a note struck early still counts when its gate arrives', early.nowQ >= 4,
     `nowQ=${early.nowQ.toFixed(3)}`);

  // And leaning on a chord when you press Play is not playing it.
  const lean = createGhost(piece([[0, 1, 60, 1], [1, 1, 62, 1]]));
  lean.frame({ held: [60] });
  lean.play();
  for (let f = 0; f < 30; f++) lean.advance(1 / 60);
  ok('a chord already held when you press Play is not free', near(lean.nowQ, 0),
     `nowQ=${lean.nowQ.toFixed(3)}`);
}

/* -- 5. waiting means waiting -------------------------------------------- */
/* There used to be an eight-second escape here, and it was a bug wearing a safety
   jacket: hunting for a chord you cannot play yet takes longer than that, so it fired
   during the exact activity the mode exists for and walked off mid-hunt. Nothing
   auto-advances now. These assertions are the old ones inverted on purpose. */
{
  const g = createGhost(piece([[0, 1, 60, 1], [1, 1, 62, 1]]));
  g.play();
  for (let f = 0; f < 60 * 10; f++) g.advance(1 / 60);     // ten seconds, nothing played
  ok('ten seconds of hunting does not move the roll', near(g.nowQ, 0),
     `nowQ=${g.nowQ.toFixed(3)}`);
  for (let f = 0; f < 60 * 120; f++) g.advance(1 / 60);    // two more minutes
  ok('and neither does two minutes', near(g.nowQ, 0), `nowQ=${g.nowQ.toFixed(3)}`);
  ok('it is still saying it is waiting, not pretending to play', g.waiting === true);

  // Playing the note is the only thing that moves it -- still true after the long wait.
  g.frame({ held: [60] });
  for (let f = 0; f < 30; f++) g.advance(1 / 60);
  ok('and it picks up the moment you find the note', g.nowQ > 0,
     `nowQ=${g.nowQ.toFixed(3)}`);
}

/* -- 5b. the two things the timer was actually insuring against ----------- */
{
  // A pitch off the end of an 88-key board can never be played, so it must never be
  // asked for. Bottom A is 21; this file wants a C two octaves below it.
  const g = createGhost(piece([[0, 1, 12, 2], [1, 1, 60, 1]]));
  g.setTempo(120);
  g.play();
  for (let f = 0; f < 60; f++) g.advance(1 / 60);
  ok('a note off the end of the keyboard cannot stall a gate', g.nowQ >= 1,
     `nowQ=${g.nowQ.toFixed(3)}`);
  // ...and the playable note in the same piece still gates normally.
  for (let f = 0; f < 60; f++) g.advance(1 / 60);
  ok('while the playable note still does', near(g.nowQ, 1), `nowQ=${g.nowQ.toFixed(3)}`);

  // A gate of ONLY unplayable notes passes straight through rather than deadlocking.
  const all = createGhost(piece([[0, 1, 5, 1], [0, 1, 120, 1], [2, 1, 60, 1]]));
  all.setTempo(120);
  all.play();
  for (let f = 0; f < 120; f++) all.advance(1 / 60);
  ok('a gate with nothing playable in it does not deadlock', all.nowQ >= 2,
     `nowQ=${all.nowQ.toFixed(3)}`);
}

/* -- 5c. skipping, which is the deliberate escape ------------------------ */
{
  const g = createGhost(piece([[0, 1, 60, 1], [1, 1, 62, 1], [2, 1, 64, 1]]));
  g.play();
  for (let f = 0; f < 60; f++) g.advance(1 / 60);
  ok('stuck at the first chord', near(g.nowQ, 0));
  ok('skip reports that it did something', g.skip() === true);
  for (let f = 0; f < 120; f++) g.advance(1 / 60);
  ok('skipping walks on to the next chord and stops THERE', near(g.nowQ, 1),
     `nowQ=${g.nowQ.toFixed(3)} -- one skip, not a free run to the end`);

  // Skipping releases the gate; it does NOT jump the playhead. Time flows on from
  // where it stopped, so the rest between two chords is still played rather than cut.
  const t = createGhost(piece([[0, 1, 60, 1], [4, 1, 62, 1]]));
  t.setTempo(120);
  t.play();
  for (let f = 0; f < 30; f++) t.advance(1 / 60);
  t.skip();
  for (let f = 0; f < 30; f++) t.advance(1 / 60);      // half a second = 1 quarter
  ok('skipping resumes the clock rather than jumping the playhead',
     t.nowQ > 0.5 && t.nowQ < 3, `nowQ=${t.nowQ.toFixed(3)} -- should be walking to bar 2`);

  // A key you were holding while you gave up must not then open the gate you skipped
  // TO -- otherwise Skip silently plays the next note for you.
  const h = createGhost(piece([[0, 1, 60, 1], [1, 1, 60, 1], [2, 1, 60, 1]]));
  h.setTempo(120);
  h.play();
  h.frame({ held: [60] });          // one press: clears gate 0, then blocks on gate 1
  for (let f = 0; f < 90; f++) h.advance(1 / 60);
  ok('...and it is blocked on the second C, still holding it', near(h.nowQ, 1),
     `nowQ=${h.nowQ.toFixed(3)}`);
  h.skip();
  for (let f = 0; f < 180; f++) h.advance(1 / 60);
  ok('skipping does not spend the held key on the NEXT gate too', near(h.nowQ, 2),
     `nowQ=${h.nowQ.toFixed(3)} -- should stop at the third C, not run to the end`);

  // Skipping off the end is harmless.
  const e = createGhost(piece([[0, 1, 60, 1]]));
  e.play();
  e.skip();
  ok('skipping the last chord is harmless', e.skip() === false);
}

/* -- 6. hands separate --------------------------------------------------- */
{
  // A left-hand-only gate must not stop the clock while the right hand is drilling.
  const g = createGhost(piece([[0, 1, 48, 2], [1, 1, 60, 1]]));
  g.setHands('R');
  g.play();
  for (let f = 0; f < 40; f++) g.advance(1 / 60);
  ok('a gate the muted hand owns passes straight through', g.nowQ > 0,
     `nowQ=${g.nowQ.toFixed(3)}`);
  // ...and the right hand's gate still stops it.
  for (let f = 0; f < 200; f++) g.advance(1 / 60);
  ok('the practising hand still gates the clock', near(g.nowQ, 1),
     `nowQ=${g.nowQ.toFixed(3)}`);
  ok('the muted hand is still in the note list to read', g.notes.length === 2);
}

/* -- 6b. switching hands mid-piece --------------------------------------- */
/* Resting your left hand on a key while drilling the right is not playing it, and
   must not still be true after you switch to the left. A gate only ever spends the
   notes the CURRENT hand was asked for, so anything the muted hand is leaning on sits
   in `held` unspent -- and without re-spending on the switch it opens the very next
   gate for free. */
{
  const g = createGhost(piece([
    [0, 0.5, 48, 2], [1, 0.5, 48, 2], [2, 0.5, 48, 2],       // left hand: C3 throughout
    [0, 0.5, 60, 1], [1, 0.5, 62, 1], [2, 0.5, 64, 1],       // right hand: the tune
  ]));
  g.setTempo(120);
  g.setHands('R');
  g.play();
  g.frame({ held: [48, 60] });          // playing the right hand, left hand resting on C3
  for (let f = 0; f < 60; f++) g.advance(1 / 60);
  ok('a hand you are resting does not gate the one you are drilling', near(g.nowQ, 1),
     `nowQ=${g.nowQ.toFixed(3)}`);

  const before = g.nowQ;
  g.setHands('L');                      // now drill the left hand instead
  for (let f = 0; f < 40; f++) g.advance(1 / 60);
  ok('switching hands does not cash in the key you were resting on',
     near(g.nowQ, before), `nowQ=${g.nowQ.toFixed(3)}, was ${before.toFixed(3)}`);
}

/* -- 7. seek, and the cursor reset it implies ---------------------------- */
{
  const g = createGhost(piece([[0, 1, 60, 1], [1, 1, 62, 1], [2, 1, 64, 1]]));
  const seq0 = g.seq;
  g.seek(2);
  ok('a seek bumps seq so the roll rebuilds its cursors', g.seq > seq0);
  ok('and lands where it was asked to', near(g.nowQ, 2));
  g.play();
  for (let f = 0; f < 30; f++) g.advance(1 / 60);
  ok('it waits at the gate it landed on, not an earlier one', near(g.nowQ, 2));
  g.frame({ held: [64] });
  for (let f = 0; f < 30; f++) g.advance(1 / 60);
  ok('which is the right gate for that position', g.nowQ > 2);
  g.seek(999);
  ok('seeking past the end clamps to it', near(g.nowQ, g.total));
  g.seek(-5);
  ok('and before the start clamps to zero', near(g.nowQ, 0));
}

/* -- 8. held comes from `held`, never from `off` ------------------------- */
{
  const g = createGhost(piece([[0, 1, 60, 1]]));
  g.play();
  /* ORDER IS THE TEST. The key goes down FIRST, and only then does a note-off arrive
     carrying no `held` -- which is exactly what a pedal-decay fade looks like on the
     wire. If frame() ever honoured `off`, the key would leave the held set here and
     the gate would block, so this fails under that regression. Sending the `off`
     first and the `held` second (the obvious order) asserts nothing at all: the
     second call re-establishes the key whatever the first did. */
  g.frame({ held: [60] });
  g.frame({ off: [60] });
  for (let f = 0; f < 30; f++) g.advance(1 / 60);
  ok('a note-off with no `held` is ignored -- pedal decay is not a key release',
     g.nowQ > 0, `nowQ=${g.nowQ.toFixed(3)}`);

  // ...and a lift-and-restrike inside ONE drain window, where `held` comes out
  // byte-identical and only `on` reveals that anything happened.
  const rs = createGhost(piece([[0, 0.5, 60, 1], [0.5, 0.5, 60, 1]]));
  rs.setTempo(240);
  rs.play();
  rs.frame({ held: [60] });
  for (let f = 0; f < 20; f++) rs.advance(1 / 60);
  const afterFirst = rs.nowQ;
  rs.frame({ held: [60], off: [60], on: [[60, 90]] });    // one batched window
  for (let f = 0; f < 40; f++) rs.advance(1 / 60);
  ok('a re-strike batched into one frame still counts', rs.nowQ > afterFirst + 0.4,
     `nowQ=${rs.nowQ.toFixed(3)} from ${afterFirst.toFixed(3)}`);

  // The 1 Hz heartbeat is the un-stick: it re-asserts the engine's own set.
  const h = createGhost(piece([[0, 1, 60, 1], [1, 1, 62, 1]]));
  h.play();
  h.resync([60]);
  for (let f = 0; f < 30; f++) h.advance(1 / 60);
  ok('the heartbeat can satisfy a gate on its own', h.nowQ > 0);
}

/* -- 9. tempo is a knob, and it is not the roll speed -------------------- */
{
  // Long enough that a second of playing does not simply hit the end of the piece.
  const g = createGhost(piece([[0, 1, 60, 1], [8, 1, 62, 1]]));
  g.setWait(false);
  g.setTempo(120);
  g.play();
  for (let f = 0; f < 60; f++) g.advance(1 / 60);
  const atFull = g.nowQ;
  ok('one second at 120bpm is two quarter notes', near(atFull, 2, 0.05),
     `nowQ=${atFull.toFixed(3)}`);

  g.seek(0);
  g.setTempo(60);
  g.play();
  for (let f = 0; f < 60; f++) g.advance(1 / 60);
  ok('halving the tempo halves the music, not the picture', near(g.nowQ, 1, 0.05),
     `nowQ=${g.nowQ.toFixed(3)}`);
  ok('tempo is clamped to something playable',
     g.setTempo(9000) === 240 && g.setTempo(1) === 20);
}

/* -- 9b. the tempo holds in WAIT MODE, at any frame rate -------------------- */
/* The mode that ships on by default, timed -- which the checks above deliberately do
   NOT do, because they all turn wait off first. That gap let a real bug ship: landing
   on a gate and returning threw away the rest of the frame's music, so every gate
   crossing cost up to one frame and a piece set to 120 ran at about 114 on a 60 Hz
   screen and 112 on a 30 Hz one. The same file, a different tempo, on a different
   monitor. Sweeping the frame rate is the point: a bug that quantises to the frame is
   invisible if you only ever test at 60. */
{
  const notes = [];
  for (let i = 0; i < 128; i++) notes.push([i * 0.5, 0.5, 48 + (i % 12), 2]);
  for (const fps of [30, 60, 100, 144]) {
    const g = createGhost(piece(notes));
    g.setTempo(120);
    g.setHands('R');          // every gate belongs to the muted hand, so all auto-pass
    g.play();
    let frames = 0;
    while (!g.finished && frames < 200000) { g.advance(1 / fps); frames++; }
    const seconds = frames / fps;
    const effective = (g.total / seconds) * 60;      // quarters per minute actually run
    ok(`wait mode holds 120bpm at ${fps}fps`, near(effective, 120, 0.6),
       `ran at ${effective.toFixed(2)} bpm`);
  }
  // ...and a gate that genuinely blocks must still stop the clock dead, so the fix
  // above cannot have been "just never freeze".
  const held = createGhost(piece([[0, 1, 60, 1], [1, 1, 62, 1]]));
  held.setTempo(120);
  held.play();
  for (let f = 0; f < 300; f++) held.advance(1 / 60);
  ok('and a blocked gate still stops it dead', near(held.nowQ, 0), `nowQ=${held.nowQ}`);
}

/* -- 10. wait mode off is a plain player --------------------------------- */
{
  const g = createGhost(piece([[0, 1, 60, 1], [1, 1, 62, 1]]), { wait: false });
  g.play();
  for (let f = 0; f < 120; f++) g.advance(1 / 60);
  ok('with wait off the piece runs without you', g.nowQ > 1, `nowQ=${g.nowQ.toFixed(3)}`);
  ok('and never claims to be waiting', g.waiting === false);

  // It stops at the end rather than running off into silence.
  for (let f = 0; f < 600; f++) g.advance(1 / 60);
  ok('it stops at the last note', near(g.nowQ, g.total) && g.playing === false);
  ok('and reports that it finished', g.finished === true);
}

/* -- 11. the bar readout ------------------------------------------------- */
{
  const g = createGhost(piece(
    [[0, 1, 60, 1], [4, 1, 62, 1], [8, 1, 64, 1]],
    [{ number: 1, onset: 0, beats: 4, beat_type: 4 },
     { number: 2, onset: 4, beats: 4, beat_type: 4 },
     { number: 3, onset: 8, beats: 4, beat_type: 4 }],
  ));
  g.seek(0);
  ok('bar 1 at the top', g.bar() === 1);
  g.seek(5);
  ok('bar 2 in the middle of bar 2', g.bar() === 2, `got ${g.bar()}`);
  g.seek(8);
  ok('bar 3 exactly on its downbeat', g.bar() === 3, `got ${g.bar()}`);
  ok('and it knows how many there are', g.bars() === 3);
}

/* -- 12. degenerate input ------------------------------------------------ */
{
  const empty = createGhost({ notes: [], measures: [] });
  empty.play();
  empty.advance(1 / 60);
  ok('an empty piece does not throw', true);
  ok('and reports no bars rather than guessing', empty.bar() === 0);

  // Unsorted input, which the endpoint does not produce but a hand-built payload can.
  const messy = createGhost(piece([[2, 1, 64, 1], [0, 1, 60, 1], [1, 1, 62, 1]]));
  ok('notes come out in onset order whatever went in',
     messy.notes.every((n, i, a) => i === 0 || a[i - 1].onset <= n.onset));
  // A zero-length note would be an invisible target.
  const zero = createGhost(piece([[0, 0, 60, 1]]));
  ok('a zero-length note is given a floor', zero.notes[0].duration > 0);
}

/* -- 13. the A/B section ------------------------------------------------- */
/* The wrap lives in advance(), not in roll.js's frame loop, and this block is the
   whole payoff of that: a loop implemented in the drawing code could not be checked
   without a canvas, and the "move to the boundary and return" shape it would invite
   is the same bug section 9b exists to catch. */
{
  // Four bars of 4/4, one note on each downbeat, and a fifth bar that only the piece's
  // total reaches -- so `total` is 20 and the last bar line is at 16.
  const bars4 = [1, 2, 3, 4, 5].map((n) => (
    { number: n, onset: (n - 1) * 4, beats: 4, beat_type: 4 }));
  const song = () => createGhost(piece(
    [[0, 1, 60, 1], [4, 1, 62, 1], [8, 1, 64, 1], [12, 1, 65, 1], [16, 4, 67, 1]], bars4));

  const g = song();
  g.setWait(false);
  // Both stamps sit where the NEAREST bar line is bar 3's, so a nearest-snap build puts
  // A and B on the same line and calls the section empty.
  g.seek(6.5);                      // most of the way through bar 2
  g.setLoopA();
  ok('A floors to the top of the bar the playhead is in', near(g.loopA, 4), `A=${g.loopA}`);
  g.seek(9.5);                      // and just into bar 3
  g.setLoopB();
  ok('B ceils to the end of the bar the playhead is in', near(g.loopB, 12), `B=${g.loopB}`);
  ok('and a section of two bar lines is a section', g.looping === true);
  ok('bar() names a position other than the playhead, which is what the readout needs',
     g.bar(g.loopA) === 2 && g.bar() === 3, `bar(A)=${g.bar(g.loopA)}, bar()=${g.bar()}`);

  // B must be able to reach the end of the piece. `total` is 20 and the last bar line
  // is at 16, so snapping to bar onsets alone would leave the final bar unloopable.
  const e = song();
  e.seek(e.total);
  e.setLoopB();
  ok('B can reach the end of the piece, not just the last bar line',
     near(e.loopB, e.total), `B=${e.loopB}, total=${e.total}`);
}

/* -- 13b. the wrap carries the remainder --------------------------------- */
{
  const bars4 = [1, 2, 3].map((n) => ({ number: n, onset: (n - 1) * 4, beats: 4, beat_type: 4 }));
  const g = createGhost(piece([[0, 1, 60, 1], [4, 1, 62, 1], [8, 1, 64, 1]], bars4));
  g.setWait(false);
  g.setTempo(120);                  // 2 quarters a second
  g.seek(0); g.setLoopA();
  g.seek(3); g.setLoopB();          // in bar 1, so B ceils to the bar line at 4
  g.seek(3.9);
  g.play();
  const seq0 = g.seq;
  g.advance(0.15);                  // 0.3 quarters: 0.1 to B, 0.2 more from A
  ok('the clock wraps B back to A', g.seq > seq0 && g.nowQ < 4, `nowQ=${g.nowQ.toFixed(4)}`);
  ok('and the rest of the step is carried across the wrap, not thrown away',
     near(g.nowQ, 0.2, 1e-9), `nowQ=${g.nowQ.toFixed(6)} -- should be 0.2`);
}

/* -- 13c. the same laps at any frame rate -------------------------------- */
/* Same sweep as 9b, for the same reason: a wrap that loses the remainder quantises to
   the frame, so it is invisible at 60fps and only shows as a different lap count on a
   different monitor. */
{
  const bars4 = [1, 2, 3].map((n) => ({ number: n, onset: (n - 1) * 4, beats: 4, beat_type: 4 }));
  const laps = [];
  for (const fps of [30, 60, 100, 144]) {
    const g = createGhost(piece([[0, 1, 60, 1], [4, 1, 62, 1], [8, 1, 64, 1]], bars4));
    g.setWait(false);
    g.setTempo(120);
    g.seek(0); g.setLoopA();
    g.seek(3); g.setLoopB();        // in bar 1, so B ceils to the bar line at 4
    g.seek(0);
    g.play();
    let seen = 0;
    let last = g.seq;
    // Ten and a half seconds of a two-second lap. Not ten: the fifth wrap would land
    // on the last frame, and whether it lands at all would come down to how the fps
    // rounds -- which is the accumulated-float question, not the one being asked.
    for (let f = 0; f < Math.round(fps * 10.5); f++) {
      g.advance(1 / fps);
      if (g.seq !== last) { seen++; last = g.seq; }
    }
    laps.push(seen);
  }
  ok('the lap count agrees at 30/60/100/144 fps', laps.every((n) => n === laps[0]),
     `laps ${laps.join(', ')}`);
  ok('and it is the five a two-second lap fits in ten and a half', laps[0] === 5,
     `${laps[0]} laps`);
}

/* -- 13d. the lap does not open its own first gate ----------------------- */
/* A section that begins and ends on the same pitch is the normal case -- it is a
   phrase -- and the hand you finished the lap with is still down when the wrap lands.
   seek() spends what is held, which is what stops that from being a free pass. */
{
  const bars2 = [1, 2].map((n) => ({ number: n, onset: (n - 1) * 4, beats: 4, beat_type: 4 }));
  const g = createGhost(piece([[0, 1, 60, 1], [2, 1, 60, 1]], bars2));
  g.setTempo(240);
  g.seek(0); g.setLoopA();
  g.seek(4); g.setLoopB();
  g.seek(0);
  g.play();
  g.frame({ held: [60] });                  // opens the gate at A, then blocks on the next
  for (let f = 0; f < 60; f++) g.advance(1 / 60);
  ok('the section plays until its second C, still holding the first', near(g.nowQ, 2),
     `nowQ=${g.nowQ.toFixed(3)}`);

  const seq0 = g.seq;
  g.frame({ held: [] });
  g.frame({ held: [60] });                  // struck again: the section runs out and wraps
  for (let f = 0; f < 120; f++) g.advance(1 / 60);
  ok('the lap wraps with the last note of the section still down',
     g.seq > seq0 && near(g.nowQ, 0), `nowQ=${g.nowQ.toFixed(3)}, seq ${seq0}->${g.seq}`);
  ok('and that key does not open the new lap for free', g.waiting === true);

  g.frame({ held: [] });
  g.frame({ held: [60] });
  for (let f = 0; f < 60; f++) g.advance(1 / 60);
  ok('striking it afresh does', g.nowQ > 0, `nowQ=${g.nowQ.toFixed(3)}`);
}

/* -- 13e. wait mode inside a section ------------------------------------- */
{
  const bars2 = [1, 2].map((n) => ({ number: n, onset: (n - 1) * 4, beats: 4, beat_type: 4 }));
  const g = createGhost(piece([[0, 1, 60, 1], [2, 1, 62, 1], [4, 1, 64, 1]], bars2));
  g.setTempo(120);
  g.seek(0); g.setLoopA();
  g.seek(4); g.setLoopB();
  g.seek(0);
  g.play();
  g.frame({ held: [60] });
  for (let f = 0; f < 600; f++) g.advance(1 / 60);
  ok('a gate inside the section still freezes the clock', near(g.nowQ, 2),
     `nowQ=${g.nowQ.toFixed(3)}`);
  ok('and says it is waiting rather than looping in silence', g.waiting === true);
}

/* -- 13f. the degenerate section ----------------------------------------- */
/* A and B on one bar line makes the distance from A to B zero, and the wrap subtracts
   that distance out of the frame's remaining music -- so an unguarded version never
   reduces `left` and hangs the tab. MIN_LOOP_Q is why `looping` stays false here.
   Ceiling B leaves exactly one way to reach that: the last bar line IS the end of the
   piece, so there is no later line and no remainder for B to take. */
{
  const bars2 = [1, 2].map((n) => ({ number: n, onset: (n - 1) * 4, beats: 4, beat_type: 4 }));
  const g = createGhost(piece([[0, 1, 60, 1], [3, 1, 62, 1]], bars2));   // total = 4
  g.setWait(false);
  g.seek(4); g.setLoopA(); g.setLoopB();      // the last bar line, which is also the end
  ok('a zero-length section is not a section', g.looping === false,
     `A=${g.loopA} B=${g.loopB}`);
  ok('and it says no rather than arming silently', g.setLoopB() === false);
  g.seek(3.5);
  g.play();
  const t0 = Date.now();
  for (let f = 0; f < 300; f++) g.advance(1 / 60);
  ok('and driving through it terminates rather than hanging',
     Date.now() - t0 < 2000 && g.nowQ > 3.5, `nowQ=${g.nowQ.toFixed(3)}`);
}

/* -- 13g. locating past B stays past B ----------------------------------- */
{
  const bars4 = [1, 2, 3, 4].map((n) => (
    { number: n, onset: (n - 1) * 4, beats: 4, beat_type: 4 }));
  const g = createGhost(piece(
    [[0, 1, 60, 1], [4, 1, 62, 1], [8, 1, 64, 1], [12, 1, 65, 1]], bars4));
  g.setWait(false);
  g.setTempo(120);
  g.seek(0); g.setLoopA();
  g.seek(7); g.setLoopB();           // in bar 2, so B ceils to the bar line at 8
  g.seek(10);                        // a scrub click past the section's end
  g.play();
  for (let f = 0; f < 60; f++) g.advance(1 / 60);
  ok('scrubbing past B plays on rather than teleporting back to A', g.nowQ > 10,
     `nowQ=${g.nowQ.toFixed(3)}`);
  // ...and locating BEFORE A plays in and then loops, which is the other half of it.
  g.seek(0);
  for (let f = 0; f < 60 * 6; f++) g.advance(1 / 60);
  ok('while landing before A plays in and then wraps', g.nowQ < 8,
     `nowQ=${g.nowQ.toFixed(3)}`);
  g.clearLoop();
  ok('clearing puts the whole piece back', g.looping === false);
  for (let f = 0; f < 60 * 6; f++) g.advance(1 / 60);
  ok('and the playhead runs past the old B', g.nowQ > 8, `nowQ=${g.nowQ.toFixed(3)}`);
}

/* -- 13h. Set B where the bar line has just gone past -------------------- */
/* The way the section actually died, and the reason these three positions are 20 ms
   apart. You decide a phrase has ended by HEARING it end, so the click lands a hair
   AFTER the bar line -- and nearest-snap then stamped B behind the playhead, where
   advance()'s deliberate "past B plays on" rule means the clock never wraps. The band
   was drawn and the readout said "looping" while nothing looped. Two of these three
   fail against nearest-snap; which one you got was a matter of milliseconds. */
{
  const bars5 = [1, 2, 3, 4, 5].map((n) => (
    { number: n, onset: (n - 1) * 4, beats: 4, beat_type: 4 }));
  const notes = [[0, 1, 60, 1], [4, 1, 62, 1], [8, 1, 64, 1], [12, 1, 65, 1], [16, 4, 67, 1]];
  const lapsIn = (g, seconds) => {
    let seen = 0;
    let last = g.seq;
    for (let f = 0; f < Math.round(60 * seconds); f++) {
      g.advance(1 / 60);
      if (g.seq !== last) { seen++; last = g.seq; }
    }
    return seen;
  };

  for (const at of [7.98, 8, 8.05]) {
    const g = createGhost(piece(notes, bars5));
    g.setWait(false);
    g.setTempo(240);
    g.seek(4); g.setLoopA();
    g.seek(at); g.setLoopB();
    g.play();
    const laps = lapsIn(g, 10);
    ok(`Set B at ${at} arms a section the clock really loops`, laps > 0,
       `A=${g.loopA} B=${g.loopB} laps=${laps} nowQ=${g.nowQ.toFixed(2)}`);
  }

  // Both stamps inside one bar mean that one bar. Nearest-snap collapsed them onto the
  // same line, markLoop refused, and two presses did nothing and said nothing.
  const one = createGhost(piece(notes, bars5));
  one.setWait(false);
  one.setTempo(240);
  one.seek(9.5);
  one.setLoopA();
  one.setLoopB();
  ok('Set A then Set B inside one bar is that one bar',
     near(one.loopA, 8) && near(one.loopB, 12) && one.looping === true,
     `A=${one.loopA} B=${one.loopB} looping=${one.looping}`);
  one.play();
  const laps = lapsIn(one, 10);
  ok('and the one-bar section laps rather than sitting there', laps > 0, `laps=${laps}`);

  // Set A on its own marks the start and NOTHING else. B starts at 0, so the rule that
  // pushes B out of A's way used to fire on the very first press -- one click on "set
  // the start" and the piece began wrapping the bar you were standing in.
  const solo = createGhost(piece(notes, bars5));
  solo.setWait(false);
  solo.setTempo(240);
  solo.seek(6.5);
  ok('Set A alone does not arm a loop', solo.setLoopA() === false,
     `A=${solo.loopA} B=${solo.loopB} looping=${solo.looping}`);
  solo.play();
  ok('and the clock runs straight past the bar it marked',
     lapsIn(solo, 10) === 0 && solo.nowQ > 8, `nowQ=${solo.nowQ.toFixed(3)}`);
  // ...and Set B still closes it, from the A that was already stamped.
  solo.seek(13);
  solo.setLoopB();
  ok('Set B then closes the section A opened',
     near(solo.loopA, 4) && near(solo.loopB, 16) && solo.looping === true,
     `A=${solo.loopA} B=${solo.loopB}`);
}

console.log(JSON.stringify(out));
"""


def main() -> int:
    node = shutil.which("node")
    if not node:
        print("node not found -- this check needs it to run the real modules")
        print("install Node, or run tools\\frontend_check.py for the structural pass")
        return 1

    driver = FRONTEND / "_ghost_check.mjs"
    driver.write_text(DRIVER, encoding="utf-8")
    try:
        proc = subprocess.run([node, str(driver)], capture_output=True, text=True)
    finally:
        driver.unlink(missing_ok=True)

    if proc.returncode != 0:
        print("the driver did not run:")
        print(proc.stderr.strip() or proc.stdout.strip())
        return 1

    try:
        results = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        print("the driver produced nothing readable:")
        print(proc.stdout.strip()[:2000])
        return 1

    ok = True
    for r in results:
        ok = ok and r["passed"]
        detail = f" -- {r['detail']}" if r.get("detail") else ""
        print(f"  [{'PASS' if r['passed'] else 'FAIL'}] {r['label']}{detail}")

    print()
    print(f"{'ALL CHECKS PASSED' if ok else 'FAILED'}  ({len(results)} assertions)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
