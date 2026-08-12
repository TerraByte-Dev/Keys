"""Sight reading, as one exercise among several rather than a special case.

This is a straight port of the old standalone SightReader: same adaptive weighting from
your own attempt history, same range clamping, same leap limit. What changed is that it
now emits `Step`s and a `Plan` like every other generator, so the shared grader handles
it and there is no sight-reading-shaped code anywhere else in the app.

It is the canonical UNTIMED exercise -- `bpm=None`, `lookahead=0`. A wrong note does not
advance the cursor: you have to find the right key, which is what makes the reaction
time mean anything and is what a teacher sitting next to you would do.
"""

from __future__ import annotations

from .. import config, music
from . import ExerciseType, GenContext, Param, Plan, Step, register

MAX_LEAP = 12          # keep successive notes within an octave -- reading, not gymnastics
WEAK_BOOST = 3.0       # how hard the adaptive weighting pulls toward your worst notes
MIN_ATTEMPTS = 3       # a note needs this many attempts before it counts as weak


def _weights(store, candidates: list[int], adaptive: bool) -> list[float]:
    if not adaptive:
        return [1.0] * len(candidates)
    weak = {
        row["note"]: row
        for row in store.weak_notes(limit=40)
        if row.get("attempts", 0) >= MIN_ATTEMPTS
    }
    out = []
    for note in candidates:
        row = weak.get(note)
        # accuracy 1.0 -> weight 1.0; accuracy 0.0 -> weight 1 + WEAK_BOOST
        out.append(1.0 if row is None
                   else 1.0 + WEAK_BOOST * (1.0 - float(row.get("accuracy", 1.0))))
    return out


def generate(params: dict, ctx: GenContext) -> Plan:
    key = str(params.get("key", "C"))
    clef = str(params.get("clef", "both"))
    count = max(1, min(16, int(params.get("notes", 4))))
    # What you asked for, met with the keys you have. Never inverted, never empty --
    # the swap this used to do turned an empty intersection into a window ABOVE the
    # instrument and generated notes nobody could play, then logged them as intended.
    want_lo, want_hi = int(params.get("low", 55)), int(params.get("high", 79))
    low_key, high_key = config.instrument_range()
    lo, hi = music.reading_window(clef, want_lo, want_hi, low_key, high_key)
    # The same window against the full piano, for the variant slug only -- see below.
    slug_lo, slug_hi = music.reading_window(clef, want_lo, want_hi,
                                            config.LOW_KEY, config.HIGH_KEY)

    scale = set(music.scale_pitch_classes(key, "major"))
    candidates = [n for n in range(lo, hi + 1) if n % 12 in scale] or list(range(lo, hi + 1))
    weights = _weights(ctx.store, candidates, bool(params.get("adaptive", True)))

    picked: list[int] = []
    for i in range(count):
        if i == 0:
            pool, pool_w = candidates, weights
        else:
            near = [(n, w) for n, w in zip(candidates, weights)
                    if abs(n - picked[-1]) <= MAX_LEAP and n != picked[-1]]
            pool, pool_w = ([p[0] for p in near], [p[1] for p in near]) if near \
                else (candidates, weights)
        picked.append(ctx.rng.choices(pool, weights=pool_w, k=1)[0])

    steps = tuple(
        Step(notes=(n,),
             hand="R" if (clef == "treble" or (clef == "both" and n >= 60)) else "L")
        for n in picked
    )
    return Plan(
        exercise="reading",
        # The clef window measured against the FULL piano -- not the keyboard you have,
        # and not the raw request either.
        #
        # A slug is an exercise's identity across sessions, so it must not move. Keying
        # it on the instrument-clamped bounds forks the row you have been watching
        # improve the day you declare a controller. Keying it on the raw request looks
        # safe and is not: it silently renames every treble and bass row that already
        # exists, because those were stored clef-windowed. Windowing against 21..108
        # reproduces exactly what v0.6.0 wrote for everyone, and never moves again.
        variant=f"{key}:{clef}:{count}:{slug_lo}-{slug_hi}",
        title=f"Sight reading in {key}, {count} note{'s' if count != 1 else ''}",
        key=key,
        steps=steps,
        bpm=None,          # untimed: wait for the right note
        lookahead=0,       # and never advance past a wrong one
        staff="both" if clef == "both" else clef,
        show_keyboard=False,
        show_fingers=False,
        params=dict(params),
    )


register(ExerciseType(
    id="reading",
    name="Sight reading",
    blurb="Read a measure and play it. Weighted toward the notes you get wrong.",
    timed_default=False,
    params=(
        Param("key", "Key", "key", "C"),
        Param("clef", "Clef", "choice", "both",
              choices=(("both", "Grand staff"), ("treble", "Treble"), ("bass", "Bass"))),
        Param("notes", "Notes", "int", 4, lo=1, hi=16),
        # Bounds stay at the full piano and are NOT narrowed to the instrument. The
        # registry is built once at import and ExerciseType is frozen, so a live range
        # could never reach here anyway -- and the clamp that matters is the one in
        # generate(), which meets these against the keyboard every time it runs. Keeping
        # the sliders wide means your stated preference survives plugging a bigger
        # keyboard in.
        Param("low", "Lowest", "note", 55, lo=config.LOW_KEY, hi=config.HIGH_KEY),
        Param("high", "Highest", "note", 79, lo=config.LOW_KEY, hi=config.HIGH_KEY),
        Param("adaptive", "Weight toward my worst notes", "bool", True,
              help="Uses your own attempt history to pick what you keep missing."),
    ),
    generate=generate,
))
