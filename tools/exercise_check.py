"""Regression test for the exercise engine. No piano, no FluidSynth, no browser.

    .venv\\Scripts\\python.exe tools\\exercise_check.py

Generation is pure arithmetic, so the whole parameter space is cheap enough to walk
exhaustively -- every key against every mode against every octave count -- and that is
exactly what catches the bugs worth catching here. A scale that runs off the end of the
keyboard is silent, not wrong-sounding, and no amount of playing it will tell you which
of the 15 keys is broken.

The GenContext wants a Store, so one lives in a throwaway temp directory and the real
keys.db is fingerprinted before and after. A check that writes to the practice log is a
bug, and has been one before.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import random
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config  # noqa: E402
from backend.exercises import REGISTRY, GenContext, Plan  # noqa: E402
from backend.exercises import scales  # noqa: E402,F401  -- imported to register
from backend.music import KEYS, MODES  # noqa: E402
from backend.store import Store  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="keys-exercise-check-"))
REAL_DB = config.DB_PATH
REAL_BEFORE = (REAL_DB.exists(), REAL_DB.stat().st_mtime_ns if REAL_DB.exists() else 0,
               REAL_DB.stat().st_size if REAL_DB.exists() else 0)

STORE = Store(TMP / "exercise-check.db")
CTX = GenContext(store=STORE, rng=random.Random(1))

LOW, HIGH = 21, 108
ok = True


def step(label: str, passed: bool, detail: str = "") -> None:
    global ok
    ok = ok and passed
    print(f"  [{'PASS' if passed else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))


def gen(exercise: str, **params) -> Plan:
    return REGISTRY[exercise].generate(params, CTX)


def midi(plan: Plan) -> list[int]:
    return [n for s in plan.steps for n in s.notes]


def line(plan: Plan, which: int) -> list[int]:
    """One hand's notes. 0 is the lower (left) line on a two-hand plan."""
    return [s.notes[which] for s in plan.steps]


# The four hand configurations that produce different note sets. Motion is ignored on a
# one-hand plan, which is itself checked below.
SHAPES = (("R", "parallel"), ("L", "parallel"), ("B", "parallel"), ("B", "contrary"))
QUALITIES = tuple(v for v, _l in
                  next(p for p in REGISTRY["arpeggio"].params if p.id == "quality").choices)

print("1. the registry")
step("both types registered", "scale" in REGISTRY and "arpeggio" in REGISTRY,
     ", ".join(sorted(REGISTRY)))
step("names", REGISTRY["scale"].name == "Scales" and REGISTRY["arpeggio"].name == "Arpeggios")
scale_params = {p.id: p for p in REGISTRY["scale"].params}
arp_params = {p.id: p for p in REGISTRY["arpeggio"].params}
step("scale controls", set(scale_params) == {"key", "mode", "octaves", "hands", "motion",
                                             "bpm", "timed", "updown"},
     ", ".join(sorted(scale_params)))
step("arpeggio controls", set(arp_params) == {"key", "quality", "inversion", "pattern",
                                              "octaves", "hands", "motion", "bpm",
                                              "timed", "updown"},
     ", ".join(sorted(arp_params)))
step("kinds and defaults",
     scale_params["key"].kind == "key" and scale_params["key"].default == "C"
     and scale_params["mode"].kind == "choice" and scale_params["mode"].default == "major"
     and scale_params["octaves"].kind == "int" and scale_params["octaves"].lo == 1
     and scale_params["octaves"].hi == 4 and scale_params["octaves"].default == 2
     and scale_params["hands"].default == "R" and scale_params["motion"].default == "parallel"
     and scale_params["bpm"].kind == "bpm" and scale_params["bpm"].default == 80
     and scale_params["bpm"].lo == 30 and scale_params["bpm"].hi == 200
     and scale_params["timed"].default is True and scale_params["updown"].default is True)
step("every key in music.KEYS is offered",
     [v for v, _l in scale_params["key"].choices] == KEYS)
step("every mode in music.MODES is offered",
     sorted(v for v, _l in scale_params["mode"].choices) == sorted(MODES),
     f"{len(scale_params['mode'].choices)} modes")
step("inversion is 0..3", arp_params["inversion"].lo == 0 and arp_params["inversion"].hi == 3)
step("to_dict survives the trip",
     REGISTRY["scale"].to_dict()["params"][0]["id"] == "key"
     and REGISTRY["arpeggio"].to_dict()["id"] == "arpeggio")

