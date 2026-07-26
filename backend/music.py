"""Note names, intervals, chords and scales. Pure functions, no state, no I/O.

Why this module is shaped the way it is:

* It runs inside the websocket drain loop, roughly 60 times a second, and
  ``detect_chord`` runs again on every note change. So every table it needs is
  built once at import, and the hot function is a loop over precomputed 12-bit
  masks -- integer AND and ``bit_count`` -- with no allocation beyond the answer.
* **Spelling is derived, not tabulated.** The same key on the piano is Eb in Eb
  major and D# in E major, and a practice app that shows the wrong one is
  teaching the wrong thing. Every note name has a position on the line of fifths
  (... Bb F C G D A E B F# ...), every key has a centre on that same line, and
  the right spelling of a pitch class is just the name nearest that centre. That
  one rule reproduces the conventional chromatic spelling of all 15 keys,
  including B# as the leading tone of C# major, without a single special case.
* **Chord detection is a set-cover search, not a pattern list.** The root is
  whichever note best explains what is actually being held, so inversions and
  slash chords fall out of the same loop instead of needing their own tables.

Octaves are scientific pitch notation throughout: middle C is C4, midi 60.
"""

from __future__ import annotations

# --- the line of fifths ------------------------------------------------------
# Index 0 is C, +1 per fifth up (G, D, A ...), -1 per fifth down (F, Bb, Eb ...).
# Fb..B# is exactly the span the 15 practical keys need; anything outside it is a
# double accidental, which is never the friendlier answer on a practice display.
_LOF_LO, _LOF_HI = -8, 12

_LETTERS = "FCGDAEB"
_LETTER_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_ACCIDENTAL_STR = {-2: "bb", -1: "b", 0: "", 1: "#", 2: "##"}

KEYS: list[str] = ["Cb", "Gb", "Db", "Ab", "Eb", "Bb", "F", "C",
                   "G", "D", "A", "E", "B", "F#", "C#"]

# KEYS is the line of fifths from -7 to +7, which is also the signature count.
_KEY_INDEX: dict[str, int] = {name: i - 7 for i, name in enumerate(KEYS)}

_SHARP_ORDER = ["F#", "C#", "G#", "D#", "A#", "E#", "B#"]
_FLAT_ORDER = ["Bb", "Eb", "Ab", "Db", "Gb", "Cb", "Fb"]


def _lof_spelling(n: int) -> tuple[str, str, int]:
    """(letter, accidental, semitone alteration) for line-of-fifths position n."""
    idx = n + 1  # shift so that F (the flat end of the natural run) sits at 0
    alter = idx // 7  # floors toward negative, which is what the flat side needs
    return _LETTERS[idx % 7], _ACCIDENTAL_STR[alter], alter


def _build_spelling(k: int) -> tuple[tuple[str, str, int], ...]:
    # The seven diatonic notes of key k occupy positions k-1 .. k+5, so k+2 is the
    # centre of the key. Spelling a pitch class is then just "closest name to the
    # centre", and ties break toward the flat side (Ab, not G#, in C major).
    centre = k + 2
    out = []
    for pc in range(12):
        n0 = (7 * pc) % 12  # 7 is its own inverse mod 12, so this inverts pc = 7n
        best = min((n for n in (n0 - 12, n0, n0 + 12) if _LOF_LO <= n <= _LOF_HI),
                   key=lambda n: (abs(n - centre), n))
        out.append(_lof_spelling(best))
    return tuple(out)


_SPELLING: dict[str, tuple[tuple[str, str, int], ...]] = {
    name: _build_spelling(k) for name, k in _KEY_INDEX.items()
}
_TONIC_PC: dict[str, int] = {name: (7 * k) % 12 for name, k in _KEY_INDEX.items()}


def _key_name(key: str) -> str:
    """Canonical key name, or ValueError. The fast path is a plain dict hit."""
    if key in _SPELLING:
        return key
    fixed = key.strip()
    fixed = fixed[:1].upper() + fixed[1:].lower()
    if fixed in _SPELLING:
        return fixed
    raise ValueError(f"unknown key {key!r}; expected one of {', '.join(KEYS)}")


