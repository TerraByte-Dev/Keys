"""Explaining a scale or a chord, in the terms you actually think in.

`music.py` already knows what the notes ARE. This module answers the different
question: *how do you find them at the keyboard.* The answer this is built around
is counting the keys between one note and the next -- a major scale is
W W H W W W H, a major chord is four keys then three -- because that is a rule you
can apply at the piano, and "the major scale is C D E F G A B" is a fact you can
only recall.

So every shape here reports its **steps**, the gaps between consecutive notes, and
that is what the UI leads with. Intervals from the root are reported too, but
second: they are how the shape is *named*, not how it is found.

Spelling is done by letter, not by lookup, and that is not a detail. A seven-note
scale uses each letter exactly once, so the letters are forced the moment you know
the tonic, and the accidental is whatever makes that letter land on the right key.
A chord is the same rule skipping a letter each time. Without this, A harmonic
minor comes back with an Ab in it -- the right key, the wrong name, which is worse
than useless in a tool for learning. `music.note_parts` cannot do it because it
spells against one key signature, and these shapes step outside one.

Nothing here is on the hot path. It runs when a dropdown changes.
"""

from __future__ import annotations

from . import music
from .exercises import fingering

LETTERS = "CDEFGAB"
_NATURAL_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_ACC = {-2: "bb", -1: "b", 0: "", 1: "#", 2: "##"}

MIDDLE_C = 60

# What a gap between two notes is called out loud. Anything wider than a tone in a
# scale is the step people trip on -- harmonic minor's sixth-to-seventh, the blues
# scale's root-to-third -- so it gets a word rather than a number.
STEP_WORDS = {1: ("H", "half step"), 2: ("W", "whole step"), 3: ("W+H", "step and a half"),
              4: ("2W", "two whole steps")}

# The four forms fingering.py has rows for. Modes are deliberately absent: the
# common claim that a mode is fingered like its parent major is not true, and a
# fingering this tool invents is one a teacher would have to undo.
FINGERED_FORMS = ("major", "natural_minor", "harmonic_minor", "melodic_minor")

MODE_LABELS = {
    "major": "major", "natural_minor": "natural minor",
    "harmonic_minor": "harmonic minor", "melodic_minor": "melodic minor",
    "dorian": "dorian", "phrygian": "phrygian", "lydian": "lydian",
    "mixolydian": "mixolydian", "locrian": "locrian",
    "major_pentatonic": "major pentatonic", "minor_pentatonic": "minor pentatonic",
    "blues": "blues", "chromatic": "chromatic",
}

# Which scales get one-note-per-letter spelling. The others skip letters by
# construction (a pentatonic is a major scale with two notes taken out), so the
# rule does not apply and they are spelled against a key signature instead.
_SEVEN = {"major", "natural_minor", "harmonic_minor", "melodic_minor", "dorian",
          "phrygian", "lydian", "mixolydian", "locrian"}

# Fifths from the tonic's own major key to the major key a mode borrows its
# signature from: D dorian is spelled out of C, two fifths down.
_PARENT_FIFTHS = {"major": 0, "lydian": 1, "mixolydian": -1, "dorian": -2,
                  "natural_minor": -3, "harmonic_minor": -3, "melodic_minor": -3,
                  "phrygian": -4, "locrian": -5,
                  "major_pentatonic": 0, "minor_pentatonic": -3, "blues": -3,
                  "chromatic": 0}


def _spell(letter: str, pc: int) -> str:
    """Name the pitch class `pc` using `letter`, whatever accidental that takes."""
    alter = (pc - _NATURAL_PC[letter]) % 12
    if alter > 6:
        alter -= 12
    if alter not in _ACC:
        # Triple sharps and flats exist on paper and nowhere else. Falling back to
        # a plain name is a better answer than "G###".
        return music.pitch_class_name(pc, "C")
    return letter + _ACC[alter]


def _by_letter(tonic: str, pcs, skip: int) -> list[str]:
    """Spell a run where each note takes the next letter (skip=1) or every other
    letter (skip=2, how chords stack)."""
    start = LETTERS.index(tonic[0].upper())
    return [_spell(LETTERS[(start + i * skip) % 7], pc) for i, pc in enumerate(pcs)]


