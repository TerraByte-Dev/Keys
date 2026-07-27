"""Scales and arpeggios. Two registry entries, one walker.

A scale and an arpeggio differ by *which* semitone offsets you climb -- a mode's seven
(or five, or twelve) against a chord's three or four -- and by whether you climb them
straight through or in a repeating three-note figure. None of that is worth a second
module, so ``_walk`` builds both shapes and everything below it is shared. Adding
"sixths" or "broken octaves" later is a new offsets table, not a new file.

Four decisions carry the module:

* **Hands are the reason this exists.** Left/right independence is the thing that
  actually stalls, so ``hands="B"`` is not a display option -- it emits a genuine
  two-note Step, and the grader's onset spread on that Step *is* the synchrony
  measurement. It costs nothing here and cannot be recovered later from two one-note
  steps that happen to sit next to each other.

* **A descending scale is not the ascending intervals negated.** It is the same pitch
  classes read the other way, which is what ``_mirror`` computes. Negating the intervals
  would put Bb in a descending C major. This only bites in contrary motion, where the
  left hand really does walk downward from the tonic.

* **The run is anchored to the hand, not to the middle of the keyboard.** A two-octave
  right-hand C major belongs at C4..C6; centring it on the 88 keys would put it at
  C3..C5, which is not where the right hand lives. So the tonic starts at middle C for
  the right hand and an octave below for the left, and long runs hang off the top of
  that and get pulled down by whole octaves only when they run out of keys.

* **Range is a hard invariant, so octaves is a request.** Four octaves of contrary
  motion spans 108 semitones and there are only 87 available; the generator shrinks the
  run until it fits and writes the number it actually used back into the params, because
  a form that keeps claiming 4 while the plan plays 3 lies to the user and to the slug.
"""

from __future__ import annotations

from typing import Any, Callable, Sequence

from .. import music
from . import ExerciseType, GenContext, Param, Plan, Step, clean_params, register

try:
    from .fingering import crossings, fingers_for
except ImportError:  # Fingering is decoration; without it a plan still plays.
    def fingers_for(tonic: str, form: str, hand: str, degrees: int) -> tuple[int, ...]:
        return ()

    def crossings(fingers: Sequence[int]) -> tuple[bool, ...]:
        return ()


# Two Steps per beat -- eighth notes. One note per beat is a dirge at any tempo a scale
# is actually practised at, and four would make the top of the bpm slider (200) demand
# 13.3 notes a second, which nobody in this house can play. At two, the 30..200 range
# spans 1.0 to 6.7 notes a second: beginner to fluent, with nothing wasted at either end.
STEPS_PER_BEAT = 2.0

# Where the tonic sits before anything is shifted for range, and how far below it the
# left hand starts. See the module docstring for why this is the hand's centre and not
# the keyboard's.
HOME = 60
HAND_GAP = 12

LOW, HIGH = 21, 108      # the 88 keys. Nothing may ever leave this range.

# A timed run may survive two dropped notes before the rest of the plan is marked wrong;
# untimed blocks on the wrong note, which is what gives reaction time its meaning.
LOOKAHEAD_TIMED = 2

_MODES: tuple[tuple[str, str], ...] = (
    ("major", "Major"),
    ("natural_minor", "Natural minor"),
    ("harmonic_minor", "Harmonic minor"),
    ("melodic_minor", "Melodic minor"),
    ("dorian", "Dorian"),
    ("phrygian", "Phrygian"),
    ("lydian", "Lydian"),
    ("mixolydian", "Mixolydian"),
    ("locrian", "Locrian"),
    ("major_pentatonic", "Major pentatonic"),
    ("minor_pentatonic", "Minor pentatonic"),
    ("blues", "Blues"),
    ("chromatic", "Chromatic"),
)

