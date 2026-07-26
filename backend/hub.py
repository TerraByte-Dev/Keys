"""The one-way street between the MIDI callback thread and everything else.

The callback may only ever `append` to a bounded deque. It never reads it, never
locks, never serialises, never touches a socket. Everything downstream -- the
websocket feed, the practice clock, the sight-reading grader, the database --
runs off the drain, on the asyncio loop, where being slow is merely ugly.

`deque(maxlen=N)` is the whole safety mechanism: when the UI stalls, appends keep
succeeding and the oldest events fall off the back. Dropped frames, not audio
glitches. The 1 Hz status heartbeat carries the engine's authoritative held-note
set, so a UI that missed a note-off gets corrected within a second.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Any

# Event kinds. Ints, not strings -- the hot path should not build objects.
NOTE_ON = 0
NOTE_OFF = 1
CONTROL = 2
BEND = 3

QUEUE_LIMIT = 4096
SERVICE_SAMPLES = 512


class Hub:
    """Bounded event queue plus the only latency number we can honestly measure."""

    def __init__(self, maxlen: int = QUEUE_LIMIT) -> None:
        self.q: deque[tuple[float, int, int, int, float]] = deque(maxlen=maxlen)
        # Callback-entry -> synth-call-returned, in seconds. This is NOT end-to-end
        # latency and must never be presented as such: it excludes USB, the driver,
        # the 3 ms audio buffer, DMA, the DAC and the air. Nothing in software can
        # measure the real number (see docs/HARDWARE.md section 3).
        self.service: deque[float] = deque(maxlen=SERVICE_SAMPLES)
        self.total = 0
        self.dropped_estimate = 0
        self._maxlen = maxlen

    # ------------------------------------------------------------- HOT PATH
    def push(self, t: float, kind: int, a: int, b: int, service: float) -> None:
        q = self.q
        if len(q) == self._maxlen:
            self.dropped_estimate += 1
        q.append((t, kind, a, b, service))
        self.service.append(service)
        self.total += 1

    # ---------------------------------------------------------------- drain
    def drain(self) -> list[tuple[float, int, int, int, float]]:
        """Pop everything currently queued. Called from the asyncio loop only."""
        q = self.q
        out = []
        popleft = q.popleft
        for _ in range(len(q)):
            try:
                out.append(popleft())
            except IndexError:  # pragma: no cover -- single consumer, cannot happen
                break
        return out

    def latency_stats(self) -> dict[str, Any]:
        samples = sorted(self.service)
        if not samples:
            return {"n": 0}
        n = len(samples)
        return {
            "n": n,
            "median_us": round(samples[n // 2] * 1e6, 1),
            "p95_us": round(samples[min(n - 1, int(n * 0.95))] * 1e6, 1),
            "max_us": round(samples[-1] * 1e6, 1),
            "note": "MIDI callback -> synth call only. Excludes USB, buffer, DAC, air.",
        }

    def stats(self) -> dict[str, Any]:
        return {
            "events_total": self.total,
            "queue_depth": len(self.q),
            "queue_limit": self._maxlen,
            "dropped": self.dropped_estimate,
            "latency": self.latency_stats(),
        }


def now() -> float:
    return time.perf_counter()
