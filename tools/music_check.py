"""Regression test for the music theory layer: spelling, intervals, chords, scales.

Pure arithmetic -- no piano, no FluidSynth, no sound. If this passes, anything the
UI shows about what you just played is the theory layer's fault or nobody's.

    .venv\\Scripts\\python.exe tools\\music_check.py

The timing section matters as much as the answers: detect_chord runs on every note
change inside the websocket drain loop, so a slow reading is a broken one.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import music  # noqa: E402
from backend.music import (  # noqa: E402
    KEYS, KRUMHANSL_MAJOR, KRUMHANSL_MINOR, MODES, detect_chord, in_scale,
    infer_key, interval_name, key_signature, note_name, note_parts,
    pitch_class_name, scale_degree, scale_fit, scale_pitch_classes,
)

ok = True


def step(label: str, passed: bool, detail: str = "") -> None:
    global ok
    ok = ok and passed
    print(f"  [{'PASS' if passed else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))


def sym(notes: list[int], key: str = "C") -> str:
    got = detect_chord(notes, key)
    return got["symbol"] if got else "None"


print("1. scientific pitch notation")
step("middle C is C4", note_name(60) == "C4", note_name(60))
step("bottom key is A0", note_name(21) == "A0", note_name(21))
step("top key is C8", note_name(108) == "C8", note_name(108))
step("note_parts shape", note_parts(61) == {"midi": 61, "letter": "C", "accidental": "#",
                                            "octave": 4, "name": "C#4", "pc": 1},
     str(note_parts(61)))

print("2. enharmonic spelling follows the key")
step("midi 63 in Eb is Eb4", note_name(63, "Eb") == "Eb4", note_name(63, "Eb"))
step("midi 63 in E is D#4", note_name(63, "E") == "D#4", note_name(63, "E"))
step("midi 66 in Gb is Gb4", note_name(66, "Gb") == "Gb4", note_name(66, "Gb"))
step("midi 66 in D is F#4", note_name(66, "D") == "F#4", note_name(66, "D"))
# The octave has to come from the spelling, not from midi // 12: B# and Cb sit on
# the far side of the octave boundary from the key they sound like.
step("leading tone of C# is B#3", note_name(60, "C#") == "B#3", note_name(60, "C#"))
step("tonic of Cb is Cb4", note_name(59, "Cb") == "Cb4", note_name(59, "Cb"))
step("pitch_class_name drops the octave", pitch_class_name(1, "Db") == "Db",
     pitch_class_name(1, "Db"))
step("every key spells all 12 pitch classes", all(
    len({pitch_class_name(pc, k) for pc in range(12)}) == 12 for k in KEYS))
# A key's own scale must be spelled with seven different letters -- one D# and one
# Eb in the same scale is the classic wrong-table bug.
step("no key repeats a letter in its own scale", all(
    len({pitch_class_name(pc, k)[0] for pc in scale_pitch_classes(k, "major")}) == 7
    for k in KEYS))

print("3. key signatures")
step("F# major has 6 sharps", key_signature("F#")["sharps"] == 6,
     str(key_signature("F#")["accidentals"]))
step("Cb major has 7 flats", key_signature("Cb")["flats"] == 7,
     str(key_signature("Cb")["accidentals"]))
step("C major has none", key_signature("C")["sharps"] == 0
     and key_signature("C")["flats"] == 0 and not key_signature("C")["uses_flats"])
step("sharps appear in staff order", key_signature("D")["accidentals"] == ["F#", "C#"],
     str(key_signature("D")["accidentals"]))
step("flats appear in staff order", key_signature("Eb")["accidentals"] == ["Bb", "Eb", "Ab"],
     str(key_signature("Eb")["accidentals"]))
step("uses_flats tracks the flat side", [key_signature(k)["uses_flats"] for k in KEYS]
     == [True] * 7 + [False] * 8)
step("signature count matches the circle", all(
    key_signature(k)["sharps"] + key_signature(k)["flats"] == abs(i - 7)
    for i, k in enumerate(KEYS)))

print("4. intervals")
step("P5", interval_name(60, 67)["short"] == "P5", interval_name(60, 67)["name"])
step("M9 is compound", interval_name(60, 74)["short"] == "M9"
     and interval_name(60, 74)["compound"], str(interval_name(60, 74)))
step("unison is P1", interval_name(60, 60)["short"] == "P1", interval_name(60, 60)["name"])
step("octave is P8, not compound", interval_name(60, 72)["short"] == "P8"
     and not interval_name(60, 72)["compound"], interval_name(60, 72)["name"])
step("m3 / M3", interval_name(60, 63)["short"] == "m3"
     and interval_name(60, 64)["short"] == "M3")
step("m7 / M7", interval_name(60, 70)["short"] == "m7"
     and interval_name(60, 71)["short"] == "M7")
step("tritone", interval_name(60, 66)["short"] == "TT", interval_name(60, 66)["name"])
step("direction does not matter", interval_name(72, 60) == interval_name(60, 72))
step("two octaves is P15", interval_name(60, 84)["short"] == "P15",
     interval_name(60, 84)["name"])
step("semitones is the raw distance", all(
     interval_name(60, 60 + n)["semitones"] == n for n in range(0, 48)))

print("5. chords -- triads")
step("C", sym([60, 64, 67]) == "C", sym([60, 64, 67]))
step("first inversion is C/E", sym([64, 67, 72]) == "C/E", sym([64, 67, 72]))
step("second inversion is C/G", sym([67, 72, 76]) == "C/G", sym([67, 72, 76]))
step("doubled octave is still C", sym([48, 52, 55, 60]) == "C", sym([48, 52, 55, 60]))
step("Cm", sym([60, 63, 67]) == "Cm", sym([60, 63, 67]))
step("Cdim", sym([60, 63, 66]) == "Cdim", sym([60, 63, 66]))
step("Caug", sym([60, 64, 68]) == "Caug", sym([60, 64, 68]))
step("Csus2", sym([60, 62, 67]) == "Csus2", sym([60, 62, 67]))
step("Csus4", sym([60, 65, 67]) == "Csus4", sym([60, 65, 67]))
step("two pitch classes is not a chord", detect_chord([60, 64]) is None)
step("an octave of one note is not a chord", detect_chord([48, 60, 72]) is None)

print("6. chords -- sevenths and sixths")
step("Cmaj7", sym([60, 64, 67, 71]) == "Cmaj7", sym([60, 64, 67, 71]))
step("C7", sym([60, 64, 67, 70]) == "C7", sym([60, 64, 67, 70]))
step("Cm7", sym([60, 63, 67, 70]) == "Cm7", sym([60, 63, 67, 70]))
step("Cm(maj7)", sym([60, 63, 67, 71]) == "Cm(maj7)", sym([60, 63, 67, 71]))
dim7 = detect_chord([60, 63, 66, 69])
step("dim7 recognised", dim7["quality"] == "dim7", f"{dim7['symbol']} conf={dim7['confidence']}")
half = detect_chord([60, 63, 66, 70])
step("half-diminished recognised", half["quality"] == "m7b5",
     f"{half['symbol']} -- {half['name']}")
step("C7sus4", sym([60, 65, 67, 70]) == "C7sus4", sym([60, 65, 67, 70]))
step("C6", sym([60, 64, 67, 69]) == "C6", sym([60, 64, 67, 69]))
step("Cm6", sym([60, 63, 67, 69]) == "Cm6", sym([60, 63, 67, 69]))
# C6 and Am7 are the same four pitch classes. The bass decides, and nothing else can.
step("same notes, A on the bottom, is Am7", sym([57, 60, 64, 67]) == "Am7",
     sym([57, 60, 64, 67]))
step("Cmaj7 over E", sym([64, 67, 71, 72]) == "Cmaj7/E", sym([64, 67, 71, 72]))
step("G7 over B", sym([59, 62, 65, 67]) == "G7/B", sym([59, 62, 65, 67]))
inv = detect_chord([64, 67, 71, 72])
step("inversion counted from the stack", inv["inversion"] == 1 and inv["bass_pc"] == 4
     and inv["root_pc"] == 0, str({k: inv[k] for k in ("inversion", "root_pc", "bass_pc")}))
step("Cmaj7/E confidence is docked for the inversion", inv["confidence"] == 0.92,
     str(inv["confidence"]))

print("7. chords -- extensions do not crash and keep a sane root")
for label, notes, want_root in (
    ("Cadd9", [60, 62, 64, 67], 0),
    ("C9", [60, 64, 67, 70, 62], 0),
    ("Cmaj9", [60, 64, 67, 71, 62], 0),
    ("Cm9", [60, 63, 67, 70, 62], 0),
    ("C11", [60, 64, 67, 70, 62, 65], 0),
    ("C13", [60, 64, 67, 70, 62, 65, 69], 0),
    ("C69", [60, 64, 67, 69, 62], 0),
    # C E Bb D A -- a 13 voicing with no 5th and no 11th. No template covers it
    # without assuming two notes, so the honest read is C9 with the 13th left over.
    ("13 voicing, no 5th or 11th", [36, 64, 70, 74, 81], 0),
    ("chromatic cluster", [60, 61, 62], None),
):
    got = detect_chord(notes)
    if want_root is None:
        step(f"{label} -> no chord", got is None, str(got))
    else:
        step(f"{label} -> root {want_root}", got is not None and got["root_pc"] == want_root,
             f"{got['symbol']} conf={got['confidence']} extra={got['extra']}" if got else "None")

print("8. chords -- spelling follows the key")
step("Eb major triad in Eb", sym([63, 67, 70], "Eb") == "Eb", sym([63, 67, 70], "Eb"))
step("the same triad in E is D#", sym([63, 67, 70], "E") == "D#", sym([63, 67, 70], "E"))
step("slash bass is spelled too", sym([70, 75, 79], "Eb") == "Eb/Bb", sym([70, 75, 79], "Eb"))
step("extra lists what the quality cannot explain",
     detect_chord([60, 64, 67, 61])["extra"] == [1],
     str(detect_chord([60, 64, 67, 61])))
step("a clean triad is fully confident", detect_chord([60, 64, 67])["confidence"] == 1.0)
step("leftovers lower confidence",
     detect_chord([60, 64, 67, 61])["confidence"] < detect_chord([60, 64, 67])["confidence"])
step("a symmetric chord is less confident than an unambiguous one",
     dim7["confidence"] < detect_chord([60, 64, 67, 71])["confidence"],
     f"dim7={dim7['confidence']} maj7={detect_chord([60, 64, 67, 71])['confidence']}")

print("9. scales")
step("C major", scale_pitch_classes("C") == [0, 2, 4, 5, 7, 9, 11], str(scale_pitch_classes("C")))
step("A natural minor", scale_pitch_classes("A", "natural_minor") == [9, 11, 0, 2, 4, 5, 7],
     str(scale_pitch_classes("A", "natural_minor")))
step("C harmonic minor raises the 7th",
     scale_pitch_classes("C", "harmonic_minor") == [0, 2, 3, 5, 7, 8, 11],
     str(scale_pitch_classes("C", "harmonic_minor")))
step("C blues", scale_pitch_classes("C", "blues") == [0, 3, 5, 6, 7, 10],
     str(scale_pitch_classes("C", "blues")))
step("chromatic has all 12", len(set(scale_pitch_classes("F#", "chromatic"))) == 12)
step("all 13 modes present", len(MODES) == 13, ", ".join(sorted(MODES)))
step("F# in C major is out", not in_scale(66, "C") and in_scale(65, "C"))
step("degree of A in C major is 6", scale_degree(69, "C") == 6, str(scale_degree(69, "C")))
step("degree of C in A minor is 3", scale_degree(60, "A", "natural_minor") == 3,
     str(scale_degree(60, "A", "natural_minor")))
step("out-of-scale notes have no degree", scale_degree(61, "C") is None)

bad = []
for k in KEYS:
    for mode in MODES:
        pcs = scale_pitch_classes(k, mode)
        for i, pc in enumerate(pcs):
            midi = pc + 60
            if scale_degree(midi, k, mode) != i + 1 or not in_scale(midi, k, mode):
                bad.append(f"{k} {mode} degree {i + 1}")
        for pc in range(12):
            if (pc in pcs) != in_scale(pc + 60, k, mode):
                bad.append(f"{k} {mode} pc {pc}")
step("degree round trip over 15 keys x 13 modes", not bad,
     f"{len(KEYS) * len(MODES)} scales checked" if not bad else "; ".join(bad[:4]))

print("10. bad input is rejected loudly, not silently")
for call, label in ((lambda: note_name(60, "H"), "unknown key"),
                    (lambda: scale_pitch_classes("C", "bebop"), "unknown mode")):
    try:
        call()
        step(f"{label} raises", False, "no exception")
    except ValueError as exc:
        step(f"{label} raises", True, str(exc)[:60])
step("case is forgiven", note_name(63, "eb") == "Eb4", note_name(63, "eb"))

print("11. timing -- this runs on every note change at ~60 Hz")
voicing = [48, 52, 59, 62, 67]  # C E B D G, a real five-note Cmaj9 voicing
step("five-note voicing reads as Cmaj9", sym(voicing) == "Cmaj9", sym(voicing))
t0 = time.perf_counter()
for _ in range(2000):
    detect_chord(voicing)
elapsed = time.perf_counter() - t0
step("2000 detect_chord calls under 0.4 s", elapsed < 0.4,
     f"{elapsed:.3f} s total, {elapsed / 2000 * 1e6:.1f} us per call")

ten = [36, 48, 55, 60, 64, 67, 70, 74, 77, 81]  # both hands down, ten keys
t0 = time.perf_counter()
for _ in range(2000):
    detect_chord(ten)
wide = time.perf_counter() - t0
step("ten notes stays under the 200 us budget", wide / 2000 < 200e-6,
     f"{wide / 2000 * 1e6:.1f} us per call -> {sym(ten)}")

t0 = time.perf_counter()
for _ in range(2000):
    note_name(60, "Eb")
spell = time.perf_counter() - t0
step("note_name is a table lookup", spell < 0.05, f"{spell / 2000 * 1e6:.2f} us per call")
step("no per-call table building", len(music._SPELLING) == 15  # noqa: SLF001
     and len(music._BY_ROOT) == 12, "spelling + chord tables built at import")  # noqa: SLF001

print("12. key inference -- what did I actually play in?")


def hist(weights: dict[int, float]) -> list[float]:
    """A 12-entry pitch-class histogram from {pc: count}, index 0 = C."""
    return [weights.get(pc, 0) for pc in range(12)]


def scale_hist(key: str, mode: str = "major") -> list[float]:
    return hist({pc: 1 for pc in scale_pitch_classes(key, mode)})


step("published Krumhansl-Kessler profiles", len(KRUMHANSL_MAJOR) == 12
     and len(KRUMHANSL_MINOR) == 12 and KRUMHANSL_MAJOR[0] == 6.35
     and KRUMHANSL_MINOR[3] == 5.38,
     f"major tonic {KRUMHANSL_MAJOR[0]}, minor third {KRUMHANSL_MINOR[3]}")

c_major = scale_hist("C")
ranked = infer_key(c_major)
step("the C major scale infers C major first", ranked[0]["name"] == "C major",
     f"{ranked[0]['name']} score={ranked[0]['score']} share={ranked[0]['share']}")

# A natural minor is the same seven pitch classes as C major, so this is byte for
# byte the same histogram as the line above. Nothing in a pitch-class count can
# separate a key from its relative -- only the profile weighting leans, and it
# leans the other way here. Asserting first place would be a claim about data this
# function never sees, so the honest assertion is membership in the top two.
a_minor = scale_hist("A", "natural_minor")
step("relative pair is literally the same input", a_minor == c_major, str(a_minor))
top2 = [r["name"] for r in infer_key(a_minor)[:2]]
step("A natural minor lands in the top 2", "A minor" in top2, " / ".join(top2))

# Weighted the way a real session in F# would be: tonic heaviest, then dominant
# and supertonic, the rest of the scale trailing off.
sharp = hist({6: 40, 1: 30, 8: 25, 11: 14, 3: 12, 10: 10, 5: 8})
sharp_top = infer_key(sharp)[0]
step("a sharp-weighted histogram is not C major", sharp_top["name"] != "C major",
     f"{sharp_top['name']} score={sharp_top['score']}")
step("it reads as a sharp key", sharp_top["key"] == "F#" and sharp_top["mode"] == "major",
     str([r["name"] for r in infer_key(sharp, 3)]))
full = [r["name"] for r in infer_key(sharp, top=24)]
step("C major is dead last of the 24", full[-1] == "C major",
     f"rank {full.index('C major') + 1} of {len(full)}")

step("all zeros returns []", infer_key([0] * 12) == [], str(infer_key([0] * 12)))
step("an empty histogram returns []", infer_key([]) == [])
step("a flat histogram has no key either", infer_key([7] * 12) == [],
     "correlation against a constant is undefined, not zero")

scores = [r["score"] for r in ranked]
shares = [r["share"] for r in ranked]
step("default top is 5", len(ranked) == 5, str([r["name"] for r in ranked]))
step("sorted by score, descending", scores == sorted(scores, reverse=True), str(scores))
step("scores are clamped to 0..1", all(0.0 <= s <= 1.0 for s in scores))
step("shares sum to ~1.0", abs(sum(shares) - 1.0) < 0.01, f"sum={sum(shares):.3f}")
three = infer_key(c_major, top=3)
step("top=3 returns 3 and re-splits the shares", len(three) == 3
     and abs(sum(r["share"] for r in three) - 1.0) < 0.01,
     f"sum={sum(r['share'] for r in three):.3f}")
step("top cannot exceed the 24 keys", len(infer_key(c_major, top=99)) == 24)
step("result shape", set(ranked[0]) == {"key", "mode", "name", "score", "share"}
     and ranked[0]["name"] == f"{ranked[0]['key']} {ranked[0]['mode']}", str(ranked[0]))

print("13. scale_fit -- how much of it stayed inside the scale")
pure = scale_fit(hist({0: 200, 2: 150, 4: 140, 5: 90, 7: 130, 9: 60, 11: 40}), "C")
step("pure C major input is a perfect fit",
     pure["fraction"] == 1.0 and pure["out_of_scale"] == 0, str(pure))
step("strongest degree is the tonic", pure["strongest_degree"] == 1)
step("nothing missing", pure["missing_degrees"] == [])

gapped = scale_fit(hist({0: 300, 1: 40, 2: 150, 4: 140, 5: 90,
                         6: 30, 7: 90, 8: 26, 11: 42}), "C")
step("in/out split and fraction", gapped == {"in_scale": 812, "out_of_scale": 96,
                                             "fraction": 0.894, "missing_degrees": [6],
                                             "strongest_degree": 1}, str(gapped))
step("it names the degree you never play", gapped["missing_degrees"] == [6],
     f"degree 6 of C major is {pitch_class_name(9, 'C')}")

minor_fit = scale_fit(hist({9: 10, 11: 5, 0: 8, 2: 6, 4: 7, 5: 3, 7: 4, 6: 2}),
                      "A", "natural_minor")
step("modes other than major work", minor_fit["in_scale"] == 43
     and minor_fit["out_of_scale"] == 2 and minor_fit["fraction"] == 0.956, str(minor_fit))

empty_fit = scale_fit([], "C")
step("an empty histogram does not divide by zero", empty_fit["fraction"] == 0.0
     and empty_fit["strongest_degree"] is None
     and empty_fit["missing_degrees"] == [1, 2, 3, 4, 5, 6, 7], str(empty_fit))
try:
    scale_fit(c_major, "H")
    step("scale_fit rejects an unknown key", False, "no exception")
except ValueError as exc:
    step("scale_fit rejects an unknown key", True, str(exc)[:40])

# infer_key runs once when a stats page is built, not per note, so it has three
# orders of magnitude more headroom than detect_chord. 5 ms is still the bar.
session = hist({0: 320, 2: 180, 4: 210, 5: 120, 7: 240, 9: 130, 11: 90, 10: 25, 6: 12})
t0 = time.perf_counter()
for _ in range(200):
    infer_key(session)
per_call = (time.perf_counter() - t0) / 200
step("infer_key is comfortably under 5 ms", per_call < 5e-3,
     f"{per_call * 1e3:.3f} ms per call -- runs on page load, not per note")
step("a weighted session picks its tonic cleanly",
     infer_key(session)[0]["name"] == "C major",
     f"{infer_key(session)[0]['name']} score={infer_key(session)[0]['score']}")

print()
print("ALL CHECKS PASSED" if ok else "SOMETHING FAILED")
sys.exit(0 if ok else 1)