def _parent_key(tonic: str, mode: str) -> str:
    """The major key whose signature this shape borrows."""
    canon = tonic[:1].upper() + tonic[1:].lower()
    if canon not in music.KEYS:
        canon = "C"
    i = music.KEYS.index(canon) + _PARENT_FIFTHS.get(mode, 0)
    return music.KEYS[max(0, min(len(music.KEYS) - 1, i))]


def _steps(pcs: list[int], close: bool) -> list[dict]:
    """The gaps between consecutive notes -- the whole point of this module.

    `close` adds the step from the last note back up to the octave, which is what
    makes a scale's formula add to twelve and lets you check your own counting.
    """
    seq = list(pcs) + ([pcs[0] + 12] if close else [])
    out = []
    for a, b in zip(seq, seq[1:]):
        n = (b - a) % 12 or 12
        short, spoken = STEP_WORDS.get(n, (f"{n}", f"{n} half steps"))
        out.append({
            "semitones": n,
            # The literal count of keys you pass over, black and white. This is the
            # number you count at the piano; the semitone count is the number you
            # say afterwards.
            "between": n - 1,
            "short": short,
            "spoken": spoken,
        })
    return out


def _from_root(pcs: list[int]) -> list[dict]:
    out = []
    for i, pc in enumerate(pcs):
        semis = (pc - pcs[0]) % 12
        if i and semis == 0:
            semis = 12
        iv = music.interval_name(0, semis)
        out.append({"semitones": semis, "short": iv["short"], "name": iv["name"]})
    return out


def _voice(pcs: list[int], low: int = MIDDLE_C) -> list[int]:
    """Lay pitch classes out as an ascending run starting at or above `low`."""
    out, n = [], low + ((pcs[0] - low) % 12)
    for pc in pcs:
        while n % 12 != pc:
            n += 1
        out.append(n)
        n += 1
    return out


