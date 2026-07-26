"""Turning a run's records into the handful of numbers worth showing.

Almost all of this is assembling `backend/timing.py`, which already does the hard parts
-- median-based tempo, drift over a sliding window, and a coefficient of variation
computed *after* removing the drift trend. Reimplementing any of that here would give
two answers to the same question.

Three numbers are computed here because nothing else can:

* **hands-together synchrony** -- how far apart the two hands land on a step that has
  notes for both. This is the measurement for the thing most people plateau on, and it
  falls straight out of steps-as-chords for two lines of code.
* **thumb-crossing cost** -- the inter-onset gap into a crossing minus the median gap
  everywhere else. The single most diagnostic number for a scale: an even run with a
  40 ms bump at every crossing is a hand that has not learned to prepare the thumb.
  Cheap only because the generator flagged which steps are crossings.
* **dynamic evenness** -- the spread of velocities. Meaningless until the instrument is
  off fixed-velocity touch, which the Play tab already detects and says.

No verdicts, no scores, no stars. Accuracy, evenness, sync, and what tempo you actually
played at. The workspace framing decides that.
"""

from __future__ import annotations

import statistics
from typing import Any

from .. import timing
from . import Plan

# "Clean" reuses timing.py's existing "good" boundary rather than inventing a second
# threshold, and needs enough steps that a one-octave rip cannot set a record.
CLEAN_CV = 0.06
CLEAN_MIN_STEPS = 16


def grade(plan: Plan, records: list[dict[str, Any]]) -> dict[str, Any]:
    played = [r for r in records if not r["skipped"]]
    onsets = [r["onset"] for r in played if r["onset"] is not None]
    offsets = [r["offset_ms"] for r in records if r["offset_ms"] is not None]
    vels = [v for r in played for v in (r["velocities"] or [])]

    steps = len(plan.steps)
    correct = sum(1 for r in records if r["correct"])
    accuracy = correct / steps if steps else None

    analysis = timing.analyze(onsets, offsets or None)
    steadiness = analysis.get("steadiness") or {}
    tempo = analysis.get("tempo") or {}
    drift = analysis.get("drift") or {}
    grid = analysis.get("grid")

    cv = steadiness.get("cv")
    out: dict[str, Any] = {
        "exercise": plan.exercise,
        "variant": plan.variant,
        "title": plan.title,
        "key": plan.key,
        "bpm": plan.bpm,
        "steps": steps,
        "played": len(played),
        "correct": correct,
        "skipped": sum(1 for r in records if r["skipped"]),
        "accuracy": round(accuracy, 4) if accuracy is not None else None,
        "evenness_cv": round(cv, 4) if cv is not None else None,
        "evenness": steadiness.get("rating"),
        "ioi_mean_ms": steadiness.get("mean_ms"),
        "tempo_bpm": tempo.get("bpm"),
        "drift_bpm_per_min": drift.get("bpm_per_min"),
        "grid_abs_ms": grid.get("abs_mean_ms") if grid else None,
        "grid_mean_ms": grid.get("mean_ms") if grid else None,
        "rushing": grid.get("rushing") if grid else None,
        "dragging": grid.get("dragging") if grid else None,
        "vel_sd": round(statistics.pstdev(vels), 2) if len(vels) > 1 else None,
        "mean_reaction_ms": _mean([r["reaction_ms"] for r in played
                                   if r["reaction_ms"] is not None]),
        "sync_ms": hands_sync_ms(records),
        "crossing_ms": crossing_cost_ms(records),
        "clean": bool(accuracy == 1.0 and cv is not None and cv < CLEAN_CV
                      and steps >= CLEAN_MIN_STEPS),
    }
    return out


def hands_sync_ms(records: list[dict[str, Any]]) -> float | None:
    """Mean onset spread on steps that ask for both hands at once.

    Only two-hand steps count: the spread of a one-hand chord is how much you rolled
    it, which is a different (also interesting) thing and would muddy this number.
    """
    spreads = [r["spread_ms"] for r in records
               if r["hand"] == "B" and not r["skipped"] and r["spread_ms"] is not None]
    return round(_mean(spreads), 1) if spreads else None


def crossing_cost_ms(records: list[dict[str, Any]]) -> float | None:
    """Inter-onset gap into a thumb crossing, minus the median gap everywhere else.

    Positive means the hand hesitates at the crossing. Needs a few of each to mean
    anything, so it returns None rather than a number built on one sample.
    """
    played = [r for r in records if not r["skipped"] and r["onset"] is not None]
    if len(played) < 6:
        return None
    at_crossing, elsewhere = [], []
    for prev, cur in zip(played, played[1:]):
        gap = (cur["onset"] - prev["onset"]) * 1000.0
        (at_crossing if cur["crossing"] else elsewhere).append(gap)
    if len(at_crossing) < 2 or len(elsewhere) < 3:
        return None
    return round(_mean(at_crossing) - statistics.median(elsewhere), 1)


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0
