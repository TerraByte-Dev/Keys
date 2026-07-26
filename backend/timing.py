"""Timing analysis over note onsets. Pure functions, no state, no I/O.

Why drift is a headline number here instead of one more error statistic:

Bock and Duke, "Not My Tempo" (ISME 2026, n=36) measured 36 players with and
without a click. With one they were more *accurate* beat to beat. Without one
they held their inter-onset spacing just as evenly while the whole performance
slid in tempo. Mean absolute error cannot tell those apart -- a smooth
accelerando and a ragged steady take score the same. So this module answers two
separate questions: how evenly spaced were the notes (steadiness) and where did
the tempo go (drift). Steadiness is measured on *detrended* intervals precisely
so that the even-but-accelerating player reads as "even spacing plus drift",
which is the finding rather than a bug in the metric.

Everything takes ``time.perf_counter`` seconds and answers in milliseconds or
bpm. Nothing in here imports the rest of ``backend`` -- it has to stay testable
with no piano, no synth and no sound card.
"""

from __future__ import annotations

import statistics
from typing import Any

# Two notes this close are one chord, not two rhythmic events. 45 ms is above a
# hand's natural spread on a big chord and well below any interval a human plays
# on purpose (32nd notes at 200 bpm are still 37 ms apart, but nobody practices
# those on this piano).
CHORD_WINDOW_MS = 45.0

# Below this the tempo counts as held. Roughly the point where a whole 2-minute
# piece ends within a couple of bpm of where it started.
STEADY_BPM_PER_MIN = 2.0

# Systematic offset from the click beyond which "you are early/late" is worth
# saying out loud. Under this is inside the noise of a keybed and a human ear.
GRID_BIAS_MS = 12.0


def inter_onset_intervals(onsets: list[float]) -> list[float]:
    """Consecutive differences in milliseconds. Raw -- chords are not collapsed."""
    return [(b - a) * 1000.0 for a, b in zip(onsets, onsets[1:])]


def collapse_chords(onsets: list[float], window_ms: float = CHORD_WINDOW_MS) -> list[float]:
    """Fold near-simultaneous onsets into one event, keeping the earliest.

    Each candidate is compared against the last *kept* onset rather than its
    immediate predecessor, so a fast even run cannot chain itself into a single
    giant event; the kept events are always at least ``window_ms`` apart.

    The input is sorted first because onsets arrive from a deque that a slow UI
    drain can reorder, and one negative interval poisons every statistic below.
    """
    events: list[float] = []
    for t in sorted(onsets):
        if not events or (t - events[-1]) * 1000.0 >= window_ms:
            events.append(t)
    return events


def estimate_tempo(onsets: list[float]) -> dict[str, Any]:
    """Median-interval tempo estimate.

    Median, not mean: a single long pause between phrases is one huge interval,
    and a mean would let it drag the estimate toward half the real tempo.
    """
    events = collapse_chords(onsets)
    n = len(events)
    if n < 4:
        return {"bpm": None, "confidence": 0.0, "ioi_median_ms": None, "n": n}

    iois = inter_onset_intervals(events)
    median_ms = statistics.median(iois)
    if median_ms <= 0:
        return {"bpm": None, "confidence": 0.0, "ioi_median_ms": None, "n": n}

    # Confidence is how tightly the intervals cluster (median absolute deviation
    # relative to the median, so the pause does not count against it) discounted
    # by how few of them there are. Three intervals is thin evidence however
    # even they look.
    mad = statistics.median([abs(v - median_ms) for v in iois])
    spread = max(0.0, 1.0 - 3.0 * (mad / median_ms))
    confidence = spread * min(1.0, (n - 1) / 8.0)

    return {
        "bpm": round(60000.0 / median_ms, 1),
        "confidence": round(confidence, 2),
        "ioi_median_ms": round(median_ms, 1),
        "n": n,
    }