# --- key signatures ----------------------------------------------------------
def key_signature(key: str) -> dict:
    """Accidentals in the order they are written on the staff."""
    k = _KEY_INDEX[_key_name(key)]
    sharps = max(0, k)
    flats = max(0, -k)
    return {
        "key": _key_name(key),
        "sharps": sharps,
        "flats": flats,
        "accidentals": _SHARP_ORDER[:sharps] if sharps else _FLAT_ORDER[:flats],
        "uses_flats": flats > 0,
    }


# --- note names --------------------------------------------------------------
def note_parts(midi: int, key: str = "C") -> dict:
    """Spell one midi note in one key.

    The octave is derived from the *spelling*, not from midi // 12, because the
    octave number rolls over at C: midi 60 is C4, but spelled as the leading tone
    of C# major the same key is B#3.
    """
    pc = midi % 12
    letter, accidental, alter = _SPELLING[_key_name(key)][pc]
    octave = (midi - alter - _LETTER_PC[letter]) // 12 - 1
    return {
        "midi": midi,
        "letter": letter,
        "accidental": accidental,
        "octave": octave,
        "name": f"{letter}{accidental}{octave}",
        "pc": pc,
    }


def note_name(midi: int, key: str = "C") -> str:
    letter, accidental, alter = _SPELLING[_key_name(key)][midi % 12]
    octave = (midi - alter - _LETTER_PC[letter]) // 12 - 1
    return f"{letter}{accidental}{octave}"


def pitch_class_name(pc: int, key: str = "C") -> str:
    letter, accidental, _alter = _SPELLING[_key_name(key)][pc % 12]
    return letter + accidental


# --- intervals ---------------------------------------------------------------
# (degree number, quality letter) for each semitone count inside one octave.
_SIMPLE_INTERVALS: tuple[tuple[int, str], ...] = (
    (1, "P"), (2, "m"), (2, "M"), (3, "m"), (3, "M"), (4, "P"),
    (4, "A"), (5, "P"), (6, "m"), (6, "M"), (7, "m"), (7, "M"),
)
_QUALITY_WORDS = {"P": "perfect", "M": "major", "m": "minor",
                  "A": "augmented", "d": "diminished"}
_ORDINALS = {
    1: "unison", 2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth",
    7: "seventh", 8: "octave", 9: "ninth", 10: "tenth", 11: "eleventh",
    12: "twelfth", 13: "thirteenth", 14: "fourteenth", 15: "fifteenth",
}


def interval_name(low: int, high: int) -> dict:
    """Name the distance between two notes. Order does not matter.

    Six semitones is reported as a plain tritone rather than A4/d5, because
    without a spelling for both notes there is no way to tell those apart and
    guessing one would be a lie. Compound tritones keep the augmented name,
    where the #11 reading is the only one anybody uses.
    """
    semitones = abs(int(high) - int(low))
    step = semitones % 12
    octaves = semitones // 12
    if step == 0 and octaves:
        degree, quality = 1 + 7 * octaves, "P"
    else:
        base_degree, quality = _SIMPLE_INTERVALS[step]
        degree = base_degree + 7 * octaves
    if semitones == 6:
        short, name = "TT", "tritone"
    else:
        short = f"{quality}{degree}"
        name = f"{_QUALITY_WORDS[quality]} {_ORDINALS.get(degree, str(degree) + 'th')}"
    return {
        "semitones": semitones,
        "short": short,
        "name": name,
        "compound": semitones > 12,
    }