print("2. every generated note is on the keyboard")
bad: list[str] = []
shape_bad: list[str] = []
plans = 0
for key in KEYS:
    for mode in MODES:
        for octaves in (1, 2, 3, 4):
            for hands, motion in SHAPES:
                p = gen("scale", key=key, mode=mode, octaves=octaves, hands=hands,
                        motion=motion, updown=True, timed=True, bpm=80)
                plans += 1
                notes = midi(p)
                if not notes:
                    bad.append(f"{key} {mode} {octaves}oct {hands}/{motion}: no notes")
                    continue
                if min(notes) < LOW or max(notes) > HIGH:
                    bad.append(f"{key} {mode} {octaves}oct {hands}/{motion}: "
                               f"{min(notes)}..{max(notes)}")
                for s in p.steps:
                    if s.fingers and len(s.fingers) != len(s.notes):
                        shape_bad.append(f"{p.variant}: {s.fingers} vs {s.notes}")
                    if len(set(s.notes)) != len(s.notes):
                        shape_bad.append(f"{p.variant}: duplicate notes {s.notes}")
step(f"{plans} scale plans stay inside midi 21..108", not bad,
     "; ".join(bad[:3]) if bad
     else f"{len(KEYS)} keys x {len(MODES)} modes x 4 octaves x 4 hand shapes")

arp_bad: list[str] = []
arp_plans = 0
for key in KEYS:
    for quality in QUALITIES:
        for inversion in (0, 1, 2, 3):
            for octaves in (1, 2, 3, 4):
                for pattern in ("straight", "broken"):
                    for hands, motion in SHAPES:
                        p = gen("arpeggio", key=key, quality=quality, inversion=inversion,
                                pattern=pattern, octaves=octaves, hands=hands,
                                motion=motion, updown=True, timed=True, bpm=80)
                        arp_plans += 1
                        notes = midi(p)
                        if not notes or min(notes) < LOW or max(notes) > HIGH:
                            arp_bad.append(f"{p.variant}: {min(notes)}..{max(notes)}"
                                           if notes else f"{p.variant}: no notes")
                        for s in p.steps:
                            if s.fingers and len(s.fingers) != len(s.notes):
                                shape_bad.append(f"{p.variant}: {s.fingers} vs {s.notes}")
step(f"{arp_plans} arpeggio plans stay inside midi 21..108", not arp_bad,
     "; ".join(arp_bad[:3]) if arp_bad else "")
step("fingers stay parallel to notes and a step never repeats a note", not shape_bad,
     "; ".join(shape_bad[:3]))

print("3. timed and untimed")
timed = gen("scale", key="C", mode="major", octaves=2, hands="R", timed=True, bpm=96)
beats = [s.beat for s in timed.steps]
step("timed plan carries a bpm", timed.bpm == 96.0 and timed.timed, str(timed.bpm))
step("beats start at zero", beats[0] == 0.0, str(beats[0]))
step("beats are monotonically non-decreasing",
     all(b is not None for b in beats)
     and all(a <= b for a, b in zip(beats, beats[1:])), str(beats[:5]))
step("two steps to the beat", beats[2] == 1.0 and beats[8] == 4.0, str(beats[:9]))
step("timed plans look ahead", timed.lookahead == 2, str(timed.lookahead))
untimed = gen("scale", key="C", mode="major", octaves=2, hands="R", timed=False, bpm=96)
step("untimed plan has no bpm", untimed.bpm is None and not untimed.timed)
step("untimed beats are all None", all(s.beat is None for s in untimed.steps))
step("untimed plans block on a wrong note", untimed.lookahead == 0)
step("the notes are the same either way", midi(timed) == midi(untimed))
step("both are grand-staff", timed.staff == "grand" and untimed.staff == "grand")
step("scales show fingering", timed.show_fingers is True)
mixed_bad = [p.variant for p in (
    gen("scale", key=k, mode=m, octaves=o, hands=h, motion=mo, timed=True, bpm=200)
    for k in ("C", "F#", "Eb") for m in ("major", "blues", "chromatic")
    for o in (1, 4) for h, mo in SHAPES)
    if any(a > b for a, b in zip([s.beat for s in p.steps], [s.beat for s in p.steps][1:]))]
step("beats never go backwards anywhere", not mixed_bad, "; ".join(mixed_bad[:3]))