def tempo_drift(onsets: list[float]) -> dict[str, Any]:
    """Least-squares trend of local tempo against wall time, in bpm per minute.

    Local tempo is the median interval inside a sliding window, so one hesitation
    moves a couple of samples instead of bending the whole line. Negative means
    slowing down.

    Drift needs a long take to be worth reporting. The standard error of the
    slope falls off as n**-1.5, so with a sloppy player (cv ~0.065) 100 onsets
    put the estimate within +/-6 bpm/min of the truth and 400 put it inside
    +/-1.2 -- measured over 40 seeds in tools/timing_check.py's noise model.
    Under a couple of hundred onsets, treat a small non-zero number as noise.
    """
    events = collapse_chords(onsets)
    n = len(events)
    empty = {"bpm_per_min": None, "start_bpm": None, "end_bpm": None,
             "steady": None, "r_squared": None, "n": n}
    if n < 8:
        return empty

    iois = inter_onset_intervals(events)
    width = max(3, min(8, len(iois) // 4))

    minutes: list[float] = []
    local_bpm: list[float] = []
    for i in range(len(iois) - width + 1):
        median_ms = statistics.median(iois[i:i + width])
        if median_ms <= 0:
            continue
        # Stamp each sample at the middle of the span it covers, not at its edge,
        # or every local tempo is reported half a window late.
        minutes.append(((events[i] + events[i + width]) / 2.0 - events[0]) / 60.0)
        local_bpm.append(60000.0 / median_ms)

    if len(local_bpm) < 3:
        return empty

    slope, intercept, r_squared = _linear_fit(minutes, local_bpm)
    return {
        "bpm_per_min": round(slope, 2),
        "start_bpm": round(intercept + slope * minutes[0], 1),
        "end_bpm": round(intercept + slope * minutes[-1], 1),
        "steady": abs(slope) < STEADY_BPM_PER_MIN,
        "r_squared": round(r_squared, 3),
        "n": n,
    }


def steadiness(onsets: list[float]) -> dict[str, Any]:
    """Evenness of spacing, measured after the tempo trend is removed.

    The detrend is the whole point. Raw sd counts drift as unevenness, which is
    the mistake the research warns about: a player who is metronomically even
    while gliding from 100 to 130 bpm is not sloppy, they are steady and
    drifting, and those are two different practice problems.
    """
    events = collapse_chords(onsets)
    n = len(events)
    # 3 intervals is the floor: 2 points fit a line exactly and leave residuals
    # of zero, which would report perfect steadiness for any two notes at all.
    if n < 4:
        return {"cv": None, "sd_ms": None, "mean_ms": None, "rating": None, "n": n}

    iois = inter_onset_intervals(events)
    mean_ms = statistics.fmean(iois)
    slope, intercept, _r2 = _linear_fit([float(i) for i in range(len(iois))], iois)
    residuals = [v - (intercept + slope * i) for i, v in enumerate(iois)]
    sd_ms = statistics.stdev(residuals)
    cv = sd_ms / mean_ms if mean_ms > 0 else 0.0

    if cv < 0.03:
        rating = "excellent"
    elif cv < 0.06:
        rating = "good"
    elif cv < 0.12:
        rating = "fair"
    else:
        rating = "loose"

    return {
        "cv": round(cv, 3),
        "sd_ms": round(sd_ms, 1),
        "mean_ms": round(mean_ms, 1),
        "rating": rating,
        "n": n,
    }


def grid_error(offsets_ms: list[float]) -> dict[str, Any]:
    """Summarise per-note distance from the nearest click. Positive is late.

    Mean and absolute mean are both here because they answer different things:
    the mean is bias (are you consistently ahead of the beat), the absolute mean
    is accuracy (how far off you are at all). A player 20 ms early half the time
    and 20 ms late the other half has a mean of zero and is not on the beat.
    """
    n = len(offsets_ms)
    if n == 0:
        return {"mean_ms": None, "abs_mean_ms": None, "sd_ms": None,
                "rushing": None, "dragging": None, "n": 0}

    mean_ms = statistics.fmean(offsets_ms)
    return {
        "mean_ms": round(mean_ms, 1),
        "abs_mean_ms": round(statistics.fmean([abs(v) for v in offsets_ms]), 1),
        "sd_ms": round(statistics.stdev(offsets_ms), 1) if n >= 2 else 0.0,
        "rushing": mean_ms < -GRID_BIAS_MS,
        "dragging": mean_ms > GRID_BIAS_MS,
        "n": n,
    }


def analyze(onsets: list[float], offsets_ms: list[float] | None = None) -> dict[str, Any]:
    """Everything above in one dict. Never raises -- thin input returns Nones."""
    return {
        "tempo": estimate_tempo(onsets),
        "drift": tempo_drift(onsets),
        "steadiness": steadiness(onsets),
        "grid": grid_error(offsets_ms) if offsets_ms is not None else None,
    }


def _linear_fit(xs: list[float], ys: list[float]) -> tuple[float, float, float]:
    """Ordinary least squares. Returns (slope, intercept, r_squared).

    A vertical or single-point x gives a flat line rather than a division error.
    r_squared is 1.0 when y is constant, because a flat line does explain a flat
    series perfectly -- the usual 0/0 convention would report the opposite.
    """
    n = len(xs)
    if n < 2:
        return (0.0, ys[0] if ys else 0.0, 0.0)

    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    sxx = sum((x - mean_x) ** 2 for x in xs)
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    slope = sxy / sxx if sxx > 0 else 0.0
    intercept = mean_y - slope * mean_x

    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    ss_res = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
    r_squared = 1.0 if ss_tot <= 0 else max(0.0, 1.0 - ss_res / ss_tot)
    return (slope, intercept, r_squared)
