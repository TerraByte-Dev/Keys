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
flush thread -- see docs/ARCHITECTURE.md.
"""

from __future__ import annotations

import json
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

# Two note-ons closer together than this were one gesture, not a melodic step. The
# chord detector upstream settles on roughly the same window, and a human cannot
# strike two keys deliberately in sequence anywhere near this fast.
CHORD_WINDOW_MS = 40

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

CREATE TABLE IF NOT EXISTS chord_event (
    id         INTEGER PRIMARY KEY,
    session_id INTEGER,
    t_ms       INTEGER,
    symbol     TEXT,
    root_pc    INTEGER,
    quality    TEXT,
    bass_pc    INTEGER,
    note_count INTEGER
);
-- Same access shape as note_event: the day window resolves to session ids, then the
-- rows group by symbol (top_chords) or by quality (chord_qualities).
CREATE INDEX IF NOT EXISTS idx_chord_session_sym  ON chord_event(session_id, symbol);
CREATE INDEX IF NOT EXISTS idx_chord_session_qual ON chord_event(session_id, quality);

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

-- One row per exercise run, whatever the exercise was: a scale, a cadence, a
-- sight-reading page. The columns are the FACTS the grader measured and nothing
-- else -- there is deliberately no "clean" column even though metrics.grade()
-- computes one. A verdict written into a row is frozen at the thresholds that
-- were current the day it was written; computed at query time from correct/steps
-- and evenness_cv, every past attempt re-reads itself when the bar moves. Same
-- reasoning that keeps inferred key out of note_event.
CREATE TABLE IF NOT EXISTS exercise_attempt (
    id          INTEGER PRIMARY KEY,
    session_id  INTEGER,
    at          REAL,
    exercise    TEXT,
    variant     TEXT,
    "key"       TEXT,
    bpm         REAL,
    steps       INTEGER,
    correct     INTEGER,
    evenness_cv REAL,
    ioi_mean_ms REAL,
    grid_abs_ms REAL,
    grid_mean_ms REAL,
    vel_sd      REAL,
    crossing_ms REAL,
    sync_ms     REAL,
    duration_ms INTEGER,
    params      TEXT
);
-- Everything that reads this table either asks about one (exercise, variant) --
-- history and the personal-best tempo -- or sweeps a time window. The compound
-- index carries `at` as its third column so the per-variant queries get their
-- ordering out of the index rather than a sort.
CREATE INDEX IF NOT EXISTS idx_exattempt_variant ON exercise_attempt(exercise, variant, at);
CREATE INDEX IF NOT EXISTS idx_exattempt_at      ON exercise_attempt(at);

CREATE TABLE IF NOT EXISTS exercise_step (
    id          INTEGER PRIMARY KEY,
    attempt_id  INTEGER,
    idx         INTEGER,
    note        INTEGER,
    played      INTEGER,
    correct     INTEGER,
    reaction_ms INTEGER,
    offset_ms   REAL,
    spread_ms   REAL,
    finger      INTEGER,
    crossing    INTEGER
);
-- attempt_id for the cascade in discard_session; note for weak_steps, which is
-- the only query that reaches into these rows without an attempt in hand.
CREATE INDEX IF NOT EXISTS idx_exstep_attempt ON exercise_step(attempt_id);
CREATE INDEX IF NOT EXISTS idx_exstep_note    ON exercise_step(note);
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


# --- labels -------------------------------------------------------------------
# These three tables restate what backend.music already knows, on purpose: nothing in
# the practice log may depend on the theory module. store.py is the one file that must
# still open and answer questions when the rest of the app is broken or being rewritten,
# and a name on a chart is not worth an import edge to a 400-line module.

# Sharp spellings, because a range readout has no key to spell against -- and unlike a
# staff, "A#2" on a bar label is never mistaken for the wrong key.
_NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

# (degree, quality) per semitone step, the same construction music.interval_name uses.
_INTERVAL_STEPS: tuple[tuple[int, str], ...] = (
    (1, "P"), (2, "m"), (2, "M"), (3, "m"), (3, "M"), (4, "P"),
    (4, "A"), (5, "P"), (6, "m"), (6, "M"), (7, "m"), (7, "M"),
)

# Chord quality as detect_chord stores it (the symbol suffix, so a plain major triad
# is the empty string) -> something that can go on an axis. An unknown suffix is
# passed through rather than dropped: a label nobody recognises beats a silent zero.
_QUALITY_LABELS: dict[str, str] = {
    "": "major", "m": "minor", "dim": "diminished", "aug": "augmented",
    "sus4": "sus4", "sus2": "sus2",
    "maj7": "major 7th", "7": "dominant 7th", "m7": "minor 7th",
    "m7b5": "half-diminished", "dim7": "diminished 7th", "m(maj7)": "minor major 7th",
    "7sus4": "dominant 7sus4", "6": "major 6th", "m6": "minor 6th",
    "add9": "add9", "madd9": "minor add9",
    "9": "dominant 9th", "maj9": "major 9th", "m9": "minor 9th", "69": "6/9",
    "11": "dominant 11th", "m11": "minor 11th", "13": "dominant 13th",
}


def _note_label(midi: int) -> str:
    """Scientific pitch notation: midi 60 is C4."""
    return f"{_NOTE_NAMES[midi % 12]}{midi // 12 - 1}"


def _interval_label(semitones: int) -> str:
    """Short interval name. Six semitones is a plain tritone -- see music.interval_name."""
    if semitones == 6:
        return "TT"
    octaves, step = divmod(semitones, 12)
    if step == 0:
        return f"P{1 + 7 * octaves}"
    degree, quality = _INTERVAL_STEPS[step]
    return f"{quality}{degree + 7 * octaves}"


# --- optional numbers ---------------------------------------------------------
# Half of what metrics.grade() reports is legitimately None: there is no drift on
# four notes, no grid on an untimed run, no velocity spread on a fixed-touch piano.
# NULL is the honest storage for "not measurable", and `float(None)` raises, so the
# guard happens once here instead of at each of the seventeen call sites.
def _opt_float(v: Any) -> float | None:
    return float(v) if v is not None else None


def _opt_int(v: Any) -> int | None:
    return int(round(float(v))) if v is not None else None


def _accuracy(correct: Any, steps: Any) -> float:
    steps = int(steps or 0)
    return round(int(correct or 0) / steps, 4) if steps else 0.0


def _attempt_row(r: sqlite3.Row) -> dict:
    """One attempt as the shelf wants to read it. Shared so recent_variants and the
    summary's `recent` cannot drift into two different shapes."""
    return {
        "exercise": r["exercise"] or "",
        "variant": r["variant"] or "",
        "title": _params_title(r["params"]),
        "at": r["at"],
        "accuracy": _accuracy(r["correct"], r["steps"]),
    }


