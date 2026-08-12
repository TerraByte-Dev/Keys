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

Sections 9 to 12 are a different kind of test. `backend/exercises/fingering.py` is not
arithmetic -- it is a table transcribed from published charts that disagree with each
other, and every digit in it is one keystroke away from teaching the wrong thing every
morning. So every row is checked against pitches computed here from `backend.music`, by
a completely different path than the one that produced the table, and against the
structural rules every scale fingering in the literature obeys. A typo in any row has to
fail, which is worth far more than a clever generator that gets B major's left hand wrong.
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
from backend.exercises.fingering import (  # noqa: E402
    ARPEGGIO_FINGERING, SCALE_FINGERING, crossings, fingers_for)
from backend.music import KEYS, MODES, scale_pitch_classes  # noqa: E402
from backend.store import Store  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="keys-exercise-check-"))
REAL_DB = config.DB_PATH
REAL_BEFORE = (REAL_DB.exists(), REAL_DB.stat().st_mtime_ns if REAL_DB.exists() else 0,
               REAL_DB.stat().st_size if REAL_DB.exists() else 0)

STORE = Store(TMP / "exercise-check.db")
CTX = GenContext(store=STORE, rng=random.Random(1))

# Pinned to a scratch settings file holding the 88 keys, deliberately, and this is the
# one line in the file that matters most.
#
# The generators read the instrument range at generation time now. Left alone, this
# check would inherit whatever is in the developer's own config.local.json -- so it
# would pass on their machine and assert nothing about anyone else's, and every
# absolute-MIDI expectation below ("right hand starts on middle C", the exact contrary
# head, the 29-step counts) would be measuring a keyboard nobody chose. A test whose
# expected values come from live user settings is the same failure CONTRIBUTING.md
# already names twice.
config.settings = config.Settings(TMP / "settings.json")
config.settings.update({"instrument": {"low": 21, "high": 108}})
LOW, HIGH = config.instrument_range()
ok = True


