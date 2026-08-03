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

/* -- 5. the escape ------------------------------------------------------- */
{
  const g = createGhost(piece([[0, 1, 60, 1], [1, 1, 62, 1]]));
  g.play();
  for (let f = 0; f < 60 * 6; f++) g.advance(1 / 60);      // six seconds, nothing held
  ok('a blocked gate is still blocked at six seconds', near(g.nowQ, 0));
  for (let f = 0; f < 60 * 4; f++) g.advance(1 / 60);      // past the eight-second escape
  ok('but it releases itself rather than hanging forever', g.nowQ > 0,
     `nowQ=${g.nowQ.toFixed(3)} after ten seconds blocked`);
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