# --- scales ------------------------------------------------------------------
def scale_plan(key: str, mode: str = "major", octaves: int = 1,
               hand: str = "R") -> dict:
    """Everything the visualiser needs about one scale."""
    if mode not in music.MODES:
        raise ValueError(f"unknown mode {mode!r}")
    pcs = music.scale_pitch_classes(key, mode)
    tonic = key[:1].upper() + key[1:].lower()

    if mode in _SEVEN:
        names = _by_letter(tonic, pcs, skip=1)
    else:
        parent = _parent_key(tonic, mode)
        names = [music.pitch_class_name(pc, parent) for pc in pcs]

    octaves = max(1, min(4, int(octaves)))
    degrees = len(pcs) * octaves + 1          # the closing tonic
    midi = _voice(pcs)
    run = [midi[i % len(pcs)] + 12 * (i // len(pcs)) for i in range(degrees)]

    fingers = ()
    if mode in FINGERED_FORMS:
        fingers = fingering.fingers_for(tonic, mode, hand, degrees)
    crossings = fingering.crossings(fingers) if fingers else ()

    # Names, degrees and steps all run the full length of what you actually play,
    # so the panel never shows eight note names above fifteen fingerings. The
    # formula stays one octave, because the repeat is the point of a formula.
    one = _steps(pcs, close=True)
    run_names = [names[i % len(pcs)] for i in range(degrees)]
    run_degrees = [str(i % len(pcs) + 1) for i in range(degrees)]
    run_degrees[-1] = str(len(pcs) + 1)
    run_steps = [dict(one[i % len(one)]) for i in range(degrees - 1)]

    return {
        "kind": "scale",
        "title": f"{tonic} {MODE_LABELS.get(mode, mode)}",
        "root": tonic,
        "mode": mode,
        "pcs": pcs,
        "names": run_names,
        "midi": run,
        "degrees": run_degrees,
        "steps": run_steps,
        "formula": " ".join(s["short"] for s in one),
        "from_root": _from_root(pcs),
        "fingers": list(fingers),
        "crossings": [bool(c) for c in crossings],
        "hand": hand.upper()[:1] if fingers else "",
        "signature": music.key_signature(_parent_key(tonic, mode)),
        "chords": diatonic(key, mode) if mode in _SEVEN else [],
    }


# --- chords ------------------------------------------------------------------
QUALITIES = [{"suffix": suffix, "name": name, "intervals": list(ivs)}
             for suffix, name, ivs in music._QUALITIES]
_BY_SUFFIX = {q["suffix"]: q for q in QUALITIES}


def chord_plan(root: str, quality: str = "", inversion: int = 0) -> dict:
    """Everything the visualiser needs about one chord.

    `steps` is the stack -- four keys then three is a major triad, three then four
    is minor -- and that pair of numbers is the most portable chord fact there is.
    """
    q = _BY_SUFFIX.get(quality)
    if q is None:
        raise ValueError(f"unknown chord quality {quality!r}")
    tonic = root[:1].upper() + root[1:].lower()
    root_pc = _NATURAL_PC[tonic[0].upper()]
    for ch in tonic[1:]:
        root_pc += 1 if ch == "#" else -1
    root_pc %= 12

    pcs = [(root_pc + iv) % 12 for iv in q["intervals"]]
    names = _by_letter(tonic, pcs, skip=2)

    midi = _voice(pcs)
    from_root = _from_root(pcs)
    inversion = max(0, min(len(midi) - 1, int(inversion)))
    # Names and intervals rotate WITH the notes. Leave them behind and an inversion
    # shows one order on the ribbon and a different one on the keyboard, which is
    # the single most confusing thing a chord diagram can do.
    for _ in range(inversion):
        # Up by as many octaves as it takes to clear the top, not by exactly one.
        # An add9 already voices its ninth an octave up, so a single octave would
        # leave the old bass sitting in the middle of the chord.
        lifted = midi[0]
        while lifted <= midi[-1]:
            lifted += 12
        midi = midi[1:] + [lifted]
        names = names[1:] + [names[0]]
        from_root = from_root[1:] + [from_root[0]]

    # Measured off the voicing rather than off the template, so the numbers on the
    # ribbon are the gaps between the keys under your hand. In root position the
    # two agree; in an inversion only this one is true.
    stack = []
    for a, b in zip(midi, midi[1:]):
        n = b - a
        iv = music.interval_name(0, n)
        stack.append({"semitones": n, "between": n - 1,
                      "short": iv["short"], "name": iv["name"]})

    return {
        "kind": "chord",
        "title": tonic + q["suffix"],
        "spoken": f"{tonic} {q['name']}",
        "root": tonic,
        "quality": quality,
        "pcs": pcs,
        "names": names,
        "midi": midi,
        "steps": stack,
        "formula": " + ".join(str(s["semitones"]) for s in stack),
        "from_root": from_root,
        "inversion": inversion,
        "bass": names[0],
        "keys": [k for k in music.KEYS if set(pcs) <= set(music.scale_pitch_classes(k))],
    }


# --- the chords that live in a key -------------------------------------------
_ROMAN = ("I", "II", "III", "IV", "V", "VI", "VII")


def diatonic(key: str, mode: str = "major", sevenths: bool = False) -> list[dict]:
    """The chords you can build without leaving the scale.

    Stacked in scale steps, not semitones -- that is what makes the qualities come
    out different on each degree, and why a major key gives you three major chords,
    three minor and one diminished rather than seven of anything.
    """
    pcs = music.scale_pitch_classes(key, mode)
    if len(pcs) != 7:
        return []
    tonic = key[:1].upper() + key[1:].lower()
    letters = _by_letter(tonic, pcs, skip=1)

    out = []
    size = 4 if sevenths else 3
    for d in range(7):
        tones = [pcs[(d + 2 * i) % 7] for i in range(size)]
        shape = tuple((t - tones[0]) % 12 for t in tones)
        match = next((q for q in QUALITIES if tuple(q["intervals"]) == shape), None)
        suffix = match["suffix"] if match else "?"
        name = match["name"] if match else "unnamed"

        roman = _ROMAN[d]
        minorish = shape[:2] == (0, 3)
        if minorish:
            roman = roman.lower()
        if shape == (0, 3, 6) or shape[:3] == (0, 3, 6):
            roman += "°"
        elif shape[:3] == (0, 4, 8):
            roman += "+"
        if sevenths and match:
            roman += "7" if suffix in ("7", "m7", "m7b5", "dim7") else ""

        out.append({
            "degree": d + 1,
            "roman": roman,
            "symbol": letters[d] + suffix,
            "root": letters[d],
            "quality": suffix,
            "quality_name": name,
            "pcs": list(tones),
            "midi": _voice(list(tones)),
            "names": _by_letter(letters[d], list(tones), skip=2),
        })
    return out
