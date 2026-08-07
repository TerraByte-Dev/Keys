"""Playing a score: a transport, not a fire-and-forget button.

The first version scheduled every note on the sequencer and had no way to take them
back, so pressing Play twice played the piece twice, on top of itself. That is the
whole reason this module exists: **anything you can start, you must be able to stop.**

Cancelling is what makes the rest possible. ``fluid_sequencer_remove_events`` filters
by source client, so this player registers its own and only ever flushes its own --
the metronome and the loop station schedule into the same sequencer and none of the
three may cancel another's events. Verified before it was relied on.

Pause, seek and rewind are all the same operation: flush what is queued, then queue
the piece again from a different starting point. There is no position to scrub because
the events are already in the sequencer's queue -- moving means rebuilding it.

**All-notes-off is not optional after a flush.** A scheduled note is a note-on and a
note-off; drop the queue while a note is sounding and its note-off goes with it, and
the note rings forever. That is the bug this file would have if the CC 123 below were
removed, and it would only show up on notes long enough to still be sounding when you
hit pause.
"""

from __future__ import annotations

import ctypes
import threading
from typing import Any

import fluidsynth

from .engine import Engine
from .score import Score

MAX_EVENTS = 8000          # a page-turner, not a symphony renderer
LEAD_IN_MS = 250.0
DEFAULT_BPM = 100.0

_remove_events = fluidsynth.cfunc(
    "fluid_sequencer_remove_events", None,
    ("seq", ctypes.c_void_p, 1),
    ("source", ctypes.c_int, 1),
    ("dest", ctypes.c_int, 1),
    ("type", ctypes.c_int, 1),
)

STOPPED, PLAYING, PAUSED = "stopped", "playing", "paused"


class ScorePlayer:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._lock = threading.Lock()
        self._client: int | None = None

        self.state = STOPPED
        self.score_id = ""
        self.title = ""
        self.bpm = DEFAULT_BPM
        self.total = 0.0            # length in quarter notes
        self._notes: list[tuple[float, float, int]] = []   # (onset, duration, midi)
        self._origin = 0.0          # sequencer tick where `_from` sits
        self._from = 0.0            # quarter note the current run started at
        self._channel = 0

    # ------------------------------------------------------------------ state
    def _ms_per_quarter(self) -> float:
        return 60000.0 / max(20.0, min(300.0, self.bpm))

    def position(self) -> float:
        """Where the playhead is, in quarter notes."""
        if self.state != PLAYING or self.engine.sequencer is None:
            return self._from
        elapsed = self.engine.sequencer.get_tick() - self._origin
        # Clamped at _from, not at zero. During the lead-in `elapsed` is negative, and
        # without this the playhead reads a quarter-note BEHIND where you resumed --
        # so pressing Pause then Play looked like it rewound a little every time.
        return max(self._from, min(self.total, self._from + elapsed / self._ms_per_quarter()))

    def status(self) -> dict[str, Any]:
        at = self.position()
        # The piece running out is not a state anyone sets -- it is just the playhead
        # reaching the end, and the UI has to be told or it shows Playing forever.
        if self.state == PLAYING and self.total and at >= self.total:
            self.state = PAUSED
            self._from = self.total
            at = self.total
        ms = self._ms_per_quarter()
        return {
            "state": self.state,
            "score_id": self.score_id,
            "title": self.title,
            "bpm": round(self.bpm, 2),
            "at": round(at, 4),
            "total": round(self.total, 4),
            "seconds": round(at * ms / 1000.0, 1),
            "total_seconds": round(self.total * ms / 1000.0, 1),
            "notes": len(self._notes),
        }

    # --------------------------------------------------------------- controls
    def load(self, score_id: str, score: Score, title: str = "") -> None:
        """Point the transport at a score, stopping whatever was playing."""
        with self._lock:
            self._silence()
            self.score_id = score_id
            self.title = title or score.title
            self._notes = [(n.onset, n.duration, n.midi) for n in score.notes[:MAX_EVENTS]]
            self.total = score.quarters
            self.bpm = score.tempo or DEFAULT_BPM
            self._from = 0.0
            self.state = STOPPED

    def play(self, at: float | None = None, bpm: float | None = None) -> dict[str, Any]:
        with self._lock:
            if self.engine.fs is None or self.engine.sequencer is None:
                return self.status()
            if bpm:
                self.bpm = max(20.0, min(300.0, float(bpm)))
            start = self._from if at is None else max(0.0, min(self.total, float(at)))
            # Play at the end means play again. status() parks `_from` at `total` when
            # the piece runs out, so a bare play() would schedule nothing -- every onset
            # is behind the start -- and the next status() would flip PLAYING straight
            # back to PAUSED. Silence, and no way out but reloading the score. This is
            # the same guard ghost.js spells `if (model.finished) seek(0)`. Only when the
            # caller named no position: an explicit seek to the end is a seek, not a
            # rewind.
            if at is None and self.total and start >= self.total:
                start = 0.0
            # Unconditional, and the fix for the doubling: whatever is queued goes,
            # whether we think we are playing or not. State can be wrong; the queue
            # is the truth.
            self._silence()
            self._schedule(start)
            self._from = start
            self.state = PLAYING
        return self.status()

    def pause(self) -> dict[str, Any]:
        with self._lock:
            if self.state == PLAYING:
                self._from = self.position()
            self._silence()
            self.state = PAUSED if self._notes else STOPPED
        return self.status()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            self._silence()
            self._from = 0.0
            self.state = STOPPED
        return self.status()

    def seek(self, at: float) -> dict[str, Any]:
        """Move the playhead. Keeps playing if it was, stays put if it was not."""
        with self._lock:
            was = self.state
            self._silence()
            self._from = max(0.0, min(self.total, float(at)))
            if was == PLAYING:
                self._schedule(self._from)
                self.state = PLAYING
        return self.status()

    def set_bpm(self, bpm: float) -> dict[str, Any]:
        """Tempo change. Reschedules, because the queue is in absolute ticks."""
        with self._lock:
            at = self.position()
            playing = self.state == PLAYING
            self.bpm = max(20.0, min(300.0, float(bpm)))
            self._silence()
            self._from = at
            if playing:
                self._schedule(at)
        return self.status()

    # --------------------------------------------------------------- internals
    def _schedule(self, at: float) -> None:
        seq = self.engine.sequencer
        fs = self.engine.fs
        if seq is None or fs is None:
            return
        if self._client is None:
            # Registered lazily and never used for anything but its id: this callback
            # is never invoked, it exists so remove_events has something to filter on.
            self._client = seq.register_client("score", lambda *_a: None)
        self._channel = self.engine.active_channels[0] if self.engine.active_channels else 0
        ms = self._ms_per_quarter()
        self._origin = seq.get_tick() + LEAD_IN_MS
        dest = self.engine.seq_dest
        for onset, duration, midi in self._notes:
            if onset < at:
                continue        # already gone past; seeking must not replay the start
            seq.note(
                int(round(self._origin + (onset - at) * ms)), self._channel, midi, 80,
                max(40, int(round(duration * ms))),
                source=self._client, dest=dest,
            )

    def _silence(self) -> None:
        """Drop our queued events and kill anything still sounding.

        The second half is the part that is easy to forget: a flush takes the pending
        note-OFFs with it, so without CC 123 every note that was sounding at the moment
        you paused would ring until you closed the app.
        """
        seq = self.engine.sequencer
        if seq is not None and _remove_events is not None and self._client is not None:
            _remove_events(seq.sequencer, self._client, -1, -1)
        if self.engine.fs is not None:
            self.engine.fs.cc(self._channel, 123, 0)