print("4. the variant slug is stable")
args = dict(key="C", mode="major", octaves=2, hands="B", motion="parallel", updown=True)
first = gen("scale", **args, timed=True, bpm=80)
second = gen("scale", **args, timed=True, bpm=80)
step("same params, same slug", first.variant == second.variant, first.variant)
step("same params, same notes", midi(first) == midi(second))
step("the slug is the documented shape", first.variant == "C:major:2oct:B:parallel:updown",
     first.variant)
step("tempo is not part of the identity",
     gen("scale", **args, timed=True, bpm=200).variant == first.variant
     and gen("scale", **args, timed=False, bpm=80).variant == first.variant)
step("one hand ignores motion",
     gen("scale", key="C", mode="major", octaves=2, hands="R", motion="contrary").variant
     == gen("scale", key="C", mode="major", octaves=2, hands="R", motion="parallel").variant
     == "C:major:2oct:R:parallel:updown")
step("up-only is a different exercise",
     gen("scale", **{**args, "updown": False}).variant == "C:major:2oct:B:parallel:up")
step("the arpeggio slug names every control",
     gen("arpeggio", key="Eb", quality="dom7", inversion=2, pattern="broken", octaves=1,
         hands="L").variant == "Eb:dom7:inv2:1oct:L:parallel:broken:updown",
     gen("arpeggio", key="Eb", quality="dom7", inversion=2, pattern="broken", octaves=1,
         hands="L").variant)
step("the title reads like English",
     first.title == "C major, 2 octaves, hands together", first.title)
step("contrary motion says so",
     gen("scale", key="A", mode="harmonic_minor", octaves=1, hands="B",
         motion="contrary").title == "A harmonic minor, 1 octave, hands together, "
                                     "contrary motion")
squeezed = gen("scale", key="C", mode="major", octaves=4, hands="B", motion="contrary")
step("an octave count that cannot fit is reduced, not clipped",
     squeezed.params["octaves"] == 3 and "3oct" in squeezed.variant
     and min(midi(squeezed)) >= LOW and max(midi(squeezed)) <= HIGH,
     f"{squeezed.variant} {min(midi(squeezed))}..{max(midi(squeezed))}")
step("params round-trip the rest of the form",
     first.params["key"] == "C" and first.params["mode"] == "major"
     and first.params["hands"] == "B" and first.params["bpm"] == 80)

print("5. hands")
right = gen("scale", key="C", mode="major", octaves=2, hands="R")
left = gen("scale", key="C", mode="major", octaves=2, hands="L")
both = gen("scale", key="C", mode="major", octaves=2, hands="B")
step("one hand is one note per step",
     all(len(s.notes) == 1 for s in right.steps)
     and all(len(s.notes) == 1 for s in left.steps))
step("both hands is two notes per step", all(len(s.notes) == 2 for s in both.steps))
step("the hand field matches",
     {s.hand for s in right.steps} == {"R"} and {s.hand for s in left.steps} == {"L"}
     and {s.hand for s in both.steps} == {"B"})
step("the left hand plays lower than the right",
     max(midi(left)) < max(midi(right)) and min(midi(left)) < min(midi(right)),
     f"L {min(midi(left))}..{max(midi(left))}  R {min(midi(right))}..{max(midi(right))}")
step("right hand starts on middle C in C major", right.steps[0].notes == (60,),
     str(right.steps[0].notes))
step("parallel motion is an octave apart the whole way",
     all(s.notes[1] - s.notes[0] == 12 for s in both.steps))
step("parallel hands play the same run an octave apart",
     line(both, 1) == midi(right) and line(both, 0) == [n - 12 for n in midi(right)])
step("the same step count either way",
     len(right.steps) == len(left.steps) == len(both.steps) == 29, len(both.steps))

print("6. contrary motion genuinely diverges")
contrary = gen("scale", key="C", mode="major", octaves=2, hands="B", motion="contrary")
head = [s.notes for s in contrary.steps[:4]]
# RH  C4 D4 E4 F4  ascending; LH  C3 B2 A2 G2  descending the same seven pitch classes.
step("the first four steps are exact",
     head == [(48, 60), (47, 62), (45, 64), (43, 65)], str(head))
out = gen("scale", key="C", mode="major", octaves=2, hands="B", motion="contrary",
          updown=False)
gaps = [s.notes[1] - s.notes[0] for s in out.steps]
step("the hands move apart on every single step",
     all(a < b for a, b in zip(gaps, gaps[1:])) and gaps == sorted(gaps)
     and gaps[0] == 12 and gaps[-1] == 60, f"{gaps[0]}..{gaps[-1]}")
