"""The practice log: the only part of this app that deliberately touches the disk.

Three shape decisions, because the obvious version of this file is wrong in each of them:

* **One connection, one lock.** Writes arrive from the asyncio loop thread *and* from
  a background flush thread. The connection is opened ``check_same_thread=False`` and
  every statement runs under a single instance lock. A per-thread connection pool
  would be faster; on a one-machine practice log that speed buys nothing and costs
  the ability to reason about it.

* **A "day" is a local calendar day, computed in Python.** SQLite's ``date()`` reads a
  unix epoch as UTC, so an evening session in this timezone is already filed under
  tomorrow -- which is exactly when this user practices. Every boundary in here comes
  from ``time.mktime`` on a local ``datetime.date``, which also gets DST right: a day
  is not always 86400 seconds, so day arithmetic is done on dates and only then
  converted to epochs.

* **A database error is never fatal.** Every statement goes through ``_rows`` /
  ``_write``, which swallow ``sqlite3.Error``, log it, and hand back an empty result.
  A full disk must degrade the practice history, not stop the piano making sound.

WAL + ``synchronous=NORMAL`` means a hard power cut can lose the last few note events.
That is the right trade here: a fsync on the practice log must never be able to stall
a thread that is about to make a sound.

Nothing in here may be called from the MIDI callback. Notes arrive in batches from the
flush thread -- see CLAUDE.md.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Sequence

from . import config

log = logging.getLogger("keys.store")

# A day counts toward the streak once there is a real minute of playing on it.
STREAK_SECONDS = 60

SCHEMA = """
CREATE TABLE IF NOT EXISTS session (
    id         INTEGER PRIMARY KEY,
    started_at REAL,
    ended_at   REAL,
    active_ms  INTEGER,
    note_count INTEGER,
    preset     TEXT
);
CREATE INDEX IF NOT EXISTS idx_session_started ON session(started_at);

CREATE TABLE IF NOT EXISTS note_event (
    id         INTEGER PRIMARY KEY,
    session_id INTEGER,
    t_ms       INTEGER,
    note       INTEGER,
    velocity   INTEGER
);
-- Every note_event query filters by session (the day window is resolved to a set of
-- session ids first) and then groups by note or velocity, so both indexes cover.
CREATE INDEX IF NOT EXISTS idx_note_session_note ON note_event(session_id, note);
CREATE INDEX IF NOT EXISTS idx_note_session_vel  ON note_event(session_id, velocity);

