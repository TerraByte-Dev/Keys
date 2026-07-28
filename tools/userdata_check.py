"""Checks for backend/userdata.py -- the reset buttons.

Everything here runs against a THROWAWAY data directory created by this script.
That is not tidiness: these functions delete practice history, and a check that
imported the real config would erase the history of whoever ran it. KEYS_DATA_DIR
is set before backend.config is imported, because config resolves its paths at
import time and there is no second chance.

What is actually being tested is that each category deletes its own rows and
NOTHING else. A reset that takes a neighbouring table with it is the failure that
matters, so every case asserts on the survivors as well as the casualties.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SANDBOX = Path(tempfile.mkdtemp(prefix="keys-userdata-check-"))
os.environ["KEYS_DATA_DIR"] = str(SANDBOX)

from backend import config, store, userdata   # noqa: E402

assert config.DATA_DIR == SANDBOX, f"sandbox not in effect: {config.DATA_DIR}"

fails: list[str] = []
count = 0


def ok(cond: bool, label: str, detail: str = "") -> None:
    global count
    count += 1
    if cond:
        print(f"  [PASS] {label}" + (f" -- {detail}" if detail else ""))
    else:
        print(f"  [FAIL] {label}" + (f" -- {detail}" if detail else ""))
        fails.append(label)


def seed(st: store.Store) -> None:
    """A little of everything, so a reset has something to get wrong."""
    sid = st.start_session("grand-piano")
    st.log_notes(sid, [(60 + i % 12, 80, i * 100) for i in range(40)])
    st.log_chords(sid, [(i * 200, "C", 0, "", 3, 90) for i in range(5)])
    for i in range(6):
        st.log_sightread(sid, 60 + i, i % 2 == 0, 240, "C", "treble")
    st.log_exercise(sid, {
        "exercise": "scales", "variant": "C major", "steps": 8, "correct": 8,
        "accuracy": 1.0, "bpm": 60, "seconds": 12.0,
    }, [{"index": i, "expected": 60 + i, "played": 60 + i, "correct": True,
         "offset_ms": 3.0, "ioi_ms": 500.0, "crossing": False} for i in range(8)])
    st.end_session(sid, 60000, 40)

    config.RECORDING_DIR.mkdir(parents=True, exist_ok=True)
    for i in range(3):
        (config.RECORDING_DIR / f"take{i}.loop.json").write_text("{}", "utf-8")
    scores = SANDBOX / "scores"
    scores.mkdir(parents=True, exist_ok=True)
    for i in range(2):
        (scores / f"s{i}.json").write_text("{}", "utf-8")
        (scores / f"s{i}.musicxml").write_text("<score/>", "utf-8")


def counts(st: store.Store) -> dict[str, int]:
    conn = st._conn
    return {t: userdata._count(conn, t) for t in
            ("session", "note_event", "chord_event", "sightread_attempt",
             "exercise_attempt", "exercise_step")}


_open: store.Store | None = None


def fresh() -> tuple[store.Store, config.Settings]:
    # Windows will not unlink an open file, so the previous handle has to go first.
    global _open
    if _open is not None:
        _open.close()
        _open = None
    for p in (config.DB_PATH, config.RECORDING_DIR, SANDBOX / "scores",
              config.SETTINGS_PATH):
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.exists():
            p.unlink()
    for side in ("-wal", "-shm"):
        q = config.DB_PATH.with_name(config.DB_PATH.name + side)
        if q.exists():
            q.unlink()
    st = store.Store(config.DB_PATH)
    _open = st
    seed(st)
    return st, config.Settings(config.SETTINGS_PATH)


print(f"sandbox: {SANDBOX}")
print("\n1. the inventory reports what is really there")

st, settings = fresh()
inv = st.inventory()
by_id = {i["id"]: i for i in inv["items"]}
ok(by_id["practice"]["count"] == 1, "one session logged", by_id["practice"]["detail"])
ok(by_id["recordings"]["count"] == 3, "three recordings", by_id["recordings"]["detail"])
ok(by_id["scores"]["count"] == 2, "two scores, counted by metadata not by halving",
   by_id["scores"]["detail"])
ok(by_id["exercises"]["count"] == 1, "one exercise run", by_id["exercises"]["detail"])
ok(inv["db_bytes"] > 0, "the database has a size", f"{inv['db_bytes']:,} B")
ok(set(i["id"] for i in inv["items"]) == set(userdata.CATEGORIES),
   "every category a reset accepts is offered in the inventory")

print("\n2. each category deletes ITS OWN rows and no others")

st, settings = fresh()
before = counts(st)
st.wipe("practice", settings)
after = counts(st)
ok(after["session"] == 0 and after["note_event"] == 0 and after["chord_event"] == 0,
   "practice takes sessions, notes and chords")
ok(after["sightread_attempt"] == before["sightread_attempt"]
   and after["exercise_attempt"] == before["exercise_attempt"]
   and after["exercise_step"] == before["exercise_step"],
   "and leaves sight-reading and exercises alone",
   f"sightread {after['sightread_attempt']}, exercises {after['exercise_attempt']}")

st, settings = fresh()
before = counts(st)
st.wipe("sightread", settings)
after = counts(st)
ok(after["sightread_attempt"] == 0, "sightread takes its attempts")
ok(after["session"] == before["session"] and after["note_event"] == before["note_event"]
   and after["exercise_attempt"] == before["exercise_attempt"],
   "and leaves your sessions and exercises standing")

st, settings = fresh()
before = counts(st)
st.wipe("exercises", settings)
after = counts(st)
ok(after["exercise_attempt"] == 0 and after["exercise_step"] == 0,
   "exercises takes runs and their steps")
ok(after["session"] == before["session"] and after["sightread_attempt"] == before["sightread_attempt"],
   "and nothing else")

st, settings = fresh()
st.wipe("recordings", settings)
ok(not list(config.RECORDING_DIR.glob("*.loop.json")), "recordings clears the folder")
ok(counts(st)["session"] == 1, "without touching the database")

st, settings = fresh()
st.wipe("scores", settings)
ok(not list((SANDBOX / "scores").glob("*")), "scores clears the folder")
ok(config.RECORDING_DIR.exists() and list(config.RECORDING_DIR.glob("*.loop.json")),
   "and leaves your recordings where they are")

print("\n3. settings and layout")

st, settings = fresh()
settings.update({"ui": {"layout": {"play": [{"id": "a", "span": 6}]}},
                 "idle_seconds": 45, "audio": {"gain": 0.9}})
st.wipe("layout", settings)
ok(settings.get("ui", "layout", default={}) == {"play": []},
   "layout empties every view's list", str(settings.get("ui", "layout")))
ok(settings.get("idle_seconds") == 45 and settings.get("audio", "gain") == 0.9,
   "and changes nothing else")

st, settings = fresh()
settings.update({"ui": {"layout": {"play": [{"id": "a", "span": 6}]}, "theme": "paper"},
                 "idle_seconds": 45, "audio": {"gain": 0.9}})
st.wipe("settings", settings)
ok(settings.get("idle_seconds") == 12 and settings.get("audio", "gain") == 0.6,
   "settings goes back to the defaults",
   f"idle={settings.get('idle_seconds')} gain={settings.get('audio', 'gain')}")
ok(settings.get("ui", "theme") == "midnight", "including the theme")
# The panel arrangement has its own button; a reset that also moved every panel
# would be doing something its label never claimed.
ok(settings.get("ui", "layout") == {"play": [{"id": "a", "span": 6}]},
   "but keeps the panel layout, which is a separate reset",
   str(settings.get("ui", "layout")))
ok(counts(st)["session"] == 1, "and never touches your history")

print("\n4. everything, and the guards")

st, settings = fresh()
res = st.wipe("everything", settings)
after = counts(st)
ok(all(v == 0 for v in after.values()), "everything empties every table", str(after))
ok(not list(config.RECORDING_DIR.glob("*")) and not list((SANDBOX / "scores").glob("*")),
   "and both folders")
ok(set(res["removed"]) == set(userdata.CATEGORIES),
   "and reports each category it touched", str(sorted(res["removed"])))

# The schema has to survive: the app keeps running with this connection.
sid = st.start_session("grand-piano")
st.log_notes(sid, [(60, 80, 0)])
ok(counts(st)["note_event"] == 1,
   "the database still works afterwards -- rows were deleted, not tables dropped")

try:
    st.wipe("../../etc", settings)
    ok(False, "an unknown category is refused")
except ValueError:
    ok(True, "an unknown category raises rather than guessing")

st.close()
shutil.rmtree(SANDBOX, ignore_errors=True)
ok(not SANDBOX.exists(), "the sandbox is cleaned up")

print()
if fails:
    print(f"  {len(fails)} of {count} FAILED:")
    for f in fails:
        print(f"    - {f}")
    raise SystemExit(1)
print(f"  {count} assertions")
print("ALL CHECKS PASSED")
