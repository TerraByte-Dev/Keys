"""Regression test for backend/timing.py, on synthetic signals with known answers.

No piano, no synth, no sound -- every train below is generated from an exact
tempo curve, so the right answer is known in advance and a wrong one here is a
bug in the math and nothing else. That is the only way to trust this module:
you cannot eyeball whether a drift number is correct on real playing.

    .venv\\Scripts\\python.exe tools\\timing_check.py
"""

from __future__ import annotations

import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import timing  # noqa: E402

ok = True


def step(label: str, passed: bool, detail: str = "") -> None:
    global ok
    ok = ok and passed
    print(f"  [{'PASS' if passed else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))


def even_train(bpm: float, count: int, start: float = 0.0) -> list[float]:
    period = 60.0 / bpm
    return [start + i * period for i in range(count)]


def ramp_train(bpm_start: float, bpm_end: float, count: int) -> list[float]:
    """Tempo interpolated linearly across the beat index, not across wall time."""
    onsets = [0.0]
    for i in range(count - 1):
        bpm = bpm_start + (bpm_end - bpm_start) * i / (count - 1)
        onsets.append(onsets[-1] + 60.0 / bpm)
    return onsets


def jitter(onsets: list[float], ms: float, seed: int = 42) -> list[float]:
    rng = random.Random(seed)
    return [t + rng.uniform(-ms, ms) / 1000.0 for t in onsets]


print("1. primitives")
iois = timing.inter_onset_intervals([0.0, 0.5, 1.0, 1.75])
step("intervals in ms", iois == [500.0, 500.0, 750.0], str(iois))
step("one onset has no intervals", timing.inter_onset_intervals([1.0]) == [])

print("2. perfectly even 120 bpm, 32 onsets")
even = even_train(120.0, 32)
t = timing.estimate_tempo(even)
s = timing.steadiness(even)
d = timing.tempo_drift(even)
step("tempo within 0.5 of 120", abs(t["bpm"] - 120.0) < 0.5,
     f"{t['bpm']} bpm, ioi {t['ioi_median_ms']} ms, confidence {t['confidence']}")
step("counted all 32 events", t["n"] == 32)
step("cv below 0.005", s["cv"] < 0.005, f"cv={s['cv']} sd={s['sd_ms']} ms -> {s['rating']}")
step("drift reports steady", d["steady"] is True,
     f"{d['bpm_per_min']} bpm/min, {d['start_bpm']} -> {d['end_bpm']}")

print("3. accelerating 100 -> 130 bpm over 64 onsets")
accel = ramp_train(100.0, 130.0, 64)
span_min = (accel[-1] - accel[0]) / 60.0
true_rate = 30.0 / span_min  # bpm gained per minute, averaged over the take
d = timing.tempo_drift(accel)
s = timing.steadiness(accel)
step("drift is positive", d["bpm_per_min"] > 0,
     f"{d['bpm_per_min']} bpm/min (true average {true_rate:.1f}), r2={d['r_squared']}")
step("magnitude is in the right ballpark", 0.5 * true_rate < d["bpm_per_min"] < 2.0 * true_rate,
     f"{d['bpm_per_min']} in ({0.5 * true_rate:.1f}, {2.0 * true_rate:.1f})")
step("not called steady", d["steady"] is False)
step("endpoints bracket the ramp", d["start_bpm"] < 110 < d["end_bpm"],
     f"{d['start_bpm']} -> {d['end_bpm']}")
# The point of detrending: an accelerando is EVEN spacing plus drift, not sloppiness.
step("detrended spacing still reads tight", s["rating"] in ("excellent", "good"),
     f"cv={s['cv']} -> {s['rating']}")

print("4. decelerating 130 -> 100 bpm over 64 onsets")
decel = ramp_train(130.0, 100.0, 64)
d = timing.tempo_drift(decel)
step("drift is negative", d["bpm_per_min"] < 0,
     f"{d['bpm_per_min']} bpm/min, {d['start_bpm']} -> {d['end_bpm']}")
step("not called steady", d["steady"] is False)

# 400 onsets, not 32. Swept over 40 seeds: at 100 onsets this noise level calls
# the tempo steady only 19 times out of 40, at 200 it is 34, at 400 it is 40 --
# the drift slope's standard error falls as n**-1.5, and a short jittery take
# genuinely cannot tell drift from noise. Tuning the assertion instead of the
# train length would have hidden that.
print("5. even 160 bpm with +/-30 ms of jitter, 400 onsets (seed 42)")
noisy = jitter(even_train(160.0, 400), 30.0)
t = timing.estimate_tempo(noisy)
s = timing.steadiness(noisy)
d = timing.tempo_drift(noisy)
step("tempo still lands on 160", abs(t["bpm"] - 160.0) < 3.0,
     f"{t['bpm']} bpm, confidence {t['confidence']}")
step("tempo reads as steady", d["steady"] is True,
     f"{d['bpm_per_min']} bpm/min, r2={d['r_squared']}")
step("but spacing does not", s["rating"] in ("fair", "loose"),
     f"cv={s['cv']} sd={s['sd_ms']} ms -> {s['rating']}")

print("6. chords collapse to one event")
chord = [0.0, 0.004, 0.009] + [0.5, 1.0, 1.5, 2.0, 2.5]
events = timing.collapse_chords(chord)
step("three notes inside 10 ms -> one event", len(events) == 6, f"{len(chord)} onsets -> {events}")
step("the earliest note is the one kept", events[0] == 0.0)
t = timing.estimate_tempo(chord)
step("chord does not distort the tempo", abs(t["bpm"] - 120.0) < 0.5,
     f"{t['bpm']} bpm from {t['n']} events")
run = timing.collapse_chords([0.0, 0.04, 0.08, 0.12])
step("a fast run does not chain into one event", len(run) == 2, str(run))
# collapse_chords sorts its input on purpose -- out-of-order onsets would
# otherwise produce negative intervals and poison every statistic downstream.
# Nothing else in this file feeds it unsorted input, so the guarantee is checked here.
shuffled = [1.0, 0.0, 2.5, 0.5, 2.0, 1.5]
step("out-of-order onsets are sorted, not turned into negative intervals",
     timing.collapse_chords(shuffled) == [0.0, 0.5, 1.0, 1.5, 2.0, 2.5]
     and timing.estimate_tempo(shuffled)["bpm"] == 120.0,
     f"{timing.estimate_tempo(shuffled)['bpm']} bpm from shuffled input")

print("7. one long pause in the middle")
paused = [t if i < 12 else t + 4.0 for i, t in enumerate(even_train(100.0, 24))]
t = timing.estimate_tempo(paused)
mean_bpm = 60000.0 / statistics.fmean(timing.inter_onset_intervals(paused))
step("median-based tempo within 5 of 100", abs(t["bpm"] - 100.0) < 5.0,
     f"{t['bpm']} bpm, confidence {t['confidence']}")
step("a mean would have been thrown off", abs(mean_bpm - 100.0) > 5.0,
     f"mean interval gives {mean_bpm:.1f} bpm -- off by {abs(mean_bpm - 100.0):.1f}")

print("8. grid error")
g = timing.grid_error([18.0, 25.0, 31.0, 22.0, 27.0, 19.0])
step("all-late is dragging", g["dragging"] is True and g["rushing"] is False,
     f"mean {g['mean_ms']} ms, abs {g['abs_mean_ms']} ms, sd {g['sd_ms']} ms")
g = timing.grid_error([-18.0, -25.0, -31.0, -22.0])
step("all-early is rushing", g["rushing"] is True and g["dragging"] is False, f"mean {g['mean_ms']} ms")
g = timing.grid_error([-20.0, 21.0, -19.0, 18.0])
step("scattered but centred is neither", not g["rushing"] and not g["dragging"],
     f"mean {g['mean_ms']} ms but abs {g['abs_mean_ms']} ms -- bias and accuracy differ")
# The value, not just the flags: abs_mean_ms exists solely to separate accuracy
# from bias, so the case where they disagree has to assert the number itself.
# Without this, dropping the abs() in grid_error passes the whole suite.
step("abs_mean_ms is accuracy, not bias", abs(g["abs_mean_ms"] - 19.5) < 0.05 and g["mean_ms"] == 0.0,
     f"mean {g['mean_ms']} ms, abs {g['abs_mean_ms']} ms (expect 19.5)")

print("9. degenerate input never raises")
try:
    empty = timing.analyze([])
    two = timing.analyze([1.0, 2.0], [])
    one_note = timing.analyze([1.0], [5.0])
    raised = ""
except Exception as exc:  # noqa: BLE001 -- that this cannot happen is the assertion
    empty = two = one_note = {}
    raised = repr(exc)
step("no exception on empty or tiny input", raised == "", raised)
step("empty -> None-shaped", empty["tempo"]["bpm"] is None and empty["drift"]["steady"] is None
     and empty["steadiness"]["cv"] is None and empty["grid"] is None,
     f"tempo n={empty['tempo']['n']}")
step("2 onsets -> None-shaped", two["tempo"]["bpm"] is None and two["steadiness"]["rating"] is None
     and two["grid"]["n"] == 0)
step("1 offset still summarises", one_note["grid"]["mean_ms"] == 5.0
     and one_note["grid"]["sd_ms"] == 0.0)
step("analyze carries all four keys", set(timing.analyze(even, [1.0])) ==
     {"tempo", "drift", "steadiness", "grid"})

print()
print("ALL CHECKS PASSED" if ok else "SOMETHING FAILED")
sys.exit(0 if ok else 1)