# --- chords ------------------------------------------------------------------
# (symbol suffix, spoken name, intervals in stacking order). Stacking order --
# root, 3rd, 5th, 7th, 9th, 11th, 13th -- not ascending order, because the
# inversion number is the index of the bass note in exactly that sequence.
#
# The order of this tuple is also the tie-break order: when two readings explain
# the held notes equally well and neither is in the bass, the earlier one wins.
_QUALITIES: tuple[tuple[str, str, tuple[int, ...]], ...] = (
    ("", "major", (0, 4, 7)),
    ("m", "minor", (0, 3, 7)),
    ("dim", "diminished", (0, 3, 6)),
    ("aug", "augmented", (0, 4, 8)),
    ("sus4", "suspended fourth", (0, 5, 7)),
    ("sus2", "suspended second", (0, 2, 7)),
    ("maj7", "major seventh", (0, 4, 7, 11)),
    ("7", "dominant seventh", (0, 4, 7, 10)),
    ("m7", "minor seventh", (0, 3, 7, 10)),
    ("m7b5", "half-diminished seventh", (0, 3, 6, 10)),
    ("dim7", "diminished seventh", (0, 3, 6, 9)),
    ("m(maj7)", "minor major seventh", (0, 3, 7, 11)),
    ("7sus4", "dominant seventh suspended fourth", (0, 5, 7, 10)),
    ("6", "major sixth", (0, 4, 7, 9)),
    ("m6", "minor sixth", (0, 3, 7, 9)),
    ("add9", "added ninth", (0, 4, 7, 2)),
    ("madd9", "minor added ninth", (0, 3, 7, 2)),
    ("9", "dominant ninth", (0, 4, 7, 10, 2)),
    ("maj9", "major ninth", (0, 4, 7, 11, 2)),
    ("m9", "minor ninth", (0, 3, 7, 10, 2)),
    ("69", "six nine", (0, 4, 7, 9, 2)),
    ("11", "dominant eleventh", (0, 4, 7, 10, 2, 5)),
    ("m11", "minor eleventh", (0, 3, 7, 10, 2, 5)),
    ("13", "dominant thirteenth", (0, 4, 7, 10, 2, 5, 9)),
)

# _BY_ROOT[root] -> ((mask, size, quality_index), ...). Transposing the templates
# once at import turns the per-frame work into integer masking.
_BY_ROOT: tuple[tuple[tuple[int, int, int], ...], ...] = tuple(
    tuple(
        (
            sum(1 << ((root + iv) % 12) for iv in set(ivs)),
            len({iv % 12 for iv in ivs}),
            qi,
        )
        for qi, (_suffix, _name, ivs) in enumerate(_QUALITIES)
    )
    for root in range(12)
)


def detect_chord(notes: list[int], key: str = "C") -> dict | None:
    """Name the chord in a set of sounding midi notes.

    Returns None for fewer than three distinct pitch classes, and for clusters
    that no template comes close to (a chromatic run is not a chord).

    How ambiguity is resolved, in order:

    1. **Smallest disagreement with what is actually sounding**, scored as
       ``missing + 2 * extra``. A leftover note counts double because it is a
       note you really played that the name is throwing away, while a missing
       one is only a tone you chose not to voice. That weighting is what reads
       C-E-Bb-D-A as C9 with the 13th left over, rather than as an A minor
       triad with a stray Bb and D.
       At most one tone may be missing, and only from a seventh or larger, so a
       three-note cluster can never be read as a mutilated ninth chord.
    2. **The bass note wins the root.** This is what settles the genuinely
       symmetric cases: C6 and Am7 are the same four pitch classes, and so are
       Csus2 and Gsus4, Cm7b5 and Ebm6, and all four rotations of a dim7. If you
       put C at the bottom you meant C6; if you put A at the bottom you meant Am7.
    3. **Template order** (see ``_QUALITIES``), then the lowest root, so the
       answer is deterministic when nothing above decides.

    The root itself must be sounding -- rootless jazz voicings are out of scope,
    and skipping absent roots is also what keeps this inside the 60 Hz budget.
    """
    if not notes:
        return None
    pcs_mask = 0
    for n in notes:
        pcs_mask |= 1 << (n % 12)
    if pcs_mask.bit_count() < 3:
        return None
    bass_pc = min(notes) % 12

    best_rank = -1
    best: tuple[int, int, int, int, int] | None = None
    # How many readings share each cost, for the confidence. Since `missing` is
    # capped at 1, the cost's parity recovers it: an odd cost always means one
    # assumed tone, an even one none. So equal cost really is the same (missing,
    # extra) pair, and this stays a count of genuinely equal readings.
    tally = [0] * 32

    for root in range(12):
        if not (pcs_mask >> root) & 1:
            continue
        for mask, size, qi in _BY_ROOT[root]:
            missing = (mask & ~pcs_mask).bit_count()
            if missing > 1 or (missing and size < 4):
                continue
            extra = (pcs_mask & ~mask).bit_count()
            cost = missing + 2 * extra
            tally[cost] += 1
            rank = ((31 - cost) * 10_000
                    + (100 if root == bass_pc else 0) + (63 - qi))
            if rank > best_rank:
                best_rank = rank
                best = (root, qi, mask, missing, extra)

    if best is None:
        return None
    root, qi, mask, missing, n_extra = best
    suffix, spoken, intervals = _QUALITIES[qi]

    bass_interval = (bass_pc - root) % 12
    inversion = intervals.index(bass_interval) if bass_interval in intervals else -1
    root_name = pitch_class_name(root, key)
    symbol = root_name + suffix
    if bass_pc != root:
        symbol += "/" + pitch_class_name(bass_pc, key)

    ties = tally[missing + 2 * n_extra]
    confidence = 1.0 - 0.20 * missing - 0.18 * n_extra - 0.06 * (ties - 1)
    if inversion != 0:
        confidence -= 0.08

    return {
        "symbol": symbol,
        "root_pc": root,
        "bass_pc": bass_pc,
        "quality": suffix,  # the symbol suffix, so a plain major triad is ""
        "name": f"{root_name} {spoken}",
        "inversion": inversion,  # -1 when the bass is not a chord tone at all
        "extra": [pc for pc in range(12)
                  if (pcs_mask >> pc) & 1 and not (mask >> pc) & 1],
        "confidence": round(max(0.05, min(1.0, confidence)), 2),
    }