# Stacking order, so index 0 is the root and an inversion is a rotation of the tuple.
_QUALITIES: tuple[tuple[str, str, tuple[int, ...]], ...] = (
    ("major", "Major", (0, 4, 7)),
    ("minor", "Minor", (0, 3, 7)),
    ("dom7", "Dominant 7th", (0, 4, 7, 10)),
    ("dim7", "Diminished 7th", (0, 3, 6, 9)),
    ("maj7", "Major 7th", (0, 4, 7, 11)),
    ("min7", "Minor 7th", (0, 3, 7, 10)),
)

_KEYS: tuple[tuple[Any, str], ...] = tuple((k, k) for k in music.KEYS)
_HANDS: tuple[tuple[Any, str], ...] = (("R", "Right"), ("L", "Left"), ("B", "Both"))
_MOTION: tuple[tuple[Any, str], ...] = (("parallel", "Parallel"), ("contrary", "Contrary"))
_PATTERN: tuple[tuple[Any, str], ...] = (("straight", "Straight"), ("broken", "Broken"))

_MODE_WORDS = {value: label.lower() for value, label in _MODES}
_QUALITY_WORDS = {value: label.lower() for value, label, _tones in _QUALITIES}
_QUALITY_TONES = {value: tones for value, _label, tones in _QUALITIES}
_HAND_WORDS = {"R": "right hand", "L": "left hand", "B": "hands together"}
_INVERSION_WORDS = {1: "1st inversion", 2: "2nd inversion", 3: "3rd inversion"}


# --- the walker --------------------------------------------------------------
def _offsets(key: str, mode: str) -> tuple[int, ...]:
    """Semitones above the tonic, ascending.

    ``scale_pitch_classes`` hands back pitch classes, but a run is built from intervals.
    They are recoverable because MODES lists its steps ascending from 0, so subtracting
    the tonic and sorting cannot reorder anything.
    """
    pcs = music.scale_pitch_classes(key, mode)
    return tuple(sorted((pc - pcs[0]) % 12 for pc in pcs))


def _mirror(offsets: Sequence[int]) -> tuple[int, ...]:
    """The same collection read downward, as semitones *below* the tonic.

    C major descending from C is 0,1,3,5,7,8,10 -- the phrygian shape -- not the
    ascending 0,2,4,5,7,9,11 turned round, which would spell Bb and Db into it. The
    reflection is exact because ``(-o) % 12`` is a bijection on the twelve pitch classes,
    so the mirrored line always has the same number of notes as the line it mirrors.
    """
    return tuple(sorted({(-o) % 12 for o in offsets}))