def step(label: str, passed: bool, detail: str = "") -> None:
    global ok
    ok = ok and passed
    print(f"  [{'PASS' if passed else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))


def gen(exercise: str, **params) -> Plan:
    return REGISTRY[exercise].generate(params, CTX)


def _refusal() -> tuple[str, bool]:
    """The first refusal a 25-key controller produces, and whether it is worth reading.

    Returns (message, usable). It used to compute the verdict and then throw it away,
    returning a string on both branches while the caller only tested `is not None` --
    an assertion that could not fail, which is worse than no assertion.
    """
    config.settings.update({"instrument": {"low": 48, "high": 72}})
    try:
        for key in KEYS:
            try:
                gen("scale", key=key, hands="B", octaves=2)
            except ValueError as err:
                text = str(err)
                # It has to name the key, the size of the keyboard, and a way out --
                # anything less is a shrug rather than an answer.
                return text, (key in text and "25" in text and "Try" in text)
        return "nothing refused on a 25-key at all", False
    finally:
        config.settings.update({"instrument": {"low": 21, "high": 108}})


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
# ARPEGGIO_FINGERING is empty on purpose, so no arpeggio may carry a finger or a crossing
# flag -- for ANY quality. "major" and "minor" are also scale-form names, so a generator
# that passes its quality through as the fingering form silently hands a C major arpeggio
# the C major SCALE fingering: right for the notes of a scale, wrong for these notes, and
# invisible because show_fingers is False. The crossing flags reach metrics.crossing_cost_ms
# and the practice log either way, which is where a fabricated number would live forever.
_arp_fingered = [
    (q, inv, [s.fingers for s in p.steps], [s.crossing for s in p.steps])
    for q in QUALITIES for inv in (0, 1, 2, 3)
    for p in (gen("arpeggio", key="C", quality=q, inversion=inv, octaves=2, hands="R"),)
    if any(s.fingers for s in p.steps) or any(s.crossing for s in p.steps)
]
step("and no arpeggio quality borrows a scale's fingering by name", not _arp_fingered,
     str(_arp_fingered[:1]) if _arp_fingered else
     f"{len(QUALITIES)} qualities x 4 inversions, no fingers and no crossings")

print("9. the fingering table, every row")
FINGER_TONICS = ["C", "C#", "Db", "D", "Eb", "E", "F", "F#", "Gb", "G", "Ab", "A", "Bb",
                 "B", "Cb"]
FINGER_FORMS = ["major", "natural_minor", "harmonic_minor", "melodic_minor"]
NATURAL_TONICS = ["C", "D", "E", "F", "G", "A", "B"]
BLACK_PCS = frozenset({1, 3, 6, 8, 10})


def row_pcs(tonic: str, form: str) -> list[int]:
    """The 8 pitch classes of one ascending octave, index-aligned with a row."""
    tonic_pc = scale_pitch_classes(tonic, "major")[0]
    return [(tonic_pc + MODES[form][i % 7]) % 12 for i in range(8)]


def thumbs_in_cycle(pattern: tuple[int, ...], hand: str) -> list[int]:
    """Where the thumb falls inside the repeating 7-note cycle.

    Digits 0..6 in the right hand, 1..7 in the left: the other digit at each end is the
    terminal finger, played once per run rather than once per octave.
    """
    return [i for i in (range(0, 7) if hand == "R" else range(1, 8)) if pattern[i] == 1]


def thumb_gaps(indices: list[int]) -> list[int]:
    """Distances between consecutive thumbs around the 7-note cycle."""
    a, b = indices
    return sorted([b - a, 7 - (b - a)])


step("every tonic x form present",
     sorted(SCALE_FINGERING) == sorted((t, f) for t in FINGER_TONICS for f in FINGER_FORMS),
     f"{len(SCALE_FINGERING)} rows, expected {len(FINGER_TONICS) * len(FINGER_FORMS)}")
# Tied to music.KEYS rather than to the list above, because the failure this catches is a
# key the Scales form offers and the table has never heard of: the run still plays, the
# finger row is just silently empty for that one option. Cb was exactly that.
_no_row = sorted({k for k in KEYS for f in FINGER_FORMS if (k, f) not in SCALE_FINGERING})
step("every key the Scales form offers has all four standard forms", not _no_row,
     f"no fingering for {_no_row}" if _no_row else f"{len(KEYS)} keys x 4 forms")

f_shape: list[str] = []
f_thumb: list[str] = []
f_groups: list[str] = []
f_order: list[str] = []
f_terminal: list[str] = []
f_five: list[str] = []
for (f_tonic, f_form), row in sorted(SCALE_FINGERING.items()):
    pcs = row_pcs(f_tonic, f_form)
    for f_hand, pattern in (("R", row[0]), ("L", row[1])):
        tag = f"{f_tonic} {f_form} {f_hand}H {'-'.join(str(d) for d in pattern)}"

        if len(pattern) != 8 or not all(1 <= d <= 5 for d in pattern):
            f_shape.append(tag)
            continue

        on_black = [i for i, d in enumerate(pattern) if d == 1 and pcs[i] in BLACK_PCS]
        if on_black:
            f_thumb.append(f"{tag} (thumb on a black key at {on_black})")

        found = thumbs_in_cycle(pattern, f_hand)
        if len(found) != 2 or thumb_gaps(found) != [3, 4]:
            f_groups.append(f"{tag} (thumbs at {found})")
            continue

        # Inside a group the fingers move by exactly one: outward from the thumb in the
        # right hand, back toward it in the left.
        cycle = [pattern[i] for i in (range(0, 7) if f_hand == "R" else range(1, 8))]
        for j, digit in enumerate(cycle):
            nxt = cycle[(j + 1) % 7]
            if f_hand == "R" and nxt != 1 and nxt != digit + 1:
                f_order.append(f"{tag} ({digit} -> {nxt})")
            if f_hand == "L" and digit != 1 and nxt != digit - 1:
                f_order.append(f"{tag} ({digit} -> {nxt})")

        # The terminal finger extends the last group by one. This one line is what makes
        # C major end on 5, F major on 4 and Db major on 2, with no special cases.
        if f_hand == "R" and pattern[7] != pattern[6] + 1:
            f_terminal.append(f"{tag} (digit 7 is {pattern[7]}, not {pattern[6] + 1})")
        if f_hand == "L" and pattern[0] != pattern[1] + 1:
            f_terminal.append(f"{tag} (digit 0 is {pattern[0]}, not {pattern[1] + 1})")

        if f_hand == "R" and 5 in pattern[:7]:
            f_five.append(tag)

step("every row is 8 digits of 1..5", not f_shape, "; ".join(f_shape[:4]))
step("the thumb never lands on a black key", not f_thumb, "; ".join(f_thumb[:4]))
step("thumbs split the octave into a 3-group and a 4-group", not f_groups,
     "; ".join(f_groups[:4]))
step("fingers move by one inside a group", not f_order, "; ".join(f_order[:4]))
step("the terminal finger extends the last group by one", not f_terminal,
     "; ".join(f_terminal[:4]))
step("no 5 appears mid-scale in an RH pattern", not f_five, "; ".join(f_five[:4]))

# The 3-and-4 grouping reads most clearly on the white-key tonics, where the thumb starts
# on the tonic itself. F is the one that takes its four-group first, which is exactly why
# its octave note is 4 and not 5.
natural_rh = {(t, f): SCALE_FINGERING[(t, f)][0] for t in NATURAL_TONICS for f in FINGER_FORMS}
step("a natural tonic always starts the RH on the thumb",
     all(p[0] == 1 for p in natural_rh.values()),
     str({k: p[0] for k, p in natural_rh.items() if p[0] != 1}))
step("naturals group 3 then 4, except F which groups 4 then 3",
     all(thumbs_in_cycle(p, "R") == ([0, 4] if t == "F" else [0, 3])
         for (t, _f), p in natural_rh.items()),
     str({t: thumbs_in_cycle(SCALE_FINGERING[(t, "major")][0], "R") for t in NATURAL_TONICS}))
step("C major is the reference row",
     SCALE_FINGERING[("C", "major")] == ((1, 2, 3, 1, 2, 3, 4, 5), (5, 4, 3, 2, 1, 3, 2, 1)),
     str(SCALE_FINGERING[("C", "major")]))
step("the two spellings of a tonic agree",
     all(SCALE_FINGERING[(a, f)] == SCALE_FINGERING[(b, f)]
         for a, b in (("C#", "Db"), ("F#", "Gb"), ("Cb", "B")) for f in FINGER_FORMS))

# The rows that had to be researched rather than copied down a column. Each of these is a
# case where the widespread "a minor scale borrows its relative or parallel major's
# fingering" claim puts a thumb on a black key.
step("B major's LH is not D major's",
     SCALE_FINGERING[("B", "major")][1] == (4, 3, 2, 1, 4, 3, 2, 1)
     != SCALE_FINGERING[("D", "major")][1],
     "the relative-major claim would put this thumb on F#")
step("Bb and Eb minor keep their LH off their major's thumbs",
     SCALE_FINGERING[("Bb", "natural_minor")][1] == (2, 1, 3, 2, 1, 4, 3, 2)
     != SCALE_FINGERING[("Bb", "major")][1]
     and SCALE_FINGERING[("Eb", "natural_minor")][1] == (2, 1, 4, 3, 2, 1, 3, 2)
     != SCALE_FINGERING[("Eb", "major")][1],
     "Bb major's D and A become Db and Ab; Eb major's G becomes Gb")
step("Ab minor's LH changes with the form",
     SCALE_FINGERING[("Ab", "natural_minor")][1] == (3, 2, 1, 3, 2, 1, 4, 3)
     and SCALE_FINGERING[("Ab", "harmonic_minor")][1] == (3, 2, 1, 4, 3, 2, 1, 3),
     "raising the 7th turns Gb into G and lets the thumb move onto it")
step("Ab melodic minor uses the natural LH, not the harmonic one",
     SCALE_FINGERING[("Ab", "melodic_minor")][1]
     == SCALE_FINGERING[("Ab", "natural_minor")][1],
     "melodic descends as natural, where the harmonic thumb would land on Gb")
step("C# and F# melodic minor get their own RH",
     SCALE_FINGERING[("C#", "melodic_minor")][0] == (2, 3, 1, 2, 3, 4, 1, 2)
     != SCALE_FINGERING[("C#", "harmonic_minor")][0]
     and SCALE_FINGERING[("F#", "melodic_minor")][0] == (2, 3, 1, 2, 3, 4, 1, 2)
     != SCALE_FINGERING[("F#", "harmonic_minor")][0],
     "the raised 6th (A# / D#) is black and cannot take a thumb")

print("10. fingers_for() across octaves")
f_len: list[str] = []
f_ends: list[str] = []
f_octave: list[str] = []
for (f_tonic, f_form), row in sorted(SCALE_FINGERING.items()):
    for f_hand, pattern in (("R", row[0]), ("L", row[1])):
        if fingers_for(f_tonic, f_form, f_hand, 8) != pattern:
            f_octave.append(f"{f_tonic} {f_form} {f_hand}H "
                            f"{fingers_for(f_tonic, f_form, f_hand, 8)}")
        for octaves in (2, 3, 4):
            run = fingers_for(f_tonic, f_form, f_hand, 7 * octaves + 1)
            if len(run) != 7 * octaves + 1:
                f_len.append(f"{f_tonic} {f_form} {f_hand}H x{octaves}: {len(run)}")
                continue
            if run[0] != pattern[0] or run[-1] != pattern[7]:
                f_ends.append(f"{f_tonic} {f_form} {f_hand}H x{octaves}: "
                              f"{run[0]}..{run[-1]} (row {pattern[0]}..{pattern[7]})")
step("one octave reproduces the row exactly", not f_octave, "; ".join(f_octave[:4]))
step("2, 3 and 4 octaves are 15, 22 and 29 notes", not f_len, "; ".join(f_len[:4]))
step("every run starts on digit 0 and ends on digit 7", not f_ends, "; ".join(f_ends[:4]))
step("a natural tonic's RH ends on 5, and F's on 4",
     all(fingers_for(t, f, "R", 22)[-1] == (4 if t == "F" else 5)
         for t in NATURAL_TONICS for f in FINGER_FORMS),
     str({t: fingers_for(t, "major", "R", 22)[-1] for t in NATURAL_TONICS}))
step("a natural tonic's LH starts on 5 at the bottom, and B's on 4",
     all(fingers_for(t, f, "L", 22)[0] == (4 if t == "B" else 5)
         for t in NATURAL_TONICS for f in FINGER_FORMS),
     str({t: fingers_for(t, "major", "L", 22)[0] for t in NATURAL_TONICS}))
step("C major RH, 2 octaves",
     fingers_for("C", "major", "R", 15) == (1, 2, 3, 1, 2, 3, 4, 1, 2, 3, 1, 2, 3, 4, 5),
     str(fingers_for("C", "major", "R", 15)))
step("C major LH, 2 octaves",
     fingers_for("C", "major", "L", 15) == (5, 4, 3, 2, 1, 3, 2, 1, 4, 3, 2, 1, 3, 2, 1),
     str(fingers_for("C", "major", "L", 15)))
step("F major RH, 4 octaves -- the octave note is 1, only the very top is 4",
     fingers_for("F", "major", "R", 29) == (1, 2, 3, 4, 1, 2, 3) * 4 + (4,),
     str(fingers_for("F", "major", "R", 29)))
step("Gb major RH, 2 octaves -- Gb is 2 every time",
     fingers_for("Gb", "major", "R", 15) == (2, 3, 4, 1, 2, 3, 1, 2, 3, 4, 1, 2, 3, 1, 2),
     str(fingers_for("Gb", "major", "R", 15)))
step("B minor LH, 3 octaves -- the bottom 4 never comes back",
     fingers_for("B", "harmonic_minor", "L", 22)
     == (4,) + (3, 2, 1, 4, 3, 2, 1) * 3,
     str(fingers_for("B", "harmonic_minor", "L", 22)))
step("the enharmonic spelling gives the same run",
     fingers_for("C#", "melodic_minor", "R", 15) == fingers_for("Db", "melodic_minor", "R", 15))
step("case is normalised",
     fingers_for("bB", "major", "r", 8) == SCALE_FINGERING[("Bb", "major")][0])
step("a part-octave run gets the cycle's finger, not the terminal one",
     fingers_for("C", "major", "R", 5) == (1, 2, 3, 1, 2), str(fingers_for("C", "major", "R", 5)))
step("an unknown key or form returns ()",
     fingers_for("H", "major", "R", 8) == () and fingers_for("C", "lydian", "R", 8) == ())
step("no notes returns ()",
     fingers_for("C", "major", "R", 0) == () and fingers_for("C", "major", "R", -3) == ())
step("hands-together returns () -- a two-hand generator asks once per hand",
     fingers_for("C", "major", "B", 8) == ())
step("one note is just the first digit",
     fingers_for("C", "major", "R", 1) == (1,) and fingers_for("C", "major", "L", 1) == (5,))

print("11. crossings()")
# Hand-checked. C major RH ascending, two octaves:
#     1  2  3  1  2  3  4  1  2  3  1  2  3  4  5
#        ^     ^  ^        ^  ^     ^  ^
# 3, 7 and 10 are the thumb tucking under; 4, 8 and 11 are the hand leaving it again.
# Index 1 is the departure from the *starting* thumb, which is not a crossing -- the one
# false positive this convention accepts, documented in fingering.crossings.
rh_run = fingers_for("C", "major", "R", 15)
lh_run = fingers_for("C", "major", "L", 15)
step("C major RH, 2 octaves",
     [i for i, c in enumerate(crossings(rh_run)) if c] == [1, 3, 4, 7, 8, 10, 11],
     str([i for i, c in enumerate(crossings(rh_run)) if c]))
# LH ascending, two octaves:
#     5  4  3  2  1  3  2  1  4  3  2  1  3  2  1
#                    ^  ^     ^  ^        ^  ^  ^
# 5, 8 and 12 are the hand passing OVER a planted thumb -- the hard moment in this
# direction, and the whole reason the second clause exists.
step("C major LH, 2 octaves",
     [i for i, c in enumerate(crossings(lh_run)) if c] == [4, 5, 7, 8, 11, 12, 14],
     str([i for i, c in enumerate(crossings(lh_run)) if c]))
step("Bb major RH, one octave (4-1-2-3-1-2-3-4)",
     crossings(SCALE_FINGERING[("Bb", "major")][0])
     == (False, True, True, False, True, True, False, False),
     str(crossings(SCALE_FINGERING[("Bb", "major")][0])))
step("index 0 is never a crossing",
     crossings((1, 2, 3))[0] is False and crossings((5, 4, 3))[0] is False)
step("empty and single-note inputs", crossings(()) == () and crossings((3,)) == (False,))
step("a run with no thumb has no crossings",
     crossings((5, 4, 3, 2)) == (False, False, False, False))
# metrics.crossing_cost_ms wants at least 2 flagged steps and 3 unflagged ones before it
# will report a number at all, so one octave has to clear that bar on its own.
octave_flags = [sum(crossings(fingers_for(t, f, h, 8)))
                for t in FINGER_TONICS for f in FINGER_FORMS for h in "RL"]
step("one octave always yields enough crossings for the metric",
     all(2 <= n <= 5 for n in octave_flags),
     f"{min(octave_flags)}..{max(octave_flags)} flagged of 8 steps")
# Against the generated run rather than the row: this is what would catch a wrap that
# moved a thumb onto a black key at an octave boundary.
f_cross: list[str] = []
for (f_tonic, f_form), _row in sorted(SCALE_FINGERING.items()):
    pcs = row_pcs(f_tonic, f_form)
    for f_hand in "RL":
        run = fingers_for(f_tonic, f_form, f_hand, 15)
        for i, flagged in enumerate(crossings(run)):
            if flagged and run[i] == 1 and pcs[i % 7] in BLACK_PCS:
                f_cross.append(f"{f_tonic} {f_form} {f_hand}H at {i}")
step("every thumb crossing in a 2-octave run lands on a white key", not f_cross,
     "; ".join(f_cross[:4]))

print("12. arpeggio fingering is deferred, not half-done")
step("ARPEGGIO_FINGERING ships empty", ARPEGGIO_FINGERING == {},
     "root-position triads were never cross-checked, so none are published")
step("an arpeggio form returns ()",
     fingers_for("C", "major_triad", "R", 4) == ()
     and fingers_for("F#", "minor_triad", "L", 4) == (),
     "callers already cope with a missing fingering")
step("and the arpeggio generator does not claim one",
     gen("arpeggio", key="C").show_fingers is False
     and not any(s.fingers for s in gen("arpeggio", key="C").steps))

print("13. a smaller keyboard: every note is reachable, or the generator refuses")
# The bug this pins is not cosmetic. An out-of-range target does not merely go silent --
# run.py blocks on it, _miss never advances the cursor, and an untimed plan has no
# lookahead to escape with. So a scale that ran off the top of a 25-key controller hung
# the exercise forever with nothing on screen to explain it. _fit computed its own
# failure and the caller threw the answer away.
BOARDS = ((61, 36, 96), (49, 36, 84), (25, 48, 72))
for keys, lo, hi in BOARDS:
    config.settings.update({"instrument": {"low": lo, "high": hi}})
    off, refused, built = [], 0, 0
    for ex in ("scale", "arpeggio"):
        for key in KEYS:
            for hands in ("R", "L", "B"):
                for octaves in (1, 2, 4):
                    try:
                        plan = gen(ex, key=key, hands=hands, octaves=octaves)
                    except ValueError:
                        refused += 1          # said so, in words, with a 400 behind it
                        continue
                    built += 1
                    off += [n for s in plan.steps for n in s.notes if not lo <= n <= hi]
    step(f"{keys}-key ({lo}..{hi}): nothing off the keyboard", not off,
         f"{built} built, {refused} refused" if not off
         else f"{len(off)} stray notes, e.g. {sorted(set(off))[:6]}")
    step(f"{keys}-key: it still builds most of them", built > refused,
         f"{built} built vs {refused} refused")

config.settings.update({"instrument": {"low": 21, "high": 108}})
_msg, _usable = _refusal()
step("a refusal names the key, the size and a way out", _usable, _msg)

print("14. the real keys.db was never touched")
after = (REAL_DB.exists(), REAL_DB.stat().st_mtime_ns if REAL_DB.exists() else 0,
         REAL_DB.stat().st_size if REAL_DB.exists() else 0)
step("keys.db unchanged", after == REAL_BEFORE,
     f"{REAL_DB} exists={after[0]} (was {REAL_BEFORE[0]})")

STORE.close()
shutil.rmtree(TMP, ignore_errors=True)
print()
print("ALL CHECKS PASSED" if ok else "SOMETHING FAILED")
sys.exit(0 if ok else 1)