# --- scales ------------------------------------------------------------------
# Semitones above the tonic, in scale order, so the index is the degree - 1.
MODES: dict[str, tuple[int, ...]] = {
    "major": (0, 2, 4, 5, 7, 9, 11),
    "natural_minor": (0, 2, 3, 5, 7, 8, 10),
    "harmonic_minor": (0, 2, 3, 5, 7, 8, 11),
    "melodic_minor": (0, 2, 3, 5, 7, 9, 11),
    "dorian": (0, 2, 3, 5, 7, 9, 10),
    "phrygian": (0, 1, 3, 5, 7, 8, 10),
    "lydian": (0, 2, 4, 6, 7, 9, 11),
    "mixolydian": (0, 2, 4, 5, 7, 9, 10),
    "locrian": (0, 1, 3, 5, 6, 8, 10),
    "major_pentatonic": (0, 2, 4, 7, 9),
    "minor_pentatonic": (0, 3, 5, 7, 10),
    "blues": (0, 3, 5, 6, 7, 10),
    "chromatic": (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11),
}

_SCALES: dict[tuple[str, str], tuple[int, ...]] = {
    (name, mode): tuple((_TONIC_PC[name] + iv) % 12 for iv in steps)
    for name in KEYS
    for mode, steps in MODES.items()
}


def _scale(key: str, mode: str) -> tuple[int, ...]:
    try:
        return _SCALES[(_key_name(key), mode)]
    except KeyError:
        raise ValueError(f"unknown mode {mode!r}; expected one of "
                         f"{', '.join(sorted(MODES))}") from None


def scale_pitch_classes(key: str, mode: str = "major") -> list[int]:
    """Pitch classes in scale order starting on the tonic, not sorted.

    ``key`` names the tonic. For a minor-key piece pass its own tonic with the
    minor mode (A + natural_minor), not the relative major -- but note that
    ``note_parts`` spells against the *major* key of that name, so pass the
    relative major there if the accidentals come out looking wrong.
    """
    return list(_scale(key, mode))


def in_scale(midi: int, key: str, mode: str = "major") -> bool:
    return (midi % 12) in _scale(key, mode)


def scale_degree(midi: int, key: str, mode: str = "major") -> int | None:
    """1-based scale degree, or None when the note is outside the scale."""
    pcs = _scale(key, mode)
    pc = midi % 12
    return pcs.index(pc) + 1 if pc in pcs else None
