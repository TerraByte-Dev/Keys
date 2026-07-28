"""Checks for backend/theory.py -- the scale and chord explainer.

The load-bearing test is the round trip: every chord this module builds is fed
back through `music.detect_chord`, which is a completely separate implementation
that got there by template matching rather than by stacking. If the two ever
disagree, one of them is wrong and this says which chord.

The spelling tests are the ones that would have caught the bug this module exists
to avoid -- A harmonic minor containing an Ab.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import music, theory   # noqa: E402

fails: list[str] = []
count = 0


def ok(cond: bool, label: str, detail: str = "") -> None:
    global count
    count += 1
    if cond:
        print(f"  [PASS] {label}" + (f" -- {detail}" if detail else ""))
    else:
        print(f"  [FAIL] {label}" + (f" -- {detail}" if detail else ""))
        fails.append(label)


print("1. spelling -- one letter per degree")

plan = theory.scale_plan("A", "harmonic_minor")
ok(plan["names"][:7] == ["A", "B", "C", "D", "E", "F", "G#"],
   "A harmonic minor raises the 7th to G#, not Ab", " ".join(plan["names"][:7]))

ok(theory.scale_plan("Gb", "major")["names"][:7]
   == ["Gb", "Ab", "Bb", "Cb", "Db", "Eb", "F"],
   "Gb major keeps its Cb", " ".join(theory.scale_plan("Gb", "major")["names"][:7]))

ok(theory.scale_plan("D", "dorian")["names"][:7]
   == ["D", "E", "F", "G", "A", "B", "C"],
   "D dorian is white notes", " ".join(theory.scale_plan("D", "dorian")["names"][:7]))

# The general rule, over every seven-note scale this app can show: 15 keys x 9
# modes. A repeated letter is the class of bug that produced the Ab above.
bad = []
for key in music.KEYS:
    for mode in theory._SEVEN:
        names = theory.scale_plan(key, mode)["names"][:7]
        letters = [n[0] for n in names]
        if len(set(letters)) != 7:
            bad.append(f"{key} {mode}: {' '.join(names)}")
ok(not bad, f"all {len(music.KEYS) * len(theory._SEVEN)} seven-note scales use each letter once",
   bad[0] if bad else "no repeats, no gaps")


print("\n2. steps -- the counting method")

major = theory.scale_plan("C", "major")
ok(major["formula"] == "W W H W W W H", "C major reads W W H W W W H", major["formula"])
ok([s["semitones"] for s in major["steps"]] == [2, 2, 1, 2, 2, 2, 1],
   "and its steps are 2 2 1 2 2 2 1")
ok([s["between"] for s in major["steps"]] == [1, 1, 0, 1, 1, 1, 0],
   "'between' is the keys you pass over, one less than the semitones")

bad = []
for key in music.KEYS:
    for mode in music.MODES:
        p = theory.scale_plan(key, mode)
        total = sum(s["semitones"] for s in p["steps"])
        if total != 12:
            bad.append(f"{key} {mode} sums to {total}")
ok(not bad, "every scale's steps close the octave -- they sum to 12",
   bad[0] if bad else f"{len(music.KEYS) * len(music.MODES)} scales")

hm = theory.scale_plan("A", "harmonic_minor")
ok(hm["steps"][5]["semitones"] == 3 and hm["steps"][5]["short"] == "W+H",
   "harmonic minor's 6th-to-7th is the step-and-a-half", hm["formula"])


print("\n3. chords -- the stack")

for suffix, want in [("", [4, 3]), ("m", [3, 4]), ("dim", [3, 3]), ("aug", [4, 4]),
                     ("maj7", [4, 3, 4]), ("7", [4, 3, 3]), ("m7", [3, 4, 3])]:
    got = [s["semitones"] for s in theory.chord_plan("C", suffix)["steps"]]
    ok(got == want, f"C{suffix or ' major'} stacks {want}", str(got))

ok(theory.chord_plan("C", "dim7")["names"] == ["C", "Eb", "Gb", "Bbb"],
   "C dim7 spells its 7th Bbb, not A", " ".join(theory.chord_plan("C", "dim7")["names"]))
ok(theory.chord_plan("C", "aug")["names"] == ["C", "E", "G#"],
   "C aug spells G#, not Ab", " ".join(theory.chord_plan("C", "aug")["names"]))

# The round trip. detect_chord is template matching over held notes and knows
# nothing about how these were stacked, so agreement is real evidence.
#
# Root and quality, not the printed symbol: detect_chord spells against a key
# signature and this module spells against the root you asked for, so Db major
# comes back as C# when the key is left at C. Same three keys under the hand,
# different name -- comparing symbols would be testing the speller, and the
# symbol IS compared below where the key makes it well defined.
misses, unspelled = [], []
for root in ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]:
    for q in theory.QUALITIES:
        plan = theory.chord_plan(root, q["suffix"])
        back = music.detect_chord(plan["midi"])
        if back is None or back["quality"] != q["suffix"] or back["root_pc"] != plan["pcs"][0]:
            misses.append(f"{plan['title']} -> {back['symbol'] if back else 'None'}")
        elif music.detect_chord(plan["midi"], root)["symbol"] != plan["title"]:
            unspelled.append(f"{plan['title']} -> "
                             f"{music.detect_chord(plan['midi'], root)['symbol']}")
total = 12 * len(theory.QUALITIES)
ok(not misses, f"all {total} chords round-trip through music.detect_chord "
               f"with the same root and quality",
   f"{len(misses)} disagreed: {misses[:3]}" if misses else f"{total} agreed")
ok(not unspelled, "and the same printed symbol once detect_chord is told the key",
   f"{len(unspelled)} differed: {unspelled[:3]}" if unspelled else f"{total} agreed")


print("\n4. inversions")

first = theory.chord_plan("C", "", inversion=1)
ok([n % 12 for n in first["midi"]] == [4, 7, 0], "first inversion of C puts E at the bottom",
   str(first["midi"]))
ok(first["bass"] == "E", "and says so", first["bass"])
ok(first["midi"] == sorted(first["midi"]), "inversions stay ascending", str(first["midi"]))
ok(theory.chord_plan("C", "", inversion=9)["inversion"] == 2,
   "an out-of-range inversion clamps rather than raising")

# The ribbon has to describe the hand, not the template. If names stayed in
# stacking order while the notes rotated, the panel would print C E G over a
# keyboard lighting E G C.
ok(first["names"] == ["E", "G", "C"], "an inversion rotates its names with its notes",
   " ".join(first["names"]))
ok([s["semitones"] for s in first["steps"]] == [3, 5],
   "and reports the gaps of the voicing (3 then 5), not the stack (4 then 3)",
   str([s["semitones"] for s in first["steps"]]))
ok([f["short"] for f in first["from_root"]] == ["M3", "P5", "P1"],
   "while the intervals stay measured from the root",
   " ".join(f["short"] for f in first["from_root"]))

bad = []
for root in ["C", "Eb", "F#" if "F#" in music.KEYS else "Gb", "A"]:
    for q in theory.QUALITIES:
        for inv in range(min(4, len(q["intervals"]))):
            p = theory.chord_plan(root, q["suffix"], inversion=inv)
            if p["midi"] != sorted(p["midi"]):
                bad.append(f"{p['title']} inv{inv} not ascending")
            if len(p["names"]) != len(p["midi"]) or len(p["steps"]) != len(p["midi"]) - 1:
                bad.append(f"{p['title']} inv{inv} ragged")
            if [n % 12 for n in p["midi"]] != p["pcs"][inv:] + p["pcs"][:inv]:
                bad.append(f"{p['title']} inv{inv} wrong rotation")
ok(not bad, "every inversion of every quality stays ascending, square and correctly rotated",
   bad[0] if bad else f"{4 * len(theory.QUALITIES)} chords x up to 4 inversions")


print("\n5. the chords in a key")

got = [c["roman"] for c in theory.diatonic("C", "major")]
ok(got == ["I", "ii", "iii", "IV", "V", "vi", "vii°"],
   "C major gives I ii iii IV V vi vii", " ".join(got))
got = [c["symbol"] for c in theory.diatonic("C", "major")]
ok(got == ["C", "Dm", "Em", "F", "G", "Am", "Bdim"], "with the right symbols", " ".join(got))

got = [c["roman"] for c in theory.diatonic("A", "natural_minor")]
ok(got == ["i", "ii°", "III", "iv", "v", "VI", "VII"],
   "A minor gives i ii III iv v VI VII", " ".join(got))

got = [c["symbol"] for c in theory.diatonic("C", "major", sevenths=True)]
ok(got == ["Cmaj7", "Dm7", "Em7", "Fmaj7", "G7", "Am7", "Bm7b5"],
   "and the sevenths put the only dominant on V", " ".join(got))

ok(theory.diatonic("C", "major_pentatonic") == [],
   "a five-note scale has no diatonic triads, and says so rather than inventing them")

# Every diatonic chord must actually be in the scale -- the whole claim.
bad = []
for key in music.KEYS:
    for mode in ("major", "natural_minor", "dorian", "mixolydian"):
        scale = set(music.scale_pitch_classes(key, mode))
        for c in theory.diatonic(key, mode, sevenths=True):
            if not set(c["pcs"]) <= scale:
                bad.append(f"{key} {mode} {c['symbol']}")
ok(not bad, "no diatonic chord uses a note outside its scale", bad[0] if bad else "60 keys x 4 modes")


print("\n6. fingering")

c = theory.scale_plan("C", "major", hand="R")
ok(c["fingers"] == [1, 2, 3, 1, 2, 3, 4, 5], "C major RH is 1 2 3 1 2 3 4 5", str(c["fingers"]))
ok(c["crossings"][3] is True, "and flags the thumb-under at the 4th note")
ok(theory.scale_plan("C", "major", hand="L")["fingers"] == [5, 4, 3, 2, 1, 3, 2, 1],
   "C major LH is 5 4 3 2 1 3 2 1")
ok(theory.scale_plan("D", "dorian")["fingers"] == [],
   "a mode reports no fingering rather than guessing one")
two = theory.scale_plan("C", "major", octaves=2)
ok(len(two["fingers"]) == 15, "two octaves is 15 notes of fingering",
   str(len(two["fingers"])))
ok(len(two["names"]) == 15 and len(two["steps"]) == 14 and len(two["midi"]) == 15,
   "and 15 names over 14 gaps, so the ribbon and the fingering cannot drift apart",
   f"{len(two['names'])} names, {len(two['steps'])} gaps")
ok(two["formula"] == "W W H W W W H",
   "while the formula stays one octave -- the repeat is the point of a formula",
   two["formula"])
ok(two["names"][:3] == ["C", "D", "E"] and two["names"][-1] == "C"
   and two["degrees"][-1] == "8",
   "the run closes on the tonic and calls it the 8th",
   " ".join(two["names"]))


print("\n7. it does not raise on anything the UI can ask for")

n = 0
for key in music.KEYS:
    for mode in music.MODES:
        for octs in (1, 2):
            p = theory.scale_plan(key, mode, octaves=octs)
            assert p["midi"] == sorted(p["midi"]), f"{key} {mode} not ascending"
            # Every row of the panel is the same length or the ribbon cannot line
            # its fingering up with its notes.
            assert len(p["names"]) == len(p["midi"]), f"{key} {mode} names/midi"
            assert len(p["degrees"]) == len(p["midi"]), f"{key} {mode} degrees/midi"
            assert len(p["steps"]) == len(p["midi"]) - 1, f"{key} {mode} steps/midi"
            if p["fingers"]:
                assert len(p["fingers"]) == len(p["midi"]), f"{key} {mode} fingers/midi"
            n += 1
for root in music.KEYS:
    for q in theory.QUALITIES:
        for inv in range(3):
            theory.chord_plan(root, q["suffix"], inversion=inv)
            n += 1
ok(True, f"{n} scale and chord requests, none raised, every run ascending")

try:
    theory.scale_plan("C", "nonsense")
    ok(False, "an unknown mode raises")
except ValueError:
    ok(True, "an unknown mode raises ValueError rather than returning nonsense")
try:
    theory.chord_plan("C", "nonsense")
    ok(False, "an unknown quality raises")
except ValueError:
    ok(True, "an unknown quality raises ValueError")


print()
if fails:
    print(f"  {len(fails)} of {count} FAILED:")
    for f in fails:
        print(f"    - {f}")
    raise SystemExit(1)
print(f"  {count} assertions")
print("ALL CHECKS PASSED")