updown_gaps = [s.notes[1] - s.notes[0] for s in contrary.steps]
step("and come back together on the way down",
     updown_gaps == updown_gaps[::-1] and max(updown_gaps) == 60
     and updown_gaps.index(60) == len(updown_gaps) // 2, str(max(updown_gaps)))
step("the left hand really descends",
     line(contrary, 0)[:8] == [48, 47, 45, 43, 41, 40, 38, 36], str(line(contrary, 0)[:8]))
step("both hands stay in the scale",
     set(n % 12 for n in midi(contrary)) == set(MODES["major"]),
     str(sorted(set(n % 12 for n in midi(contrary)))))
step("contrary is not parallel", midi(contrary) != midi(both))

print("7. up and down is a palindrome")
for hands, motion in SHAPES:
    p = gen("scale", key="Bb", mode="melodic_minor", octaves=2, hands=hands,
            motion=motion, updown=True)
    lines = [line(p, i) for i in range(len(p.steps[0].notes))]
    step(f"{hands}/{motion} reads the same backwards",
         all(ln == ln[::-1] for ln in lines), str(lines[0][:4]))
up_only = gen("scale", key="Bb", mode="melodic_minor", octaves=2, hands="R", updown=False)
step("up-only climbs and stops",
     all(a < b for a, b in zip(midi(up_only), midi(up_only)[1:]))
     and len(up_only.steps) == 15, len(up_only.steps))
step("up and down is one step short of double",
     len(gen("scale", key="Bb", mode="melodic_minor", octaves=2, hands="R").steps) == 29)
step("the top note is struck once",
     midi(up_only)[-1] == max(midi(up_only))
     and midi(gen("scale", key="Bb", mode="melodic_minor", octaves=2,
                  hands="R")).count(max(midi(up_only))) == 1)

print("8. arpeggio inversions rotate the chord tones")
def arp(**kw) -> list[int]:
    return midi(gen("arpeggio", key="C", quality="major", octaves=2, hands="R",
                    updown=False, **kw))


step("root position is C E G C", arp(inversion=0)[:4] == [60, 64, 67, 72],
     str(arp(inversion=0)))
step("1st inversion starts on the third", arp(inversion=1)[:4] == [64, 67, 72, 76],
     str(arp(inversion=1)[:4]))
step("2nd inversion starts on the fifth", arp(inversion=2)[:4] == [67, 72, 76, 79],
     str(arp(inversion=2)[:4]))
step("a triad has no 3rd inversion, so it clamps", arp(inversion=3) == arp(inversion=2))
step("the slug records the clamp",
     gen("arpeggio", key="C", quality="major", inversion=3).variant.split(":")[2] == "inv2")
sevenths = midi(gen("arpeggio", key="C", quality="dim7", inversion=3, octaves=1,
                    hands="R", updown=False))
step("a seventh chord does have one", sevenths == [69, 72, 75, 78, 81], str(sevenths))
step("every inversion holds the same pitch classes",
     {n % 12 for n in arp(inversion=0)} == {n % 12 for n in arp(inversion=1)}
     == {n % 12 for n in arp(inversion=2)} == {0, 4, 7})
broken = midi(gen("arpeggio", key="C", quality="major", octaves=1, hands="R",
                  pattern="straight", updown=False))
step("straight runs the chord tones and keeps climbing", broken == [60, 64, 67, 72],
     str(broken))
figure = midi(gen("arpeggio", key="C", quality="major", octaves=1, hands="R",
                  pattern="broken", updown=False))
step("broken repeats a three-note figure from every position",
     figure == [60, 64, 67, 64, 67, 72, 67, 72, 76], str(figure))
step("broken and straight are different exercises",
     gen("arpeggio", key="C", pattern="broken").variant
     != gen("arpeggio", key="C", pattern="straight").variant)
step("arpeggios do not claim a fingering they were not given",
     gen("arpeggio", key="C").show_fingers is False)

print("9. the real keys.db was never touched")
after = (REAL_DB.exists(), REAL_DB.stat().st_mtime_ns if REAL_DB.exists() else 0,
         REAL_DB.stat().st_size if REAL_DB.exists() else 0)
step("keys.db unchanged", after == REAL_BEFORE,
     f"{REAL_DB} exists={after[0]} (was {REAL_BEFORE[0]})")

STORE.close()
shutil.rmtree(TMP, ignore_errors=True)
print()
print("ALL CHECKS PASSED" if ok else "SOMETHING FAILED")
sys.exit(0 if ok else 1)
