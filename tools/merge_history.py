"""Merge one keys.db into another without losing a note.

    .venv\\Scripts\\python tools\\merge_history.py SOURCE.db INTO.db          # dry run
    .venv\\Scripts\\python tools\\merge_history.py SOURCE.db INTO.db --write  # do it

Why this exists: a source checkout keeps its database beside keys.py, and the
installed app keeps its own under %LOCALAPPDATA%\\Keys. Play in both and you end up
with two partial histories, and neither `cp` nor the app can reconcile them --
copying one over the other throws the other away.

**Nothing is ever overwritten.** Rows are appended with fresh ids, the destination
is backed up first, and the source is opened read-only so a mistake in the
arguments cannot cost you the file you meant to keep.

Two parent-child chains have to be remapped rather than copied verbatim:

    session          -> note_event, chord_event, sightread_attempt, exercise_attempt
    exercise_attempt -> exercise_step

Both parents use INTEGER PRIMARY KEY, so ids collide between any two databases.
Every parent row is therefore inserted with its id left to the destination, and
its children are rewritten to point at whatever id came back.

Sessions already present are skipped. A session is identified by when it started
rather than by its id -- ids are per-database and meaningless across two, while a
start time is a real event that happened once. Re-running is therefore safe.
"""

from __future__ import annotations

import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Children of `session`, and the column that points back at it.
SESSION_CHILDREN = {
    "note_event": "session_id",
    "chord_event": "session_id",
    "sightread_attempt": "session_id",
}
# Sessions and their start times match to within this many seconds. Two clocks
# writing the same event never disagree by more than rounding.
SAME_SESSION_SECONDS = 0.5


def columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def copy_row(dst: sqlite3.Connection, table: str, cols: list[str],
             row: sqlite3.Row, overrides: dict[str, object]) -> int:
    """Insert one row, dropping its id so the destination assigns a fresh one."""
    use = [c for c in cols if c != "id"]
    values = [overrides.get(c, row[c]) for c in use]
    placeholders = ", ".join("?" for _ in use)
    quoted = ", ".join(f'"{c}"' for c in use)
    cur = dst.execute(f"INSERT INTO {table} ({quoted}) VALUES ({placeholders})", values)
    return int(cur.lastrowid or 0)


def merge(src_path: Path, dst_path: Path, write: bool) -> int:
    if not src_path.exists():
        print(f"  no such file: {src_path}")
        return 1
    if not dst_path.exists():
        print(f"  no such file: {dst_path}")
        return 1
    if src_path.resolve() == dst_path.resolve():
        print("  source and destination are the same file")
        return 1

    if write:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = dst_path.with_name(f"{dst_path.stem}.before-merge-{stamp}.db")
        shutil.copy2(dst_path, backup)
        print(f"  backup -> {backup}")

    # Read-only source: the file you are merging FROM is never touched.
    src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    dst = sqlite3.connect(str(dst_path))
    dst.row_factory = sqlite3.Row

    existing = [r[0] for r in dst.execute("SELECT started_at FROM session")]

    def already_there(started_at: float | None) -> bool:
        if started_at is None:
            return False
        return any(abs(started_at - e) < SAME_SESSION_SECONDS
                   for e in existing if e is not None)

    sess_cols = columns(src, "session")
    child_cols = {t: columns(src, t) for t in SESSION_CHILDREN}
    att_cols = columns(src, "exercise_attempt")
    step_cols = columns(src, "exercise_step")

    moved = {t: 0 for t in SESSION_CHILDREN}
    moved["session"] = 0
    moved["exercise_attempt"] = 0
    moved["exercise_step"] = 0
    skipped = 0

    for sess in src.execute("SELECT * FROM session ORDER BY started_at"):
        if already_there(sess["started_at"]):
            skipped += 1
            continue
        if not write:
            moved["session"] += 1
            for table, fk in SESSION_CHILDREN.items():
                moved[table] += src.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {fk} = ?", (sess["id"],)
                ).fetchone()[0]
            n_att = src.execute("SELECT COUNT(*) FROM exercise_attempt WHERE session_id = ?",
                                (sess["id"],)).fetchone()[0]
            moved["exercise_attempt"] += n_att
            continue

        new_sid = copy_row(dst, "session", sess_cols, sess, {})
        moved["session"] += 1

        for table, fk in SESSION_CHILDREN.items():
            for row in src.execute(f"SELECT * FROM {table} WHERE {fk} = ?", (sess["id"],)):
                copy_row(dst, table, child_cols[table], row, {fk: new_sid})
                moved[table] += 1

        # exercise_attempt is both a child of session and a parent of exercise_step,
        # so its new id has to be captured on the way past.
        for att in src.execute("SELECT * FROM exercise_attempt WHERE session_id = ?",
                               (sess["id"],)):
            new_aid = copy_row(dst, "exercise_attempt", att_cols, att,
                               {"session_id": new_sid})
            moved["exercise_attempt"] += 1
            for step in src.execute("SELECT * FROM exercise_step WHERE attempt_id = ?",
                                    (att["id"],)):
                copy_row(dst, "exercise_step", step_cols, step, {"attempt_id": new_aid})
                moved["exercise_step"] += 1

    # Runs recorded with no session at all -- the app allows it -- would be dropped
    # by the loop above, which only ever walks sessions. They need their own identity
    # test too, or a second run of this script appends them again: there is no session
    # start time to recognise them by.
    seen_att = {(round(r[0] or 0, 3), r[1], r[2]) for r in dst.execute(
        "SELECT at, exercise, variant FROM exercise_attempt")}
    orphans = list(src.execute("SELECT * FROM exercise_attempt WHERE session_id IS NULL"))
    for att in orphans:
        ident = (round(att["at"] or 0, 3), att["exercise"], att["variant"])
        if ident in seen_att:
            continue
        seen_att.add(ident)
        if not write:
            moved["exercise_attempt"] += 1
            continue
        new_aid = copy_row(dst, "exercise_attempt", att_cols, att, {"session_id": None})
        moved["exercise_attempt"] += 1
        for step in src.execute("SELECT * FROM exercise_step WHERE attempt_id = ?",
                                (att["id"],)):
            copy_row(dst, "exercise_step", step_cols, step, {"attempt_id": new_aid})
            moved["exercise_step"] += 1

    if write:
        dst.commit()

    print(f"\n  {'merged' if write else 'would merge'}:")
    for table in ("session", "note_event", "chord_event", "sightread_attempt",
                  "exercise_attempt", "exercise_step"):
        if moved.get(table):
            print(f"    {table:<20} {moved[table]:>8,}")
    print(f"    {'sessions already there':<20} {skipped:>8,}  (skipped)")

    if write:
        after = dst.execute(
            "SELECT (SELECT COUNT(*) FROM session), (SELECT COUNT(*) FROM note_event)"
        ).fetchone()
        print(f"\n  {dst_path.name} now holds {after[0]:,} sessions and {after[1]:,} notes")
    src.close()
    dst.close()
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    write = "--write" in sys.argv
    if len(args) != 2:
        print(__doc__)
        return 1
    if not write:
        print("  DRY RUN -- pass --write to actually merge\n")
    return merge(Path(args[0]), Path(args[1]), write)


if __name__ == "__main__":
    raise SystemExit(main())
