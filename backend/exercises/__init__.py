"""The exercise engine: parameters in, a list of target chords out, a grade back.

This package exists so the Practice tab is a *shelf* rather than a feature. Sight
reading is not special here -- it is one generator among several, and the grader
cannot tell it apart from a scale. That is the test of the design.

The whole thing rests on one decision:

    **A step is a chord, not a note.**

    Step((60,))                 one note   -- a sight-reading target
    Step((60, 64, 67))          a triad    -- a cadence, a voicing drill
    Step((48, 72))              two hands  -- hands-together, an octave apart

A single-note step is the degenerate case, so one grader serves reading, scales,
chords and hands-together work without a branch. Everything downstream -- grading,
timing, the staff renderer, storage -- is written once against that shape.

The second decision is that **timed and untimed are a property of the plan, never of
the engine**. `Plan.bpm is None` is the entire switch:

    untimed   waits for the right note, reports reaction time, never advances on a miss
    timed     runs against the click, reports offset from the grid, allows a bounded skip

Adding exercise type #7 must touch exactly one new module in this package plus one
`register()` call. If it needs anything else -- a new endpoint, a frontend module, a
grader branch -- the abstraction has failed and the fix is here, not there.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from ..store import Store

# Notes closer together than this are one gesture, not two. Same figure timing.py uses
# to separate a chord from a melodic interval.
CHORD_WINDOW_MS = 45.0


@dataclass(frozen=True, slots=True)
class Step:
    """One thing you have to play at one moment."""

    notes: tuple[int, ...]
    beat: float | None = None        # beats from the start; None on an untimed plan
    hand: str = "R"                  # "R" | "L" | "B" -- display, and hands-together sync
    fingers: tuple[int, ...] = ()    # parallel to notes; empty when unknown
    # Set by the generator, never inferred later: which finger you used is a choice, and
    # guessing it from pitch afterwards would make the thumb-crossing metric a fiction.
    crossing: bool = False
    label: str = ""                  # "ii7", "3rd inv" -- display only

    def to_dict(self, namer: Callable[[int], dict]) -> dict[str, Any]:
        return {
            "notes": [namer(n) for n in self.notes],
            "midi": list(self.notes),
            "beat": self.beat,
            "hand": self.hand,
            "fingers": list(self.fingers),
            "crossing": self.crossing,
            "label": self.label,
        }


@dataclass(frozen=True, slots=True)
class Plan:
    """A concrete exercise, ready to play. Policy travels with the notes."""

    exercise: str                    # registry id
    variant: str                     # stable slug -- what you compare across days
    title: str                       # "C major, 2 octaves, hands together"
    key: str
    steps: tuple[Step, ...]
    bpm: float | None = None         # None => untimed. THE switch.
    beats_per_bar: int = 4
    count_in_bars: int = 1
    # How far ahead a played note may resolve. 0 makes a wrong note block, which is what
    # gives reaction time its meaning. >0 lets a timed run survive one dropped note --
    # a *skipped* note would otherwise mark every remaining step wrong.
    lookahead: int = 0
    staff: str = "grand"             # "grand" | "treble" | "bass" | "none"
    show_keyboard: bool = True
    show_fingers: bool = False
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def timed(self) -> bool:
        return self.bpm is not None


@dataclass(frozen=True, slots=True)
class Param:
    """One control. The frontend builds the whole setup form from these, which is why a
    new exercise type needs no new JavaScript."""

    id: str
    label: str
    kind: str                        # key | mode | int | choice | bool | bpm | note
    default: Any
    choices: tuple[tuple[Any, str], ...] = ()
    lo: int = 0
    hi: int = 0
    help: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "label": self.label, "kind": self.kind,
            "default": self.default, "lo": self.lo, "hi": self.hi, "help": self.help,
            "choices": [{"value": v, "label": t} for v, t in self.choices],
        }


@dataclass
class GenContext:
    """What a generator is allowed to reach. Deliberately narrow: the store, for
    adaptive weighting, and an rng, so generation is reproducible under test."""

    store: Store
    rng: random.Random
    display_key: str = "C"           # how to spell, from settings.ui.key_signature


@dataclass(frozen=True, slots=True)
class ExerciseType:
    id: str
    name: str
    blurb: str                       # one line on the shelf card
    params: tuple[Param, ...]
    generate: Callable[[dict, GenContext], Plan]
    timed_default: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "blurb": self.blurb,
            "timed_default": self.timed_default,
            "params": [p.to_dict() for p in self.params],
        }


REGISTRY: dict[str, ExerciseType] = {}


def register(ex: ExerciseType) -> ExerciseType:
    REGISTRY[ex.id] = ex
    return ex


def defaults_for(ex: ExerciseType) -> dict[str, Any]:
    return {p.id: p.default for p in ex.params}


def clean_params(ex: ExerciseType, raw: dict[str, Any] | None) -> dict[str, Any]:
    """Coerce whatever the browser sent into the shape the generator expects.

    Generators are pure and assume their inputs are already valid, so every guard
    lives here rather than being repeated in each one.
    """
    out = defaults_for(ex)
    raw = raw or {}
    for p in ex.params:
        if p.id not in raw:
            continue
        v = raw[p.id]
        try:
            if p.kind in ("int", "note", "bpm"):
                v = max(p.lo, min(p.hi, int(v)))
            elif p.kind == "bool":
                v = bool(v)
            elif p.kind == "choice":
                allowed = {c[0] for c in p.choices}
                if v not in allowed:
                    continue
            else:
                v = str(v)
        except (TypeError, ValueError):
            continue
        out[p.id] = v
    return out


GENERATOR_MODULES = ("reading", "scales")


def load_all() -> dict[str, ExerciseType]:
    """Import every generator module so it can register itself.

    Imported here rather than at package import time so `from . import exercises` stays
    cheap and the import graph has one obvious entry point.

    One broken generator must not take the shelf down with it. A typo in a new exercise
    should cost you that exercise, not the whole Practice tab -- and certainly not the
    app, which is holding the audio device.
    """
    import importlib
    import logging

    for name in GENERATOR_MODULES:
        try:
            importlib.import_module(f".{name}", __name__)
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("keys.exercises").warning(
                "exercise generator %r failed to load: %s", name, exc)
    return REGISTRY
