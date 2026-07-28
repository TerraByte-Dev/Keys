"""What Keys has kept about you, and how to make it forget.

Two jobs. `inventory()` answers "what is on my disk" with counts and bytes, because
a Delete button next to a number nobody can see is a button nobody will press.
`reset()` deletes exactly one category and nothing else.

Every category is deliberately narrow. "Clear my history" meaning *also* your
presets, your loops and your settings is the kind of helpfulness that loses work,
so the widest thing here is `everything`, and even that spells out what it took.

The database is never dropped and recreated -- rows are deleted from the tables
that hold the chosen category, inside one transaction, and the schema survives. A
half-deleted database with a live connection on it is how an app starts throwing
"no such table" at a user who only wanted to clear last month.

Nothing here touches the SoundFont, the bundled presets, or anything under the
install directory. Those are the program, not your data.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from typing import Any

from . import config

# Category -> the tables it owns. Order matters inside a category: children first,
# because these are plain DELETEs and nothing here relies on cascade being on.
TABLES: dict[str, tuple[str, ...]] = {
    "practice": ("note_event", "chord_event", "session"),
    "sightread": ("sightread_attempt",),
    "exercises": ("exercise_step", "exercise_attempt"),
}

# Category -> a directory that is entirely yours.
FOLDERS: dict[str, Path] = {
    "recordings": config.RECORDING_DIR,
    "scores": config.DATA_DIR / "scores",
}

CATEGORIES = tuple(TABLES) + tuple(FOLDERS) + ("layout", "settings")


def _plural(n: int, word: str) -> str:
    return word if n == 1 else word + "s"


def _dir_stats(path: Path, pattern: str = "*") -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    files = [p for p in path.glob(pattern) if p.is_file()]
    return len(files), sum(p.stat().st_size for p in files)


def _count(conn: sqlite3.Connection | None, table: str) -> int:
    if conn is None:
        return 0
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except sqlite3.Error:
        return 0


def inventory(conn: sqlite3.Connection | None) -> dict[str, Any]:
    """Counts and bytes for everything a reset can remove."""
    sessions = _count(conn, "session")
    notes = _count(conn, "note_event")
    rec_n, rec_b = _dir_stats(config.RECORDING_DIR, "*.loop.json")
    # Counted off the metadata files, not off half the file count: a score is one
    # .json plus one original, but a failed import can leave an odd number.
    sc_n, _ = _dir_stats(FOLDERS["scores"], "*.json")
    _, sc_b = _dir_stats(FOLDERS["scores"])

    db_bytes = config.DB_PATH.stat().st_size if config.DB_PATH.exists() else 0
    # WAL and the shared-memory file are part of what the database costs on disk;
    # reporting only keys.db under-reports it by however much has not checkpointed.
    for suffix in ("-wal", "-shm"):
        side = config.DB_PATH.with_name(config.DB_PATH.name + suffix)
        if side.exists():
            db_bytes += side.stat().st_size

    return {
        "data_dir": str(config.DATA_DIR),
        "db_bytes": db_bytes,
        "items": [
            {"id": "practice", "label": "Practice history",
             "detail": f"{sessions:,} {_plural(sessions, 'session')}, "
                       f"{notes:,} {_plural(notes, 'note')}",
             "count": sessions,
             "note": "every session, note and chord Keys has logged"},
            {"id": "sightread", "label": "Sight-reading history",
             "detail": f"{_count(conn, 'sightread_attempt'):,} "
                       f"{_plural(_count(conn, 'sightread_attempt'), 'attempt')}",
             "count": _count(conn, "sightread_attempt"),
             "note": "also what the adaptive weighting learns from"},
            {"id": "exercises", "label": "Exercise history",
             "detail": f"{_count(conn, 'exercise_attempt'):,} "
                       f"{_plural(_count(conn, 'exercise_attempt'), 'run')}",
             "count": _count(conn, "exercise_attempt"),
             "note": "accuracy, evenness and your best clean tempos"},
            {"id": "recordings", "label": "Loop recordings",
             "detail": f"{rec_n} saved, {rec_b / 1024:.0f} KB",
             "count": rec_n,
             "note": "the takes you saved from the loop station"},
            {"id": "scores", "label": "Sheet music",
             "detail": f"{sc_n} {_plural(sc_n, 'score')}, {sc_b / 1024:.0f} KB",
             "count": sc_n,
             "note": "everything you imported, and the originals"},
            {"id": "layout", "label": "Panel layout",
             "detail": "per tab", "count": 1,
             "note": "puts every panel back where it shipped"},
            {"id": "settings", "label": "Settings",
             "detail": "audio, MIDI, effects, theme", "count": 1,
             "note": "back to defaults; your history is untouched"},
        ],
    }


def reset(conn: sqlite3.Connection | None, what: str, settings: Any) -> dict[str, Any]:
    """Delete one category. Returns what was removed, counted before it went."""
    if what not in CATEGORIES and what != "everything":
        raise ValueError(f"unknown category {what!r}")

    targets = list(CATEGORIES) if what == "everything" else [what]
    removed: dict[str, int] = {}

    for cat in targets:
        if cat in TABLES:
            if conn is None:
                removed[cat] = 0     # no database open; nothing to forget
                continue
            n = 0
            # One transaction per category: a category either goes or it does not.
            with conn:
                for table in TABLES[cat]:
                    n += conn.execute(f"DELETE FROM {table}").rowcount or 0
            removed[cat] = n
        elif cat in FOLDERS:
            folder = FOLDERS[cat]
            n = 0
            if folder.exists():
                for p in folder.iterdir():
                    if p.is_file():
                        p.unlink()
                        n += 1
                    elif p.is_dir():
                        shutil.rmtree(p, ignore_errors=True)
                        n += 1
            removed[cat] = n
        elif cat == "layout":
            # Under "ui", where layout.js writes it -- clearing a top-level "layout"
            # key would report success and change nothing at all.
            #
            # Each view set to [] rather than the whole key to {}: Settings.update
            # deep-merges dicts, so clearing it with {} clears nothing either. Lists
            # are the one thing it replaces wholesale.
            current = settings.get("ui", "layout", default={}) or {}
            settings.update({"ui": {"layout": {k: [] for k in current}}})
            removed["layout"] = len(current)
        elif cat == "settings":
            removed["settings"] = settings.reset_to_defaults()

    # Space is only handed back at a checkpoint; without this the file stays exactly
    # as large as it was and the number on screen does not move.
    if conn is not None and any(cat in TABLES for cat in targets):
        try:
            conn.commit()            # VACUUM cannot run inside a transaction
            conn.execute("VACUUM")
        except sqlite3.Error:
            pass       # a VACUUM that cannot run is untidy, never incorrect

    return {"removed": removed, "what": what}