CREATE TABLE IF NOT EXISTS sightread_attempt (
    id          INTEGER PRIMARY KEY,
    session_id  INTEGER,
    at          REAL,
    note        INTEGER,
    correct     INTEGER,
    reaction_ms INTEGER,
    "key"       TEXT,
    clef        TEXT
);
CREATE INDEX IF NOT EXISTS idx_sight_at   ON sightread_attempt(at);
CREATE INDEX IF NOT EXISTS idx_sight_note ON sightread_attempt(note);
"""


# --- local-day arithmetic ----------------------------------------------------
def day_start(day: date) -> float:
    """Unix epoch of local midnight opening `day`.

    ``tm_isdst=-1`` hands the DST question to the C library instead of guessing, which
    is why the spring-forward day resolves to the right instant rather than to 01:00.
    """
    return time.mktime((day.year, day.month, day.day, 0, 0, 0, 0, 0, -1))


def local_day(ts: float) -> date:
    return date.fromtimestamp(ts)


def _window(days: int) -> tuple[float, float, date, date]:
    """Half-open epoch range covering the last `days` local days, ending today.

    days=1 is today alone. Returns (start, end, first_day, today).
    """
    today = date.today()
    first = today - timedelta(days=max(1, int(days)) - 1)
    return day_start(first), day_start(today + timedelta(days=1)), first, today


class Store:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else config.DB_PATH
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(SCHEMA)
            conn.commit()
            self._conn = conn
        except (sqlite3.Error, OSError) as exc:
            # No database is a survivable state: every method below no-ops.
            log.warning("store: cannot open %s (%s) -- practice history disabled", self.path, exc)

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except sqlite3.Error:
                    pass
                self._conn = None

    # ------------------------------------------------------------- plumbing
    def _rows(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            # Read the handle *inside* the lock: close() may have landed between the
            # caller deciding to write and this thread getting the lock.
            conn = self._conn
            if conn is None:
                return []
            try:
                return conn.execute(sql, params).fetchall()
            except sqlite3.Error as exc:
                log.warning("store: query failed (%s): %s", exc, sql.strip().split("\n")[0])
                return []

    def _write(self, sql: str, params: Sequence[Any] = (), many: bool = False) -> int:
        """Run one statement in one transaction. Returns lastrowid, or -1 on failure."""
        with self._lock:
            # Same reason as _rows: read the handle inside the lock, and keep using
            # that local. Reaching for self._conn again in the error path is how the
            # rollback ends up on a connection that close() already dropped.
            conn = self._conn
            if conn is None:
                return -1
            cur = None
            try:
                cur = conn.executemany(sql, params) if many else conn.execute(sql, params)
                conn.commit()
                rowid = cur.lastrowid
                return rowid if rowid is not None else 0
            except sqlite3.Error as exc:
                log.warning("store: write failed (%s): %s", exc, sql.strip().split("\n")[0])
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                return -1
            finally:
                # Close the cursor while we still hold the lock. Letting it die with the
                # frame frees it *after* the lock is released, and sqlite3's per-connection
                # statement cache is not thread-safe: one thread finalising its statement
                # while another is mid-executemany raises InterfaceError("bad parameter or
                # other API misuse"), which _write then swallows -- silently losing a whole
                # batch of note events. Measured: 13 of 25 four-thread runs lost rows
                # without this close, 0 of 25 with it.
                if cur is not None:
                    cur.close()

    def _session_ids_sql(self) -> str:
        return "SELECT id FROM session WHERE started_at >= ? AND started_at < ?"

    # ------------------------------------------------------------- sessions
    def start_session(self, preset: str) -> int:
        # Epoch seconds, not perf_counter: these have to survive a reboot and mean
        # something on a calendar.
        return self._write(
            "INSERT INTO session(started_at, ended_at, active_ms, note_count, preset) "
            "VALUES (?, NULL, 0, 0, ?)",
            (time.time(), preset),
        )

    def update_session(self, sid: int, active_ms: int, note_count: int) -> None:
        self._write(
            "UPDATE session SET active_ms = ?, note_count = ? WHERE id = ?",
            (int(active_ms), int(note_count), int(sid)),
        )

    def end_session(self, sid: int, active_ms: int, note_count: int) -> None:
        self._write(
            "UPDATE session SET ended_at = ?, active_ms = ?, note_count = ? WHERE id = ?",
            (time.time(), int(active_ms), int(note_count), int(sid)),
        )

    def discard_session(self, sid: int) -> None:
        """Delete a session and everything hanging off it.

        Sessions open on the first note, so pressing one key to check the sound is
        working creates a row. Those would inflate the session count and clutter the
        history, so a session too short and too quiet to be practice is removed rather
        than closed. Only ever called for sessions the practice clock rejected.
        """
        self._write("DELETE FROM note_event WHERE session_id = ?", (int(sid),))
        self._write("DELETE FROM sightread_attempt WHERE session_id = ?", (int(sid),))
        self._write("DELETE FROM session WHERE id = ?", (int(sid),))

    def log_notes(self, sid: int, rows: list[tuple[int, int, int]]) -> None:
        """Append a batch of (t_ms, note, velocity). t_ms is since session start."""
        if not rows:
            return
        self._write(
            "INSERT INTO note_event(session_id, t_ms, note, velocity) VALUES (?, ?, ?, ?)",
            [(sid, t, n, v) for t, n, v in rows],
            many=True,
        )

    def log_sightread(self, sid: int, note: int, correct: bool,
                      reaction_ms: int, key: str, clef: str) -> None:
        self._write(
            'INSERT INTO sightread_attempt(session_id, at, note, correct, reaction_ms, "key", clef) '
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sid, time.time(), int(note), 1 if correct else 0, int(reaction_ms), key, clef),
        )

    # -------------------------------------------------------------- practice
    def today(self) -> dict:
        start, end, _first, _today = _window(1)
        rows = self._rows(
            "SELECT COUNT(*) AS n, "
            "       COALESCE(SUM(active_ms), 0) AS ms, "
            "       COALESCE(SUM(note_count), 0) AS notes, "
            "       MIN(started_at) AS first_at, "
            "       MAX(COALESCE(ended_at, started_at)) AS last_at "
            "FROM session WHERE started_at >= ? AND started_at < ?",
            (start, end),
        )
        r = rows[0] if rows else None
        if r is None:
            return {"active_seconds": 0, "note_count": 0, "sessions": 0,
                    "first_at": None, "last_at": None}
        return {
            "active_seconds": int(round(r["ms"] / 1000.0)),
            "note_count": int(r["notes"]),
            "sessions": int(r["n"]),
            "first_at": r["first_at"],
            "last_at": r["last_at"],
        }

    def history(self, days: int = 30) -> list[dict]:
        """One row per local day, oldest first, empty days included as zeros.

        The zero days are the point: the heatmap needs to draw the gaps.
        """
        start, end, first, today = _window(days)
        buckets: dict[date, list[int]] = {}
        for r in self._rows(
            "SELECT started_at, active_ms, note_count FROM session "
            "WHERE started_at >= ? AND started_at < ?",
            (start, end),
        ):
            b = buckets.setdefault(local_day(r["started_at"]), [0, 0, 0])
            b[0] += int(r["active_ms"] or 0)
            b[1] += int(r["note_count"] or 0)
            b[2] += 1

        out: list[dict] = []
        day = first
        while day <= today:
            ms, notes, n = buckets.get(day, (0, 0, 0))
            out.append({
                "date": day.isoformat(),
                "active_seconds": int(round(ms / 1000.0)),
                "note_count": notes,
                "sessions": n,
            })
            day += timedelta(days=1)
        return out

    def streak(self) -> dict:
        """Consecutive local days with at least STREAK_SECONDS of active practice.

        Scans every session -- a few thousand rows after years of use, and the longest
        streak is not answerable from a window anyway.
        """
        seconds: dict[date, float] = {}
        for r in self._rows("SELECT started_at, active_ms FROM session"):
            d = local_day(r["started_at"])
            seconds[d] = seconds.get(d, 0.0) + (r["active_ms"] or 0) / 1000.0
        qualifying = {d for d, s in seconds.items() if s >= STREAK_SECONDS}

        today = date.today()
        practiced_today = today in qualifying

        # Today has not happened yet for most of the day. A streak that ended
        # yesterday is still alive, so anchor to yesterday when today is still empty.
        current = 0
        cursor = today if practiced_today else today - timedelta(days=1)
        while cursor in qualifying:
            current += 1
            cursor -= timedelta(days=1)

        longest = 0
        run = 0
        previous: date | None = None
        for d in sorted(qualifying):
            run = run + 1 if previous is not None and d - previous == timedelta(days=1) else 1
            longest = max(longest, run)
            previous = d

        return {"current": current, "longest": longest, "practiced_today": practiced_today}

    # ----------------------------------------------------------- note stats
    def note_heatmap(self, days: int = 30) -> dict[int, int]:
        start, end, _f, _t = _window(days)
        rows = self._rows(
            "SELECT note, COUNT(*) AS n FROM note_event "
            f"WHERE session_id IN ({self._session_ids_sql()}) "
            "AND note BETWEEN ? AND ? GROUP BY note",
            (start, end, config.LOW_KEY, config.HIGH_KEY),
        )
        return {int(r["note"]): int(r["n"]) for r in rows}

    def velocity_histogram(self, days: int = 30, buckets: int = 16) -> list[int]:
        buckets = max(1, int(buckets))
        start, end, _f, _t = _window(days)
        out = [0] * buckets
        for r in self._rows(
            "SELECT velocity, COUNT(*) AS n FROM note_event "
            f"WHERE session_id IN ({self._session_ids_sql()}) "
            "AND velocity BETWEEN 1 AND 127 GROUP BY velocity",
            (start, end),
        ):
            out[(int(r["velocity"]) - 1) * buckets // 127] += int(r["n"])
        return out

    def velocity_distinct(self, days: int = 1) -> int:
        """How many different velocities the piano actually sent.

        1 means the P-71B is in its shipped "Fixed" Touch Sensitivity mode, where every
        key sends velocity 64 no matter how it is struck. Worth telling the user about:
        no velocity curve in the app can do anything until they change it on the piano.
        """
        start, end, _f, _t = _window(days)
        rows = self._rows(
            "SELECT COUNT(DISTINCT velocity) AS n FROM note_event "
            f"WHERE session_id IN ({self._session_ids_sql()})",
            (start, end),
        )
        return int(rows[0]["n"]) if rows else 0

    # ------------------------------------------------------------- sightread
    def weak_notes(self, limit: int = 12) -> list[dict]:
        """Worst correct-rate first. Three attempts minimum, so one fluke is not a verdict."""
        rows = self._rows(
            "SELECT note, COUNT(*) AS attempts, SUM(correct) AS correct, "
            "       AVG(reaction_ms) AS reaction "
            "FROM sightread_attempt GROUP BY note HAVING COUNT(*) >= 3 "
            # Ties broken by attempt count: more evidence of being bad is worse.
            "ORDER BY (CAST(SUM(correct) AS REAL) / COUNT(*)) ASC, COUNT(*) DESC LIMIT ?",
            (int(limit),),
        )
        return [{
            "note": int(r["note"]),
            "attempts": int(r["attempts"]),
            "correct": int(r["correct"]),
            "accuracy": round(int(r["correct"]) / int(r["attempts"]), 2),
            # Averaged over every attempt, right or wrong -- hesitating and then
            # getting it wrong is exactly the signal this is meant to surface.
            "mean_reaction_ms": int(round(r["reaction"] or 0)),
        } for r in rows]

    def sightread_summary(self, days: int = 30) -> dict:
        start, end, _f, _t = _window(days)
        rows = self._rows(
            "SELECT COUNT(*) AS attempts, COALESCE(SUM(correct), 0) AS correct, "
            "       AVG(reaction_ms) AS reaction "
            "FROM sightread_attempt WHERE at >= ? AND at < ?",
            (start, end),
        )
        r = rows[0] if rows else None
        attempts = int(r["attempts"]) if r else 0
        correct = int(r["correct"]) if r else 0
        return {
            "attempts": attempts,
            "correct": correct,
            "accuracy": round(correct / attempts, 3) if attempts else 0.0,
            "mean_reaction_ms": int(round(r["reaction"] or 0)) if attempts else 0,
        }

    # --------------------------------------------------------------- listing
    def recent_sessions(self, limit: int = 20) -> list[dict]:
        rows = self._rows(
            "SELECT id, started_at, ended_at, active_ms, note_count, preset FROM session "
            "ORDER BY started_at DESC LIMIT ?",
            (int(limit),),
        )
        return [{
            "id": int(r["id"]),
            "started_at": r["started_at"],
            "ended_at": r["ended_at"],
            "active_ms": int(r["active_ms"] or 0),
            "note_count": int(r["note_count"] or 0),
            "preset": r["preset"],
            # Resolved here for the same reason as everywhere else in this module:
            # the caller cannot get the local day right from the epoch alone.
            "date": local_day(r["started_at"]).isoformat(),
        } for r in rows]