def _walk(offsets: Sequence[int], octaves: int, pattern: str) -> tuple[int, ...]:
    """One ascending run, as semitones above the tonic. The whole engine.

    ``straight`` climbs the collection and keeps going, closing on the tonic an octave
    up. ``broken`` plays a three-note figure from every position in turn (1-3-5, 3-5-8,
    5-8-10 ...), so each chord tone gets a turn at the bottom of the hand -- which is the
    only thing broken chords are for.
    """
    n = len(offsets)

    def at(i: int) -> int:
        return offsets[i % n] + 12 * (i // n)

    if pattern == "broken":
        return tuple(at(p + k) for p in range(n * octaves) for k in range(3))
    return tuple(at(i) for i in range(n * octaves + 1))


def _updown(line: Sequence[int]) -> tuple[int, ...]:
    """Ascend then descend. The descent is the ascent reversed, so the run is a
    palindrome in pitch and the top note is struck once rather than twice.

    Classical melodic minor descends as natural minor and this deliberately does not:
    the palindrome is a contract the staff renderer and the checks both rely on, and the
    jazz form of the scale descends unchanged anyway.
    """
    return tuple(line) + tuple(reversed(line[:-1]))


def _invert(tones: Sequence[int], inversion: int) -> tuple[int, ...]:
    """Rotate a chord so the requested tone is in the bass, still ascending.

    A triad has three positions, not four, so a 3rd inversion of one is clamped to 2nd
    rather than wrapped to root -- asking for the highest inversion and silently getting
    the lowest is the one behaviour nobody expects.
    """
    n = len(tones)
    inv = max(0, min(int(inversion), n - 1))
    return tuple(tones[(i + inv) % n] + (12 if i + inv >= n else 0) for i in range(n))


# --- placing the run on the keyboard -----------------------------------------
def _home(tonic_pc: int) -> int:
    """The tonic nearest middle C, ties downward (F# major starts at F#3, not F#4)."""
    low = HOME - ((HOME - tonic_pc) % 12)
    return low if HOME - low <= 6 else low + 12


def _layout(offsets: Sequence[int], mirror: Sequence[int], octaves: int, pattern: str,
            hands: str, motion: str, updown: bool) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """(right, left) lines as offsets from the tonic. Either may be empty."""
    asc = _walk(offsets, octaves, pattern)
    if updown:
        asc = _updown(asc)

    right = asc if hands in ("R", "B") else ()
    left: tuple[int, ...] = ()
    if hands == "B" and motion == "contrary":
        # Both hands start on the tonic and move apart. A true unison start is the
        # syllabus form, but two hands cannot hold one key: the grader dedupes a Step's
        # notes into a set, so it would silently become a one-note step and the synchrony
        # measurement -- the entire point of hands together -- would read zero forever.
        down = _walk(mirror, octaves, pattern)
        if updown:
            down = _updown(down)
        left = tuple(-HAND_GAP - x for x in down)
    elif hands in ("L", "B"):
        left = tuple(x - HAND_GAP for x in asc)
    return right, left


def _fit(base: int, right: Sequence[int], left: Sequence[int]) -> tuple[int, bool]:
    """Shift the tonic by whole octaves until the run is on the keyboard.

    Whole octaves only -- the run has to start on the tonic, so a shift of anything else
    is not the same exercise. Returns (base, fits); a False means this many octaves does
    not fit at all and the caller should ask for fewer.
    """
    lines = [ln for ln in (right, left) if ln]
    lo = base + min(min(ln) for ln in lines)
    hi = base + max(max(ln) for ln in lines)
    k_lo = -(-(LOW - lo) // 12)          # smallest shift that clears the bottom key
    k_hi = (HIGH - hi) // 12             # largest shift that clears the top key
    if k_lo > k_hi:
        return base + 12 * k_lo, False
    k = 0 if k_lo <= 0 <= k_hi else (k_lo if k_lo > 0 else k_hi)
    return base + 12 * k, True


# --- fingering ---------------------------------------------------------------
def _fingering(key: str, form: str, hand: str, length: int, updown: bool,
               descending: bool) -> tuple[tuple[int, ...], tuple[bool, ...]]:
    """Fingers and thumb crossings for one line, or two empty tuples.

    Anything the fingering module cannot answer degrades to no fingering rather than to
    no exercise, which is the same bargain store.py makes with the practice log.

    A descending line is fingered by reading the ascending answer backwards -- left hand
    C major up is 5,4,3,2,1,3,2,1, and down from the tonic it is 1,2,3,1,2,3,4,5. Same
    tuple, other end.
    """
    try:
        fingers = tuple(fingers_for(key, form, hand, length) or ())
    except Exception:  # noqa: BLE001 -- see docstring; never lose the run over a finger
        return (), ()
    if len(fingers) != length:
        return (), ()
    if descending:
        fingers = tuple(reversed(fingers))
    if updown:
        fingers = _updown_fingers(fingers)
    try:
        marks = tuple(bool(c) for c in (crossings(fingers) or ()))
    except Exception:  # noqa: BLE001
        marks = ()
    return fingers, marks if len(marks) == len(fingers) else ()


def _updown_fingers(fingers: Sequence[int]) -> tuple[int, ...]:
    return tuple(fingers) + tuple(reversed(fingers[:-1]))


# --- the shared generator ----------------------------------------------------
def _plan(exercise: str, params: dict[str, Any], form: str, offsets: Sequence[int],
          describe: Callable[[int], tuple[str, str]], show_fingers: bool) -> Plan:
    key = params["key"]
    hands = params["hands"]
    # Motion means nothing with one hand, and letting it reach the slug there would split
    # one exercise's history into two rows the day the control is nudged.
    motion = params["motion"] if hands == "B" else "parallel"
    pattern = params.get("pattern", "straight")
    updown = bool(params["updown"])
    timed = bool(params["timed"])
    mirror = _mirror(offsets)
    home = _home(music.scale_pitch_classes(key, "major")[0])

    octaves, base = 1, home
    right: tuple[int, ...] = ()
    left: tuple[int, ...] = ()
    for want in range(int(params["octaves"]), 0, -1):
        right, left = _layout(offsets, mirror, want, pattern, hands, motion, updown)
        base, fits = _fit(home, right, left)
        octaves = want
        if fits:
            break

    run = len(_walk(offsets, octaves, pattern))   # ascending length, before the mirror
    # Only ask for a fingering the exercise actually has one for. An arpeggio passes its
    # chord quality as `form`, and "major" and "minor" are also scale forms -- so without
    # this guard a C major arpeggio silently collects C major *scale* fingering whenever
    # the two happen to be the same length, and Step.crossing would then feed
    # metrics.crossing_cost_ms a thumb that is nowhere near the notes being played.
    if show_fingers:
        r_fingers, r_cross = _fingering(key, form, "R", run, updown, descending=False)
        l_fingers, l_cross = _fingering(key, form, "L", run, updown,
                                        descending=(hands == "B" and motion == "contrary"))
    else:
        r_fingers, r_cross = (), ()
        l_fingers, l_cross = (), ()

    steps = []
    for i in range(len(right or left)):
        beat = i / STEPS_PER_BEAT if timed else None
        if hands == "B":
            notes = (base + left[i], base + right[i])
            # A partial answer would misalign fingers against notes, so it is all or none.
            fingers = (l_fingers[i], r_fingers[i]) if l_fingers and r_fingers else ()
            cross = (bool(l_cross and l_cross[i]) or bool(r_cross and r_cross[i]))
        elif hands == "L":
            notes = (base + left[i],)
            fingers = (l_fingers[i],) if l_fingers else ()
            cross = bool(l_cross and l_cross[i])
        else:
            notes = (base + right[i],)
            fingers = (r_fingers[i],) if r_fingers else ()
            cross = bool(r_cross and r_cross[i])
        steps.append(Step(notes=notes, beat=beat, hand=hands, fingers=fingers,
                          crossing=cross))

    variant, title = describe(octaves)
    used = dict(params)
    used["octaves"] = octaves        # what was played, not what was asked for
    return Plan(
        exercise=exercise,
        variant=variant,
        title=title,
        key=key,
        steps=tuple(steps),
        bpm=float(params["bpm"]) if timed else None,
        lookahead=LOOKAHEAD_TIMED if timed else 0,
        staff="grand",
        show_fingers=show_fingers,
        params=used,
    )


def _octave_words(octaves: int) -> str:
    return "1 octave" if octaves == 1 else f"{octaves} octaves"


def _hand_words(hands: str, motion: str) -> str:
    words = _HAND_WORDS[hands]
    return f"{words}, contrary motion" if motion == "contrary" else words


# --- scales ------------------------------------------------------------------
def generate_scale(params: dict[str, Any], ctx: GenContext) -> Plan:
    """Params are cleaned here as well as at the edge: clean_params is idempotent, and
    it keeps the generator callable straight from a check script."""
    p = clean_params(SCALE, params)
    key, mode = p["key"], p["mode"]
    hands = p["hands"]
    motion = p["motion"] if hands == "B" else "parallel"
    swing = "updown" if p["updown"] else "up"

    def describe(octaves: int) -> tuple[str, str]:
        # bpm and timed stay out of the slug on purpose: the same scale played faster is
        # the same scale, and it is the one row you want to watch get cleaner over weeks.
        variant = f"{key}:{mode}:{octaves}oct:{hands}:{motion}:{swing}"
        title = (f"{key} {_MODE_WORDS[mode]}, {_octave_words(octaves)}, "
                 f"{_hand_words(hands, motion)}")
        return variant, title

    return _plan("scale", p, mode, _offsets(key, mode), describe, show_fingers=True)


SCALE = register(ExerciseType(
    id="scale",
    name="Scales",
    blurb="Thirteen modes, one to four octaves, one hand or both -- parallel or contrary.",
    timed_default=True,
    params=(
        Param("key", "Key", "key", "C", choices=_KEYS),
        Param("mode", "Mode", "choice", "major", choices=_MODES),
        Param("octaves", "Octaves", "int", 2, lo=1, hi=4),
        Param("hands", "Hands", "choice", "R", choices=_HANDS),
        Param("motion", "Motion", "choice", "parallel", choices=_MOTION,
              help="Contrary motion needs both hands. The classic independence drill."),
        Param("bpm", "Tempo", "bpm", 80, lo=30, hi=200,
              help="Two notes per beat."),
        Param("timed", "Play with the metronome", "bool", True),
        Param("updown", "Up and down", "bool", True),
    ),
    generate=generate_scale,
))


# --- arpeggios ---------------------------------------------------------------
def generate_arpeggio(params: dict[str, Any], ctx: GenContext) -> Plan:
    p = clean_params(ARPEGGIO, params)
    key, quality = p["key"], p["quality"]
    hands = p["hands"]
    motion = p["motion"] if hands == "B" else "parallel"
    pattern = p["pattern"]
    swing = "updown" if p["updown"] else "up"
    tones = _QUALITY_TONES[quality]
    inversion = max(0, min(int(p["inversion"]), len(tones) - 1))

    def describe(octaves: int) -> tuple[str, str]:
        variant = (f"{key}:{quality}:inv{inversion}:{octaves}oct:"
                   f"{hands}:{motion}:{pattern}:{swing}")
        title = f"{key} {_QUALITY_WORDS[quality]} arpeggio"
        if inversion:
            title += f", {_INVERSION_WORDS[inversion]}"
        title += f", {_octave_words(octaves)}, {_hand_words(hands, motion)}"
        if pattern == "broken":
            title += ", broken"
        return variant, title

    # No fingering form, deliberately. The quality names share a namespace with the scale
    # modes, so passing `quality` here hands a C major arpeggio the C major *scale*
    # fingering -- thumb-unders, crossing flags and all -- for the one quality whose name
    # collides. ARPEGGIO_FINGERING is empty on purpose (see fingering.py); an empty form
    # is how that stays true.
    return _plan("arpeggio", p, "", _invert(tones, inversion), describe,
                 show_fingers=False)


ARPEGGIO = register(ExerciseType(
    id="arpeggio",
    name="Arpeggios",
    blurb="Triads and sevenths, any inversion, straight through or in broken threes.",
    timed_default=True,
    params=(
        Param("key", "Key", "key", "C", choices=_KEYS),
        Param("quality", "Quality", "choice", "major",
              choices=tuple((v, label) for v, label, _t in _QUALITIES)),
        Param("inversion", "Inversion", "int", 0, lo=0, hi=3,
              help="A triad has three positions, so 3 plays as 2nd inversion."),
        Param("pattern", "Pattern", "choice", "straight", choices=_PATTERN,
              help="Broken plays a three-note figure from every position in turn."),
        Param("octaves", "Octaves", "int", 2, lo=1, hi=4),
        Param("hands", "Hands", "choice", "R", choices=_HANDS),
        Param("motion", "Motion", "choice", "parallel", choices=_MOTION),
        Param("bpm", "Tempo", "bpm", 80, lo=30, hi=200,
              help="Two notes per beat."),
        Param("timed", "Play with the metronome", "bool", True),
        Param("updown", "Up and down", "bool", True),
    ),
    generate=generate_arpeggio,
))