def _params_title(blob: str | None) -> str:
    """The human title back out of the params blob. A row whose JSON will not parse
    still deserves to appear on the shelf, just without its title."""
    try:
        params = json.loads(blob) if blob else {}
    except (TypeError, ValueError):
        return ""
    return str(params.get("title") or "") if isinstance(params, dict) else ""


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

    # ------------------------------------------------------- your data, your call
    def inventory(self) -> dict[str, Any]:
        from . import userdata
        with self._lock:
            return userdata.inventory(self._conn)

    def wipe(self, what: str, settings: Any) -> dict[str, Any]:
        """Delete one category of your data. Held under the same lock as every
        other write, so a reset cannot land halfway through a note being logged."""
        from . import userdata
        with self._lock:
            return userdata.reset(self._conn, what, settings)

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
        self._write("DELETE FROM chord_event WHERE session_id = ?", (int(sid),))
        self._write("DELETE FROM sightread_attempt WHERE session_id = ?", (int(sid),))
        # Steps before attempts: exercise_step hangs off the attempt, not the session,
        # so once the attempt rows are gone there is nothing left to find them by and
        # they orphan silently -- they would still be counted by weak_steps forever.
        self._write(
            "DELETE FROM exercise_step WHERE attempt_id IN "
            "(SELECT id FROM exercise_attempt WHERE session_id = ?)",
            (int(sid),),
        )
        self._write("DELETE FROM exercise_attempt WHERE session_id = ?", (int(sid),))
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

    def log_chords(self, sid: int, rows: list[tuple[int, str, int, str, int, int]]) -> None:
        """Append a batch of (t_ms, symbol, root_pc, quality, bass_pc, note_count).

        One row per chord the detector settled on, not per note-on -- see
        PracticeClock.on_chord for why the rolled-in voicings never get here.
        """
        if not rows:
            return
        self._write(
            "INSERT INTO chord_event(session_id, t_ms, symbol, root_pc, quality, "
            "bass_pc, note_count) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(sid, t, sym, root, qual, bass, n) for t, sym, root, qual, bass, n in rows],
            many=True,
        )

    def log_sightread(self, sid: int, note: int, correct: bool,
                      reaction_ms: int, key: str, clef: str) -> None:
        self._write(
            'INSERT INTO sightread_attempt(session_id, at, note, correct, reaction_ms, "key", clef) '
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sid, time.time(), int(note), 1 if correct else 0, int(reaction_ms), key, clef),
        )

    def log_exercise(self, session_id: int | None, summary: dict, steps: list[dict]) -> int:
        """One graded run: the metrics.grade() summary, then its per-step records.

        Two statements rather than one transaction spanning both, because that is what
        _write offers and a second connection-level primitive for one caller is not
        worth it. The failure it allows is an attempt with no steps -- a run whose
        detail is missing, which every query below already tolerates. The steps do go
        in as a single executemany: a two-octave scale is 30 rows and 30 commits on a
        WAL database is 30 chances to stall a thread that is about to make a sound.

        Returns the attempt id, or -1 if the write failed.
        """
        params = dict(summary.get("params") or {})
        # The shelf shows "C major, 2 octaves, hands together"; a variant slug cannot be
        # un-slugged back into that, and recent_variants has to print it. It rides in
        # the params blob rather than earning a column: it is display text that nothing
        # ever filters or groups by.
        params["title"] = str(summary.get("title") or "")

        attempt_id = self._write(
            'INSERT INTO exercise_attempt(session_id, at, exercise, variant, "key", bpm, '
            "steps, correct, evenness_cv, ioi_mean_ms, grid_abs_ms, grid_mean_ms, vel_sd, "
            "crossing_ms, sync_ms, duration_ms, params) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, time.time(),
             str(summary.get("exercise") or ""), str(summary.get("variant") or ""),
             str(summary.get("key") or ""), _opt_float(summary.get("bpm")),
             int(summary.get("steps") or 0), int(summary.get("correct") or 0),
             _opt_float(summary.get("evenness_cv")), _opt_float(summary.get("ioi_mean_ms")),
             _opt_float(summary.get("grid_abs_ms")), _opt_float(summary.get("grid_mean_ms")),
             _opt_float(summary.get("vel_sd")), _opt_float(summary.get("crossing_ms")),
             _opt_float(summary.get("sync_ms")), _opt_int(summary.get("duration_ms")),
             json.dumps(params, default=str)),
        )
        if attempt_id < 0 or not steps:
            return attempt_id

        self._write(
            "INSERT INTO exercise_step(attempt_id, idx, note, played, correct, "
            "reaction_ms, offset_ms, spread_ms, finger, crossing) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(attempt_id, int(s.get("idx", 0)), int(s.get("note", -1)),
              int(s.get("played", -1)), 1 if s.get("correct") else 0,
              _opt_int(s.get("reaction_ms")), _opt_float(s.get("offset_ms")),
              _opt_float(s.get("spread_ms")), int(s.get("finger") or 0),
              1 if s.get("crossing") else 0) for s in steps],
            many=True,
        )
        return attempt_id

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

    def year(self, y: int) -> dict:
        """One row per local day of calendar year `y`, oldest first, zeros included.

        Separate from history() because a calendar year is not a rolling window: the
        Activity chart answers "how has this year gone", which needs a fixed 1 January
        to 31 December grid whose width does not change as the year passes.

        Days after today are returned with `future: True` rather than omitted, so the
        chart can draw the shape of the whole year and leave the rest blank instead of
        redrawing itself every day.
        """
        first = date(y, 1, 1)
        last = date(y, 12, 31)
        today = date.today()
        buckets: dict[date, list[int]] = {}
        for r in self._rows(
            "SELECT started_at, active_ms, note_count FROM session "
            "WHERE started_at >= ? AND started_at < ?",
            (day_start(first), day_start(last + timedelta(days=1))),
        ):
            b = buckets.setdefault(local_day(r["started_at"]), [0, 0, 0])
            b[0] += int(r["active_ms"] or 0)
            b[1] += int(r["note_count"] or 0)
            b[2] += 1

        days: list[dict] = []
        day = first
        while day <= last:
            ms, notes, n = buckets.get(day, (0, 0, 0))
            days.append({
                "date": day.isoformat(),
                "active_seconds": int(round(ms / 1000.0)),
                "note_count": notes,
                "sessions": n,
                "future": day > today,
            })
            day += timedelta(days=1)
        played = [d for d in days if d["active_seconds"]]
        return {
            "year": y,
            "days": days,
            "days_played": len(played),
            "active_seconds": sum(d["active_seconds"] for d in played),
            "note_count": sum(d["note_count"] for d in played),
        }

    def years(self) -> list[int]:
        """Every calendar year with a session in it, plus this one. Ascending.

        This year is always included so the arrows have somewhere to land on a fresh
        install, where the honest answer is an empty grid rather than no grid.
        """
        found = {date.today().year}
        for r in self._rows("SELECT started_at FROM session", ()):
            found.add(local_day(r["started_at"]).year)
        return sorted(found)

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

        # total_days uses the same one-real-minute bar as the streak itself, so
        # "5 day streak, 130 days total" is two readings of one rule. The all-time
        # seconds are free here -- every session row is already in hand.
        return {"current": current, "longest": longest, "practiced_today": practiced_today,
                "total_days": len(qualifying),
                "total_active_seconds": int(round(sum(seconds.values())))}

    # ----------------------------------------------------------- note stats
    def note_heatmap(self, days: int = 30) -> dict[int, int]:
        """Every key you struck and how often, over the window.

        The filter is all of MIDI on purpose. It used to be the 88 keys, which was the
        right guard while the instrument was a constant and became a quiet act of
        vandalism once it was not: declaring a 61-key controller would have deleted a
        year of A0s from your own history, in a chart, with no way to tell it had
        happened. What was recorded was played. The renderer decides what to draw.
        """
        start, end, _f, _t = _window(days)
        rows = self._rows(
            "SELECT note, COUNT(*) AS n FROM note_event "
            f"WHERE session_id IN ({self._session_ids_sql()}) "
            "AND note BETWEEN 0 AND 127 GROUP BY note",
            (start, end),
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

    # ---------------------------------------------------------- chord stats
    def top_chords(self, days: int = 365, limit: int = 20) -> list[dict]:
        """Most-played chord symbols, commonest first."""
        start, end, _f, _t = _window(days)
        rows = self._rows(
            # The symbol already encodes root, quality and bass, so each group holds
            # exactly one root_pc and one quality and MIN() just reads it back.
            "SELECT symbol, COUNT(*) AS n, MIN(root_pc) AS root_pc, MIN(quality) AS quality "
            "FROM chord_event "
            f"WHERE session_id IN ({self._session_ids_sql()}) "
            # Ties broken alphabetically so the chart does not reshuffle between loads.
            "GROUP BY symbol ORDER BY n DESC, symbol ASC LIMIT ?",
            (start, end, int(limit)),
        )
        return [{
            "symbol": r["symbol"],
            "count": int(r["n"]),
            "quality": r["quality"] or "",
            "root_pc": int(r["root_pc"]) if r["root_pc"] is not None else -1,
        } for r in rows]

    def chord_qualities(self, days: int = 365) -> list[dict]:
        """What kind of chords, by readable name. "major" is what an empty suffix means.

        The mapping happens in Python rather than SQL because two suffixes can share a
        label, and merging them afterwards is one dict where a CASE expression is
        twenty-four branches that have to stay in step with the theory module.
        """
        start, end, _f, _t = _window(days)
        counts: dict[str, int] = {}
        for r in self._rows(
            "SELECT quality, COUNT(*) AS n FROM chord_event "
            f"WHERE session_id IN ({self._session_ids_sql()}) GROUP BY quality",
            (start, end),
        ):
            raw = r["quality"] or ""
            label = _QUALITY_LABELS.get(raw, raw)
            counts[label] = counts.get(label, 0) + int(r["n"])
        return [{"quality": q, "count": n}
                for q, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]

    # ------------------------------------------------------------ what was played
    def pitch_class_histogram(self, days: int = 365) -> list[int]:
        """12 counts, index 0 = C. Octave-blind, which is what key inference wants."""
        start, end, _f, _t = _window(days)
        out = [0] * 12
        for r in self._rows(
            "SELECT note % 12 AS pc, COUNT(*) AS n FROM note_event "
            f"WHERE session_id IN ({self._session_ids_sql()}) GROUP BY pc",
            (start, end),
        ):
            out[int(r["pc"])] += int(r["n"])
        return out

    def octave_histogram(self, days: int = 365) -> dict[int, int]:
        """Scientific octave number -> note count. Middle C (60) is octave 4."""
        start, end, _f, _t = _window(days)
        rows = self._rows(
            "SELECT note / 12 - 1 AS oct, COUNT(*) AS n FROM note_event "
            f"WHERE session_id IN ({self._session_ids_sql()}) GROUP BY oct",
            (start, end),
        )
        return {int(r["oct"]): int(r["n"]) for r in rows}

    def range_used(self, days: int = 365) -> dict:
        """Lowest and highest key touched, and what fraction of your keyboard they span.

        Two ranges, on purpose, because they answer different questions. What you PLAYED
        is history and is read out of all of MIDI -- narrowing your declared keyboard
        must not retroactively un-play a note. What you played it ON is the instrument
        you have now, and it is the denominator, because "how much of my keyboard do I
        use" is the only reading of coverage anyone wants.

        The consequence is that coverage can exceed 1.0 if you once played a wider
        keyboard than you now declare. That is true rather than broken -- it says you
        have used more range than you currently own -- and the caller caps the bar.
        """
        start, end, _f, _t = _window(days)
        rows = self._rows(
            "SELECT MIN(note) AS lo, MAX(note) AS hi FROM note_event "
            f"WHERE session_id IN ({self._session_ids_sql()}) AND note BETWEEN 0 AND 127",
            (start, end),
        )
        low_key, high_key = config.instrument_range()
        r = rows[0] if rows else None
        if r is None or r["lo"] is None:
            return {"low": None, "high": None, "span": 0, "coverage": 0.0,
                    "of_low": low_key, "of_high": high_key,
                    "low_name": None, "high_name": None}
        low, high = int(r["lo"]), int(r["hi"])
        span = high - low
        return {
            "low": low,
            "high": high,
            "span": span,
            # Steps, not keys: A0 to C8 is 88 keys but 87 steps between them. max(1,..)
            # rather than trusting the clamp, because this is a division and the cost of
            # being wrong is a 500 on the whole Stats page.
            "coverage": round(span / max(1, high_key - low_key), 3),
            # What the denominator was, so the label can say so instead of guessing.
            "of_low": low_key,
            "of_high": high_key,
            "low_name": _note_label(low),
            "high_name": _note_label(high),
        }

    def interval_histogram(self, days: int = 365, limit: int = 12) -> list[dict]:
        """Melodic steps between consecutive notes, commonest first.

        Two notes struck within CHORD_WINDOW_MS of each other are one gesture, so the
        pair between them is skipped rather than counted as a leap -- otherwise every
        triad would add a third and a fifth to the melodic histogram and drown the
        actual melody. Unisons and anything wider than two octaves are dropped too: a
        repeated note is not a step, and a two-octave jump is a hand moving, not a line.
        """
        start, end, _f, _t = _window(days)
        counts: dict[int, int] = {}
        prev_session: int | None = None
        prev_t = 0
        prev_note = 0
        for r in self._rows(
            "SELECT session_id, t_ms, note FROM note_event "
            f"WHERE session_id IN ({self._session_ids_sql()}) "
            # id last: within one t_ms it preserves the order the notes actually
            # arrived, which is the order the flush thread wrote them in.
            "ORDER BY session_id, t_ms, id",
            (start, end),
        ):
            sid, t_ms, note = int(r["session_id"]), int(r["t_ms"]), int(r["note"])
            if sid == prev_session and t_ms - prev_t >= CHORD_WINDOW_MS:
                step = abs(note - prev_note)
                if 0 < step <= 24:
                    counts[step] = counts.get(step, 0) + 1
            prev_session, prev_t, prev_note = sid, t_ms, note
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:max(0, int(limit))]
        return [{"semitones": s, "count": n, "name": _interval_label(s)} for s, n in ranked]

    # ------------------------------------------------------------ when it happened
    def hour_histogram(self, days: int = 365) -> list[int]:
        """Active seconds per local hour of day, index 0 = midnight.

        Seconds, not the milliseconds they are stored in, so this reads on the same
        scale as weekday_histogram. The hour comes from time.localtime for the reason
        day_start exists: an epoch is not a wall clock.

        A session is filed entirely under the hour it started in. Splitting a 40-minute
        session across two hours would be more accurate and would need the per-note
        timeline to do honestly; the question this answers is "when do you sit down".
        """
        start, end, _f, _t = _window(days)
        ms = [0] * 24
        for r in self._rows(
            "SELECT started_at, active_ms FROM session "
            "WHERE started_at >= ? AND started_at < ?",
            (start, end),
        ):
            ms[time.localtime(r["started_at"]).tm_hour] += int(r["active_ms"] or 0)
        return [int(round(v / 1000.0)) for v in ms]

    def weekday_histogram(self, days: int = 365) -> list[int]:
        """Active seconds per weekday, index 0 = Monday (date.weekday's own numbering)."""
        start, end, _f, _t = _window(days)
        ms = [0] * 7
        for r in self._rows(
            "SELECT started_at, active_ms FROM session "
            "WHERE started_at >= ? AND started_at < ?",
            (start, end),
        ):
            ms[local_day(r["started_at"]).weekday()] += int(r["active_ms"] or 0)
        return [int(round(v / 1000.0)) for v in ms]

    # ------------------------------------------------------------ how it was played
    def velocity_by_day(self, days: int = 90) -> list[dict]:
        """Dynamics per local day, oldest first. Only days that have notes.

        `distinct` is the interesting column: it sits at 1 until the piano comes off
        Fixed touch sensitivity, and everything else here is meaningless until it does.

        Grouped by (session, velocity) rather than fetched note by note -- that is at
        most 127 rows per session, and a distinct count is the one figure that cannot
        be merged from per-session totals, so the raw pairs have to reach Python.
        """
        start, end, _f, _t = _window(days)
        by_day: dict[date, dict[str, Any]] = {}
        for r in self._rows(
            "SELECT s.started_at AS started_at, e.velocity AS velocity, COUNT(*) AS n "
            "FROM note_event e JOIN session s ON s.id = e.session_id "
            "WHERE s.started_at >= ? AND s.started_at < ? "
            "GROUP BY e.session_id, e.velocity",
            (start, end),
        ):
            v, n = int(r["velocity"]), int(r["n"])
            b = by_day.setdefault(local_day(r["started_at"]),
                                  {"n": 0, "sum": 0, "min": v, "max": v, "seen": set()})
            b["n"] += n
            b["sum"] += v * n
            b["min"] = min(b["min"], v)
            b["max"] = max(b["max"], v)
            b["seen"].add(v)
        return [{
            "date": d.isoformat(),
            "mean": round(b["sum"] / b["n"], 1),
            "min": b["min"],
            "max": b["max"],
            "distinct": len(b["seen"]),
        } for d, b in sorted(by_day.items())]

    def notes_per_minute(self, days: int = 90) -> list[dict]:
        """Notes per active minute, per local day, oldest first.

        Per *active* minute, so this is a density measured against the practice clock,
        not against how long the app was open. A day of scales and a day of sight
        reading are supposed to look different here.
        """
        start, end, _f, _t = _window(days)
        by_day: dict[date, list[int]] = {}
        for r in self._rows(
            "SELECT started_at, active_ms, note_count FROM session "
            "WHERE started_at >= ? AND started_at < ?",
            (start, end),
        ):
            b = by_day.setdefault(local_day(r["started_at"]), [0, 0])
            b[0] += int(r["note_count"] or 0)
            b[1] += int(r["active_ms"] or 0)
        out: list[dict] = []
        for day, (notes, ms) in sorted(by_day.items()):
            seconds = int(round(ms / 1000.0))
            out.append({
                "date": day.isoformat(),
                # A session can be all note_count and no clock: the first note of a
                # session credits no time, so a one-note day really is 0 seconds.
                "npm": round(notes / (seconds / 60.0), 1) if seconds else 0.0,
                "notes": notes,
                "active_seconds": seconds,
            })
        return out

    def session_lengths(self, days: int = 365) -> list[dict]:
        """Session active length in 5-minute buckets, shortest first.

        `minutes` is the bucket's low edge: 10 means 10:00 up to 14:59. Empty buckets
        are omitted rather than filled -- unlike history(), nothing here needs the gaps
        drawn, and one 90-minute session should not stretch the axis with 15 zeros.
        """
        start, end, _f, _t = _window(days)
        buckets: dict[int, int] = {}
        for r in self._rows(
            "SELECT active_ms FROM session WHERE started_at >= ? AND started_at < ?",
            (start, end),
        ):
            low = (int(r["active_ms"] or 0) // 60_000 // 5) * 5
            buckets[low] = buckets.get(low, 0) + 1
        return [{"minutes": m, "count": n} for m, n in sorted(buckets.items())]

    def preset_usage(self, days: int = 365) -> list[dict]:
        """Which sounds actually got played, by active seconds."""
        start, end, _f, _t = _window(days)
        rows = self._rows(
            "SELECT preset, COALESCE(SUM(active_ms), 0) AS ms, COUNT(*) AS n FROM session "
            "WHERE started_at >= ? AND started_at < ? "
            "GROUP BY preset ORDER BY ms DESC, preset ASC",
            (start, end),
        )
        return [{
            "preset": r["preset"] or "",
            "seconds": int(round(int(r["ms"] or 0) / 1000.0)),
            "sessions": int(r["n"]),
        } for r in rows]

    def totals(self, days: int = 365) -> dict:
        """One headline row for the window.

        `days_practiced` counts any day with a session at all, a looser bar than the
        streak's one-real-minute rule -- this is "days you sat down", not "days that
        earned a tick".
        """
        start, end, _f, _t = _window(days)
        sessions = self._rows(
            "SELECT started_at, active_ms, note_count FROM session "
            "WHERE started_at >= ? AND started_at < ?",
            (start, end),
        )
        chords = self._rows(
            "SELECT COUNT(*) AS n FROM chord_event "
            f"WHERE session_id IN ({self._session_ids_sql()})",
            (start, end),
        )
        return {
            "active_seconds": int(round(sum(int(r["active_ms"] or 0) for r in sessions) / 1000.0)),
            "note_count": sum(int(r["note_count"] or 0) for r in sessions),
            "sessions": len(sessions),
            "chords": int(chords[0]["n"]) if chords else 0,
            "days_practiced": len({local_day(r["started_at"]) for r in sessions}),
            "first_at": min((r["started_at"] for r in sessions), default=None),
        }

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

    # ------------------------------------------------------------- exercises
    # Accuracy is divided here, in every one of these, rather than stored: see the
    # schema comment on exercise_attempt. correct and steps are what happened; the
    # ratio is a reading of it, and readings change.
    def exercise_history(self, exercise: str, variant: str, days: int = 90) -> list[dict]:
        """Every attempt at one variant in the window, oldest first.

        Oldest first because this is drawn as a line: the point of the chart is the
        slope, and a chart that reads right to left has to be reversed by the caller.
        """
        start, end, _f, _t = _window(days)
        rows = self._rows(
            "SELECT at, steps, correct, bpm, evenness_cv, sync_ms, crossing_ms "
            "FROM exercise_attempt "
            "WHERE exercise = ? AND variant = ? AND at >= ? AND at < ? ORDER BY at ASC",
            (exercise, variant, start, end),
        )
        return [{
            "at": r["at"],
            "accuracy": _accuracy(r["correct"], r["steps"]),
            "bpm": r["bpm"],
            "evenness_cv": r["evenness_cv"],
            "sync_ms": r["sync_ms"],
            "crossing_ms": r["crossing_ms"],
        } for r in rows]

    def best_clean_tempo(self, exercise: str, variant: str) -> dict | None:
        """Fastest tempo this variant was ever played clean at, or None.

        All of time, not a window: a personal best does not expire, and the whole
        reason to keep one is to see it stand for months and then fall.

        Clean means every step right, evenness under metrics.CLEAN_CV, and at least
        CLEAN_MIN_STEPS of them so a one-octave rip cannot set a record. Imported
        from there rather than restated: two copies of a threshold is one threshold
        and one bug waiting for someone to tune the other.
        """
        # Imported inside the method because backend.exercises imports Store at its
        # own module scope -- at the top of this file it is a cycle, and which half
        # explodes depends on which module the process happens to import first.
        from .exercises.metrics import CLEAN_CV, CLEAN_MIN_STEPS

        rows = self._rows(
            "SELECT bpm, at, evenness_cv FROM exercise_attempt "
            "WHERE exercise = ? AND variant = ? AND bpm IS NOT NULL "
            "AND steps >= ? AND correct = steps "
            "AND evenness_cv IS NOT NULL AND evenness_cv < ? "
            # Ties on bpm go to the most recent: the same tempo held again today is
            # the better evidence that it is really yours.
            "ORDER BY bpm DESC, at DESC LIMIT 1",
            (exercise, variant, CLEAN_MIN_STEPS, CLEAN_CV),
        )
        if not rows:
            return None
        r = rows[0]
        return {"bpm": r["bpm"], "at": r["at"], "evenness_cv": r["evenness_cv"]}

    def exercise_summary(self, days: int = 30) -> dict:
        """Headline row for the Practice tab: how much, of what, and what was last.

        best_accuracy is MAX over the window rather than a mean. A mean punishes the
        attempts where you pushed the tempo until it fell apart, which is the useful
        kind of practice.
        """
        start, end, _f, _t = _window(days)
        total = self._rows(
            "SELECT COUNT(*) AS n FROM exercise_attempt WHERE at >= ? AND at < ?",
            (start, end),
        )
        per = self._rows(
            "SELECT exercise, COUNT(*) AS n, "
            # CASE, not a WHERE: an attempt with no steps still counts as an attempt,
            # it just cannot contribute an accuracy. MAX ignores the NULLs.
            "MAX(CASE WHEN steps > 0 THEN CAST(correct AS REAL) / steps END) AS best "
            "FROM exercise_attempt WHERE at >= ? AND at < ? "
            "GROUP BY exercise ORDER BY n DESC, exercise ASC",
            (start, end),
        )
        recent = self._rows(
            "SELECT exercise, variant, params, steps, correct, at FROM exercise_attempt "
            "WHERE at >= ? AND at < ? ORDER BY at DESC LIMIT 10",
            (start, end),
        )
        return {
            "attempts": int(total[0]["n"]) if total else 0,
            "exercises": [{
                "exercise": r["exercise"] or "",
                "attempts": int(r["n"]),
                "best_accuracy": round(r["best"], 4) if r["best"] is not None else 0.0,
            } for r in per],
            "recent": [_attempt_row(r) for r in recent],
        }

    def recent_variants(self, limit: int = 8) -> list[dict]:
        """The last few things practised, one row per (exercise, variant).

        This is "pick up where you left off", so it carries the human title out of the
        params blob -- see log_exercise for why the title lives there.

        MAX(at) with bare columns is SQLite's documented single-row rule: every other
        column comes from the row that actually held the maximum. That is what makes
        each row here the *latest* attempt at its variant rather than an arbitrary one.
        """
        rows = self._rows(
            "SELECT exercise, variant, params, steps, correct, MAX(at) AS at "
            "FROM exercise_attempt GROUP BY exercise, variant ORDER BY at DESC LIMIT ?",
            (int(limit),),
        )
        return [_attempt_row(r) for r in rows]

    def weak_steps(self, exercise: str | None = None, limit: int = 12) -> list[dict]:
        """weak_notes for exercises: worst correct-rate first, three attempts minimum.

        A step is stored by its lowest note, so a chord answers under its bass. That is
        a real limitation and the right one for now -- "you keep missing the F#" is the
        sentence this has to be able to say.
        """
        # The join only exists to filter, so it is only paid for when there is a filter.
        src = "exercise_step s"
        where = ""
        args: tuple[Any, ...] = ()
        if exercise is not None:
            src = "exercise_step s JOIN exercise_attempt a ON a.id = s.attempt_id"
            where = "WHERE a.exercise = ? "
            args = (exercise,)
        rows = self._rows(
            # Every column qualified: `correct` exists on both tables, and an
            # unqualified one in the joined form is an ambiguous-column error that
            # _rows would swallow into an empty list.
            "SELECT s.note AS note, COUNT(*) AS attempts, SUM(s.correct) AS correct, "
            f"AVG(s.reaction_ms) AS reaction FROM {src} {where}"
            "GROUP BY s.note HAVING COUNT(*) >= 3 "
            "ORDER BY (CAST(SUM(s.correct) AS REAL) / COUNT(*)) ASC, COUNT(*) DESC LIMIT ?",
            args + (int(limit),),
        )
        return [{
            "note": int(r["note"]),
            "attempts": int(r["attempts"]),
            "correct": int(r["correct"] or 0),
            "accuracy": round(int(r["correct"] or 0) / int(r["attempts"]), 2),
            # Averaged over the attempts that have one: a skipped step has no reaction
            # time, and AVG passing over those NULLs is the behaviour wanted.
            "mean_reaction_ms": int(round(r["reaction"] or 0)),
        } for r in rows]

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
