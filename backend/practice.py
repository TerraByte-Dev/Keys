"""The practice clock. Its only job is to make "34 minutes today" honest.

Wall-clock time with the app open is worthless -- it counts the twenty minutes the
laptop sat there while you made coffee. So time is credited **between consecutive
notes**, and only up to a grace window:

    credit = min(gap_since_previous_note, idle_seconds)

A five-second pause to work out a fingering is practice and gets counted. A ten
minute pause is not, and contributes only the grace window. That single line is the
difference between a number worth looking at and a vanity metric.

Sessions start on the first note and close themselves after a long silence, so you
never have to remember to press start or stop. Nothing in here runs on the MIDI
callback thread -- it is driven entirely by the drain loop.
"""

from __future__ import annotations

import time
from typing import Any

from . import config
from .store import Store

# Silence longer than this closes the session. The next note opens a new one.
SESSION_GAP_SECONDS = 300.0
# How often queued note rows get written to SQLite.
FLUSH_SECONDS = 3.0
# Below this, a "session" is someone testing that the sound works. Do not log it.
MIN_SESSION_MS = 5_000


class PracticeClock:
    def __init__(self, store: Store, settings: config.Settings | None = None) -> None:
        self.store = store
        self.settings = settings or config.settings

        self.session_id: int | None = None
        self.active_ms = 0
        self.note_count = 0
        self.preset = ""

        self._t0_perf = 0.0            # perf_counter at session start
        self._last_note: float | None = None   # perf_counter of the most recent note-on
        self._pending: list[tuple[int, int, int]] = []
        self._pending_chords: list[tuple[int, str, int, str, int, int]] = []
        self._last_flush = 0.0
        self._onsets: list[float] = []  # perf_counter note-ons, for the timing analyser
        # What the open session's row already claims. today() counts it, so the live
        # figure has to subtract it back out or the current session gets counted twice.
        self._committed_ms = 0
        self._committed_notes = 0

    # ---------------------------------------------------------------- config
    @property
    def idle_seconds(self) -> float:
        return float(self.settings.get("idle_seconds", default=12) or 12)

    # --------------------------------------------------------------- session
    def start_session(self, preset: str = "", now: float | None = None) -> int:
        self.end_session()
        now = time.perf_counter() if now is None else now
        self.preset = preset or self.preset
        self.session_id = self.store.start_session(self.preset)
        self.active_ms = 0
        self.note_count = 0
        self._t0_perf = now
        self._last_note = None
        self._pending.clear()
        self._pending_chords.clear()
        self._onsets.clear()
        self._last_flush = now
        self._committed_ms = 0
        self._committed_notes = 0
        return self.session_id

    def end_session(self) -> None:
        if self.session_id is None:
            return
        self._flush()
        sid = self.session_id
        active, count = self.active_ms, self.note_count
        self.session_id = None
        self._last_note = None
        if active < MIN_SESSION_MS and count < 20:
            # Not a practice session -- someone checked the sound worked.
            self.store.discard_session(sid)
            return
        self.store.end_session(sid, active, count)

    # ------------------------------------------------------------ the clock
    def on_note(self, t: float, note: int, velocity: int) -> None:
        """One note-on, from the drain loop. `t` is a perf_counter stamp."""
        if self.session_id is None:
            self.start_session(now=t)
        elif self._last_note is not None and (t - self._last_note) > SESSION_GAP_SECONDS:
            self.end_session()
            self.start_session(now=t)

        if self._last_note is not None:
            self.active_ms += int(min(t - self._last_note, self.idle_seconds) * 1000)
        self._last_note = t
        self.note_count += 1
        self._pending.append((int((t - self._t0_perf) * 1000), note, velocity))
        self._onsets.append(t)
        if len(self._onsets) > 4096:
            del self._onsets[:2048]

    def on_chord(self, t: float, chord: dict[str, Any], note_count: int) -> None:
        """Record one settled chord. Called from the drain loop, never the callback.

        Only chords that survived the settle window upstream get here -- rolling a chord
        one note at a time would otherwise log C, then C5, then Cmaj7 on the way to a
        single voicing, and the analytics would be mostly noise.
        """
        if self.session_id is None:
            return
        self._pending_chords.append((
            int((t - self._t0_perf) * 1000),
            str(chord.get("symbol", "")),
            int(chord.get("root_pc", -1)),
            str(chord.get("quality", "")),
            int(chord.get("bass_pc", -1)),
            int(note_count),
        ))

    def tick(self, now: float | None = None) -> None:
        """Called a few times a second by the drain loop. Flushes and closes sessions."""
        now = time.perf_counter() if now is None else now
        if self.session_id is None:
            return
        if now - self._last_flush >= FLUSH_SECONDS:
            self._flush()
            self._committed_ms = self.live_active_ms(now)
            self._committed_notes = self.note_count
            self.store.update_session(self.session_id, self._committed_ms, self._committed_notes)
            self._last_flush = now
        if self._last_note is not None and (now - self._last_note) > SESSION_GAP_SECONDS:
            self.end_session()

    def _flush(self) -> None:
        if self.session_id is not None:
            if self._pending:
                self.store.log_notes(self.session_id, self._pending)
            if self._pending_chords:
                self.store.log_chords(self.session_id, self._pending_chords)
        self._pending.clear()
        self._pending_chords.clear()

    # ---------------------------------------------------------------- output
    def live_active_ms(self, now: float | None = None) -> int:
        """Active time including the tail since the last note, capped at the grace window.

        Counting the tail is what makes the timer tick while you hold a long chord
        instead of freezing until the next attack.
        """
        if self._last_note is None:
            return self.active_ms
        now = time.perf_counter() if now is None else now
        return self.active_ms + int(min(now - self._last_note, self.idle_seconds) * 1000)

    def is_idle(self, now: float | None = None) -> bool:
        if self._last_note is None:
            return True
        now = time.perf_counter() if now is None else now
        return (now - self._last_note) > self.idle_seconds

    def onsets(self) -> list[float]:
        return list(self._onsets)

    def status(self, now: float | None = None) -> dict[str, Any]:
        now = time.perf_counter() if now is None else now
        today = self.store.today()
        streak = self.store.streak()
        live = self.live_active_ms(now)
        return {
            "session_active": self.session_id is not None,
            "session_seconds": live // 1000,
            "session_notes": self.note_count,
            "idle": self.is_idle(now),
            "idle_seconds": self.idle_seconds,
            "seconds_since_note": (
                round(now - self._last_note, 1) if self._last_note is not None else None
            ),
            "preset": self.preset,
            # today() already includes whatever the open session's row last claimed, so
            # swap that stale figure for the live one rather than adding to it.
            "today_seconds": max(0, today["active_seconds"] - self._committed_ms // 1000)
            + (live // 1000 if self.session_id else 0),
            "today_notes": max(0, today["note_count"] - self._committed_notes)
            + (self.note_count if self.session_id else 0),
            "today_sessions": today["sessions"],
            "streak": streak,
        }
