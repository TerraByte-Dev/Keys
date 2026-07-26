"""Regression test for the practice log. Needs no piano, no FluidSynth, no browser.

    .venv\\Scripts\\python.exe tools\\store_check.py

Every Store in here lives in a throwaway temp directory, and the real keys.db is
fingerprinted before and after so an accidental default-path Store() cannot slip
through unnoticed -- the practice history is the one thing in this project that
cannot be regenerated.

Past days are seeded by inserting sessions with explicit epoch timestamps rather than
by faking the clock, and those epochs are built with `datetime.timestamp()` so the
local-day maths is checked against a different stdlib path than the one under test.
"""

from __future__ import annotations

import logging
import shutil
import sys
import tempfile
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config  # noqa: E402
from backend.store import Store  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="keys-store-check-"))
REAL_DB = config.DB_PATH
REAL_BEFORE = (REAL_DB.exists(), REAL_DB.stat().st_mtime_ns if REAL_DB.exists() else 0,
               REAL_DB.stat().st_size if REAL_DB.exists() else 0)

TODAY = date.today()
ok = True
stores: list[Store] = []


def step(label: str, passed: bool, detail: str = "") -> None:
    global ok
    ok = ok and passed
    print(f"  [{'PASS' if passed else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))


def fresh(name: str) -> Store:
    s = Store(TMP / f"{name}.db")
    stores.append(s)
    return s


def noon(days_ago: int) -> float:
    """Local noon `days_ago` days back. Noon so no DST shift can move the day."""
    d = TODAY - timedelta(days=days_ago)
    return datetime(d.year, d.month, d.day, 12, 0, 0).timestamp()


def local_epoch(days_ago: int, hour: int, minute: int = 0) -> float:
    """A known local wall-clock instant, for the hour-of-day and weekday histograms."""
    d = TODAY - timedelta(days=days_ago)
    return datetime(d.year, d.month, d.day, hour, minute, 0).timestamp()


def seed_at(store: Store, start: float, active_ms: int, notes: int = 0,
            preset: str = "grand-piano") -> int:
    return store._write(  # noqa: SLF001 -- explicit past timestamps, no public API for that
        "INSERT INTO session(started_at, ended_at, active_ms, note_count, preset) "
        "VALUES (?, ?, ?, ?, ?)",
        (start, start + active_ms / 1000.0, active_ms, notes, preset),
    )


def seed(store: Store, days_ago: int, active_ms: int, notes: int = 0,
         preset: str = "grand-piano") -> int:
    return seed_at(store, noon(days_ago), active_ms, notes, preset)


def count(store: Store, table: str) -> int:
    rows = store._rows(f"SELECT COUNT(*) AS n FROM {table}")  # noqa: SLF001
    return int(rows[0]["n"]) if rows else -1


print("1. schema")
db = TMP / "schema.db"
s = Store(db)
stores.append(s)
tables = [r["name"] for r in s._rows(  # noqa: SLF001
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
indexes = [r["name"] for r in s._rows(  # noqa: SLF001
    "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%' ORDER BY name")]
mode = s._rows("PRAGMA journal_mode")[0][0]  # noqa: SLF001
sync = s._rows("PRAGMA synchronous")[0][0]  # noqa: SLF001
step("tables created",
     tables == ["chord_event", "note_event", "session", "sightread_attempt"], str(tables))
step("indexes created", len(indexes) == 7, ", ".join(indexes))
step("WAL enabled", mode == "wal", f"journal_mode={mode}")
step("synchronous=NORMAL", sync == 1, f"synchronous={sync}")
s.close()
s2 = Store(db)  # second open over the same file must be a no-op, not a duplicate-table error
stores.append(s2)
step("reopening an existing db is clean", count(s2, "session") == 0, "CREATE TABLE IF NOT EXISTS")
try:
    s.close()
    s.close()
    closed_twice = True
except Exception as exc:  # noqa: BLE001
    closed_twice = False
    print(f"    close() raised: {exc!r}")
step("close() is idempotent", closed_twice)

print("2. session lifecycle")
live = fresh("live")
sid = live.start_session("bass-split")
rows = [(i * 50, 21 + (i % 88), 30 + (i % 90)) for i in range(300)]
live.log_notes(sid, rows)
live.update_session(sid, 12_000, 300)
live.end_session(sid, 20_000, 300)
t = live.today()
step("session id assigned", sid > 0, f"id={sid}")
step("300 note events landed", count(live, "note_event") == 300)
step("today() sees the session", t["sessions"] == 1 and t["note_count"] == 300, str(t))
step("end_session wins over update_session", t["active_seconds"] == 20,
     f"active_seconds={t['active_seconds']} (12 s was overwritten by 20 s)")
step("first_at/last_at populated", t["first_at"] is not None and t["last_at"] is not None
     and t["last_at"] >= t["first_at"])
step("recent_sessions reports it", [r["preset"] for r in live.recent_sessions()] == ["bass-split"],
     str(live.recent_sessions()[0]["date"]))

# PracticeClock.end_session() calls this for a session too short to be practice.
# It is part of the required API, not an optional extra: without it a real run
# raises AttributeError on the first "did the sound work" session.
throw = fresh("discard")
tid = throw.start_session("grand-piano")
throw.log_notes(tid, [(0, 60, 64), (10, 62, 64)])
throw.log_sightread(tid, 60, True, 500, "C", "treble")
keep = throw.start_session("grand-piano")
throw.log_notes(keep, [(0, 64, 70)])
throw.discard_session(tid)
step("discard_session exists", hasattr(throw, "discard_session"))
step("discarded session row is gone", count(throw, "session") == 1)
step("its note events went with it", count(throw, "note_event") == 1,
     "the kept session's single note survives")
step("its sightread attempts went with it", count(throw, "sightread_attempt") == 0)
step("today() no longer counts it", throw.today()["sessions"] == 1, str(throw.today()))
step("discarding a failed session id is a no-op", (throw.discard_session(-1),
                                                   count(throw, "session"))[-1] == 1)

print("3. history() fills gaps")
hist = fresh("history")
seed(hist, days_ago=5, active_ms=600_000, notes=400)
seed(hist, days_ago=0, active_ms=180_000, notes=200)
seed(hist, days_ago=0, active_ms=120_000, notes=100)
h = hist.history(7)
dates = [r["date"] for r in h]
step("one row per day", len(h) == 7, f"{dates[0]} .. {dates[-1]}")
step("ascending, most recent last", dates == sorted(dates) and dates[-1] == TODAY.isoformat())
step("gap days present and zeroed",
     all(r["active_seconds"] == 0 and r["note_count"] == 0 and r["sessions"] == 0
         for r in h if r["date"] not in (dates[1], dates[6])),
     f"{sum(1 for r in h if r['sessions'] == 0)} empty days of 7")
step("day 5 back has its 600 s", h[1]["active_seconds"] == 600 and h[1]["note_count"] == 400,
     str(h[1]))
step("same-day sessions merge", h[6]["sessions"] == 2 and h[6]["active_seconds"] == 300,
     str(h[6]))
step("history is not the last 24 hours", h[0]["date"] == (TODAY - timedelta(days=6)).isoformat())

print("4. streak()")
run = fresh("streak")
for d in range(19, 31):          # 12 consecutive days, long ago
    seed(run, days_ago=d, active_ms=300_000)
for d in range(0, 5):            # 5 consecutive days ending today
    seed(run, days_ago=d, active_ms=300_000)
st = run.streak()
step("current streak counts back from today", st["current"] == 5, str(st))
step("longest streak found in the gap-separated run", st["longest"] == 12, str(st))
step("practiced_today", st["practiced_today"] is True)
step("total_days counts every qualifying day, streak or not", st["total_days"] == 17,
     f"total_days={st['total_days']} (12 + 5)")
step("total_active_seconds is all time", st["total_active_seconds"] == 17 * 300,
     f"total_active_seconds={st['total_active_seconds']}")

pend = fresh("streak-pending")
for d in range(1, 4):            # yesterday and the two before it; today untouched
    seed(pend, days_ago=d, active_ms=300_000)
st = pend.streak()
step("today empty does NOT break the streak", st["current"] == 3, str(st))
step("but practiced_today is False", st["practiced_today"] is False)

short = fresh("streak-short")
seed(short, days_ago=0, active_ms=45_000)   # 45 s: under the one-minute bar
seed(short, days_ago=1, active_ms=30_000)
st = short.streak()
step("under 60 s does not count as a day",
     st == {"current": 0, "longest": 0, "practiced_today": False, "total_days": 0,
            "total_active_seconds": 75}, str(st))
step("but the time itself is still counted", st["total_active_seconds"] == 75,
     "45 s + 30 s of playing that earned no streak day")

print("5. note_heatmap() clamps to the 88 keys")
heat = fresh("heat")
hid = heat.start_session("grand-piano")
heat.log_notes(hid, [(0, 20, 64), (1, 21, 64), (2, 21, 64), (3, 60, 64),
                     (4, 108, 64), (5, 109, 64), (6, 127, 64)])
hm = heat.note_heatmap(30)
step("only 21..108 returned", all(config.LOW_KEY <= n <= config.HIGH_KEY for n in hm),
     str(sorted(hm)))
step("out-of-range notes dropped", 20 not in hm and 109 not in hm and 127 not in hm)
step("counts correct", hm == {21: 2, 60: 1, 108: 1}, str(hm))

print("6. velocity: the Fixed-touch detector")
fixed = fresh("vel-fixed")
fid = fixed.start_session("grand-piano")
fixed.log_notes(fid, [(i * 10, 60 + (i % 12), 64) for i in range(200)])
step("all-64 session reports 1 distinct velocity", fixed.velocity_distinct(1) == 1,
     f"distinct={fixed.velocity_distinct(1)} -- piano is in Fixed Touch Sensitivity")
hbins = fixed.velocity_histogram(1, 16)
step("histogram puts them all in one bin", sum(1 for b in hbins if b) == 1 and sum(hbins) == 200,
     str(hbins))

varied = fresh("vel-varied")
vid = varied.start_session("grand-piano")
varied.log_notes(vid, [(i * 10, 60, 1 + (i % 127)) for i in range(400)])
step("varied session reports many", varied.velocity_distinct(1) == 127,
     f"distinct={varied.velocity_distinct(1)}")
hbins = varied.velocity_histogram(1, 16)
step("histogram spreads across bins", all(b > 0 for b in hbins) and sum(hbins) == 400,
     str(hbins))

print("7. weak_notes()")
sr = fresh("sightread")
srid = sr.start_session("grand-piano")


def attempts(note: int, n_correct: int, n_wrong: int, reaction: int) -> None:
    for _ in range(n_correct):
        sr.log_sightread(srid, note, True, reaction, "C", "treble")
    for _ in range(n_wrong):
        sr.log_sightread(srid, note, False, reaction, "C", "treble")


attempts(65, 4, 5, 890)    # 0.44
attempts(60, 5, 1, 400)    # 0.83
attempts(72, 1, 3, 1200)   # 0.25
attempts(50, 0, 2, 700)    # only 2 attempts -- must be filtered out
weak = sr.weak_notes()
step("worst correct-rate first", [w["note"] for w in weak] == [72, 65, 60],
     str([(w["note"], w["accuracy"]) for w in weak]))
step("minimum 3 attempts enforced", all(w["note"] != 50 for w in weak),
     "note 50 had 2 attempts")
step("counts and accuracy", weak[1] == {"note": 65, "attempts": 9, "correct": 4,
                                        "accuracy": 0.44, "mean_reaction_ms": 890},
     str(weak[1]))
step("limit honoured", len(sr.weak_notes(limit=2)) == 2)
summary = sr.sightread_summary(30)
step("summary totals", summary["attempts"] == 21 and summary["correct"] == 10,
     str(summary))

print("8. concurrency -- 4 threads, 200 notes each")
# Repeated, because the failure this catches is probabilistic: sqlite3's statement
# cache is not thread-safe, and a cursor released after the lock rather than under it
# loses a whole batch roughly half the time. One round would flip a coin. Store.log
# is watched too -- the loss is silent by design, swallowed as a sqlite3.Error, so a
# clean row count is only half the assertion.
ROUNDS = 8
lost_rows: list[str] = []
errors: list[BaseException] = []
swallowed: list[str] = []


class _Catch(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        swallowed.append(record.getMessage())


store_log = logging.getLogger("keys.store")
store_log.addHandler(_Catch())
store_log.setLevel(logging.WARNING)

hung = False
for round_index in range(ROUNDS):
    conc = fresh(f"concurrent-{round_index}")
    cid = conc.start_session("grand-piano")

    def hammer(thread_index: int, store: Store = conc, sess: int = cid) -> None:
        try:
            for batch in range(20):
                store.log_notes(sess, [(batch * 100 + i, 21 + ((thread_index * 20 + i) % 88), 64)
                                       for i in range(10)])
        except BaseException as exc:  # noqa: BLE001 -- the point is that nothing escapes
            errors.append(exc)

    threads = [threading.Thread(target=hammer, args=(i,), name=f"hammer-{i}") for i in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=30)
    hung = hung or any(th.is_alive() for th in threads)
    landed = count(conc, "note_event")
    if landed != 800:
        lost_rows.append(f"round {round_index}: {landed}/800")

step("no thread raised", not errors, str(errors))
step("no thread hung", not hung)
step(f"all 800 rows landed in every one of {ROUNDS} rounds", not lost_rows,
     "; ".join(lost_rows) if lost_rows else f"{ROUNDS * 800} rows total")
step("no write was silently swallowed", not swallowed, "; ".join(swallowed[:3]))
step("reads still work afterwards", sum(conc.note_heatmap(1).values()) == 800)

# close() landing *while a writer is parked on the lock*. Sleeping and hoping to hit
# that window is flaky, so the interleaving is forced: hold the lock, let the writer
# block on it, drop the connection exactly as close() does, then release. A store that
# reads self._conn outside the lock and dereferences it inside raises AttributeError
# here -- and sqlite3.Error handling does not catch that one.
for method, call in (("log_notes", lambda s, i: s.log_notes(i, [(0, 60, 64)])),
                     ("today", lambda s, i: s.today()),
                     ("discard_session", lambda s, i: s.discard_session(i))):
    racer = fresh(f"close-race-{method}")
    rid = racer.start_session("grand-piano")
    race_errors: list[BaseException] = []

    def race_writer(s: Store = racer, i: int = rid) -> None:
        try:
            call(s, i)
        except BaseException as exc:  # noqa: BLE001
            race_errors.append(exc)

    racer._lock.acquire()  # noqa: SLF001
    rt = threading.Thread(target=race_writer, name=f"race-{method}")
    rt.start()
    time.sleep(0.1)        # the writer is now parked on the lock it cannot get
    racer._conn.close()    # noqa: SLF001 -- what close() does, minus the lock we hold
    racer._conn = None     # noqa: SLF001
    racer._lock.release()  # noqa: SLF001
    rt.join(timeout=10)
    step(f"close() during an in-flight {method}() does not raise",
         not race_errors and not rt.is_alive(), str(race_errors))

print("9. a broken database degrades, it does not raise")
dead = fresh("dead")
dead.close()  # every call below now runs with self._conn = None
step("writes are silent no-ops", dead.start_session("x") == -1 and
     dead._write("INSERT INTO session(preset) VALUES ('x')") == -1 and  # noqa: SLF001
     count(dead, "session") == -1)  # -1: the read no-ops too, rather than raising
try:
    dead.discard_session(1)
    dead.discard_session(-1)
    discard_survived = True
except Exception as exc:  # noqa: BLE001
    discard_survived = False
    print(f"    discard_session raised: {exc!r}")
step("discard_session on a dead store is a no-op", discard_survived)
step("today() returns an empty shape", dead.today() ==
     {"active_seconds": 0, "note_count": 0, "sessions": 0, "first_at": None, "last_at": None})
step("history() still fills its days", len(dead.history(7)) == 7)
step("stats return empty, not None", dead.streak() == {"current": 0, "longest": 0,
                                                       "practiced_today": False,
                                                       "total_days": 0,
                                                       "total_active_seconds": 0}
     and dead.note_heatmap() == {} and dead.weak_notes() == []
     and dead.velocity_histogram(1, 8) == [0] * 8 and dead.recent_sessions() == [])

print("10. chords: log_chords, top_chords, chord_qualities")
ch = fresh("chords")
chid = ch.start_session("grand-piano")
ch.log_chords(chid, [(i * 1000, "Cmaj7", 0, "maj7", 0, 4) for i in range(5)]
              + [(20_000 + i * 1000, "Am", 9, "m", 9, 3) for i in range(3)]
              + [(40_000 + i * 1000, "C", 0, "", 0, 3) for i in range(2)]
              + [(60_000, "Dm7/A", 2, "m7", 9, 4)])
step("11 chord events landed", count(ch, "chord_event") == 11)
step("log_chords([]) is a no-op", (ch.log_chords(chid, []),
                                   count(ch, "chord_event"))[-1] == 11)
top = ch.top_chords(1)
step("top_chords ordered by count",
     [(c["symbol"], c["count"]) for c in top]
     == [("Cmaj7", 5), ("Am", 3), ("C", 2), ("Dm7/A", 1)], str(top))
step("top_chords carries root and quality",
     top[0]["root_pc"] == 0 and top[0]["quality"] == "maj7" and top[1]["root_pc"] == 9,
     str(top[0]))
step("limit honoured", len(ch.top_chords(1, limit=2)) == 2)
quals = ch.chord_qualities(1)
step("qualities are human-readable labels",
     quals == [{"quality": "major 7th", "count": 5}, {"quality": "minor", "count": 3},
               {"quality": "major", "count": 2}, {"quality": "minor 7th", "count": 1}],
     str(quals))
step('an empty quality reads as "major"', quals[2] == {"quality": "major", "count": 2},
     'detect_chord stores a plain triad as ""')
tossed = ch.start_session("grand-piano")
ch.log_chords(tossed, [(0, "G7", 7, "7", 7, 4)])
ch.discard_session(tossed)
step("discard_session takes the chord rows with it", count(ch, "chord_event") == 11)

print("11. what was played: pitch classes, octaves, range")
what = fresh("what")
wid = what.start_session("grand-piano")
PLAYED = [(0, 60, 64), (100, 60, 64), (200, 60, 64),   # C4 x3
          (300, 72, 64), (400, 72, 64),                # C5 x2
          (500, 61, 64),                               # C#4
          (600, 71, 64),                               # B4
          (700, 36, 64), (800, 96, 64)]                # the two range endpoints, both C
what.log_notes(wid, PLAYED)
pcs = what.pitch_class_histogram(1)
step("12 bins", len(pcs) == 12)
step("sums to every note logged", sum(pcs) == len(PLAYED), f"{sum(pcs)} of {len(PLAYED)}")
step("lands in the right bins", pcs[0] == 7 and pcs[1] == 1 and pcs[11] == 1,
     f"C={pcs[0]} C#={pcs[1]} B={pcs[11]}")
step("octaves are scientific -- 60 is C4",
     what.octave_histogram(1) == {2: 1, 4: 5, 5: 2, 7: 1}, str(what.octave_histogram(1)))
rng = what.range_used(1)
step("range endpoints and their names", rng["low"] == 36 and rng["high"] == 96
     and rng["low_name"] == "C2" and rng["high_name"] == "C7", str(rng))
step("span and coverage arithmetic",
     rng["span"] == 60 and rng["coverage"] == round(60 / 87, 3),
     f"coverage={rng['coverage']} = 60/87 of the 88 keys")
blank = fresh("range-empty")
step("no notes means Nones, not a crash",
     blank.range_used(1) == {"low": None, "high": None, "span": 0, "coverage": 0.0,
                             "low_name": None, "high_name": None}, str(blank.range_used(1)))

print("12. interval_histogram: a melody, then chords that must not count")
mel = fresh("intervals")
mid = mel.start_session("grand-piano")
# C4 D4 E4 C4, half a second apart: up M2, up M2, down M3.
mel.log_notes(mid, [(0, 60, 64), (500, 62, 64), (1000, 64, 64), (1500, 60, 64)])
iv = mel.interval_histogram(1)
step("the melody's exact intervals",
     [(i["semitones"], i["count"], i["name"]) for i in iv] == [(2, 2, "M2"), (4, 1, "M3")],
     str(iv))
# A C major triad struck as one gesture 1.5 s later. Its own pairs are 4 and 3
# semitones; neither may appear, and the M3 count must stay at 1.
mel.log_notes(mid, [(3000, 60, 64), (3010, 64, 64), (3020, 67, 64)])
iv = mel.interval_histogram(1)
step("a 10 ms-apart chord contributed nothing",
     [(i["semitones"], i["count"]) for i in iv] == [(2, 2), (4, 1)], str(iv))
step("no minor third appeared", all(i["semitones"] != 3 for i in iv),
     "64 -> 67 happened inside the chord window")
# Again with three identical t_ms values -- what the flush thread writes when a whole
# voicing lands inside one drain tick. G4 B4 D5: pairs of 4 and 3 semitones again.
mel.log_notes(mid, [(6000, 67, 64), (6000, 71, 64), (6000, 74, 64)])
iv = mel.interval_histogram(1)
step("identical timestamps are a chord too",
     [(i["semitones"], i["count"]) for i in iv] == [(2, 2), (4, 1)], str(iv))
mid2 = mel.start_session("grand-piano")
# t_ms deliberately *ahead* of the previous session's last note, so the pair across the
# boundary (74 -> 72, a whole tone) is only suppressed by the session check and not by
# the chord window. Then 72 -> 21 is 51 semitones: a hand moving, not a melodic leap.
mel.log_notes(mid2, [(10_000, 72, 64), (10_500, 21, 64)])
iv = mel.interval_histogram(1)
step("no interval crosses a session, and >24 semitones is dropped",
     [(i["semitones"], i["count"]) for i in iv] == [(2, 2), (4, 1)], str(iv))
step("limit honoured", len(mel.interval_histogram(1, limit=1)) == 1)

print("13. when it happened: hour and weekday")
when = fresh("when")
seed_at(when, local_epoch(0, 9, 0), 120_000)
seed_at(when, local_epoch(0, 9, 30), 60_000)
seed_at(when, local_epoch(0, 22, 0), 300_000)
hours = when.hour_histogram(1)
step("24 buckets", len(hours) == 24)
step("both 09:xx sessions land in hour 9", hours[9] == 180, f"hours[9]={hours[9]} s")
step("the 22:00 session lands in hour 22", hours[22] == 300, f"hours[22]={hours[22]} s")
step("and nowhere else", sum(hours) == 480 and sum(1 for h in hours if h) == 2, str(hours))

wk = fresh("weekday")
seed(wk, days_ago=0, active_ms=120_000)
seed(wk, days_ago=1, active_ms=60_000)
wdays = wk.weekday_histogram(7)
step("7 buckets", len(wdays) == 7)
step("index 0 is Monday", wdays[TODAY.weekday()] == 120,
     f"today is {TODAY.strftime('%A')} -> index {TODAY.weekday()}")
step("yesterday landed on yesterday's weekday",
     wdays[(TODAY - timedelta(days=1)).weekday()] == 60, str(wdays))
step("and nowhere else", sum(wdays) == 180, str(wdays))

print("14. dynamics and density per day")
dyn = fresh("dynamics")
old = seed(dyn, days_ago=3, active_ms=600_000, notes=900)
dyn.log_notes(old, [(i * 10, 60, 64) for i in range(10)])   # Fixed touch: every note 64
new = seed(dyn, days_ago=0, active_ms=300_000, notes=300)
dyn.log_notes(new, [(0, 60, 40), (10, 62, 60), (20, 64, 80), (30, 65, 100)])
also = seed(dyn, days_ago=0, active_ms=300_000, notes=900)   # same day, second session
dyn.log_notes(also, [(0, 67, 120)])
THEN = (TODAY - timedelta(days=3)).isoformat()
vel = dyn.velocity_by_day(7)
step("one row per day with notes, oldest first",
     [v["date"] for v in vel] == [THEN, TODAY.isoformat()], str([v["date"] for v in vel]))
step("the fixed-touch day",
     vel[0] == {"date": THEN, "mean": 64.0, "min": 64, "max": 64, "distinct": 1}, str(vel[0]))
step("today merges both sessions before averaging",
     vel[1] == {"date": TODAY.isoformat(), "mean": 80.0, "min": 40, "max": 120, "distinct": 5},
     str(vel[1]))
npm = dyn.notes_per_minute(7)
step("npm rows ascend by date", [n["date"] for n in npm] == [THEN, TODAY.isoformat()],
     str([n["date"] for n in npm]))
step("900 notes in 600 active seconds is 90 npm",
     npm[0] == {"date": THEN, "npm": 90.0, "notes": 900, "active_seconds": 600}, str(npm[0]))
step("same-day sessions merge before dividing",
     npm[1] == {"date": TODAY.isoformat(), "npm": 120.0, "notes": 1200, "active_seconds": 600},
     str(npm[1]))
zero = fresh("npm-zero")
seed(zero, days_ago=0, active_ms=0, notes=5)
step("a zero-second day does not divide by zero",
     zero.notes_per_minute(1) == [{"date": TODAY.isoformat(), "npm": 0.0, "notes": 5,
                                   "active_seconds": 0}], str(zero.notes_per_minute(1)))

print("15. session shape: lengths, presets, totals")
shape = fresh("shape")
seed(shape, days_ago=0, active_ms=12 * 60_000, notes=100)                    # 12:00 -> bucket 10
seed(shape, days_ago=0, active_ms=14 * 60_000 + 59_000, notes=200)           # 14:59 -> bucket 10
seed(shape, days_ago=1, active_ms=15 * 60_000, notes=300, preset="rhodes")   # 15:00 -> bucket 15
seed(shape, days_ago=8, active_ms=90_000, notes=40, preset="rhodes")         # 1:30 -> bucket 0
step("5-minute buckets on the low edge, ascending",
     shape.session_lengths(30) == [{"minutes": 0, "count": 1}, {"minutes": 10, "count": 2},
                                   {"minutes": 15, "count": 1}], str(shape.session_lengths(30)))
step("preset usage by seconds, most-played first",
     shape.preset_usage(30) == [{"preset": "grand-piano", "seconds": 1619, "sessions": 2},
                                {"preset": "rhodes", "seconds": 990, "sessions": 2}],
     str(shape.preset_usage(30)))
live_sid = shape.start_session("grand-piano")
shape.log_chords(live_sid, [(0, "C", 0, "", 0, 3), (100, "G7", 7, "7", 7, 4)])
tot = shape.totals(30)
step("totals count sessions, notes and calendar days",
     tot["sessions"] == 5 and tot["note_count"] == 640 and tot["days_practiced"] == 3, str(tot))
step("totals count chord events", tot["chords"] == 2, str(tot))
step("active_seconds is the window's sum", tot["active_seconds"] == 1619 + 990, str(tot))
step("first_at is the oldest session in the window", tot["first_at"] == noon(8),
     str(tot["first_at"]))
step("the window really is a window", shape.totals(2)["sessions"] == 4,
     str(shape.totals(2)))

print("16. analytics on a broken database")
broken = fresh("dead-analytics")
broken.close()
try:
    broken.log_chords(1, [(0, "C", 0, "", 0, 3)])
    chord_write_survived = True
except Exception as exc:  # noqa: BLE001
    chord_write_survived = False
    print(f"    log_chords raised: {exc!r}")
step("log_chords on a dead store is a no-op", chord_write_survived)
step("chord stats are empty lists",
     broken.top_chords() == [] and broken.chord_qualities() == [])
step("note shapes keep their shape", broken.pitch_class_histogram() == [0] * 12
     and broken.octave_histogram() == {} and broken.interval_histogram() == [])
step("time histograms keep their length",
     broken.hour_histogram() == [0] * 24 and broken.weekday_histogram() == [0] * 7)
step("per-day series are empty",
     broken.velocity_by_day() == [] and broken.notes_per_minute() == []
     and broken.session_lengths() == [] and broken.preset_usage() == [])
step("range_used is None-shaped",
     broken.range_used() == {"low": None, "high": None, "span": 0, "coverage": 0.0,
                             "low_name": None, "high_name": None}, str(broken.range_used()))
step("totals is zero-shaped",
     broken.totals() == {"active_seconds": 0, "note_count": 0, "sessions": 0, "chords": 0,
                         "days_practiced": 0, "first_at": None}, str(broken.totals()))

print("17. the real keys.db was never touched")
after = (REAL_DB.exists(), REAL_DB.stat().st_mtime_ns if REAL_DB.exists() else 0,
         REAL_DB.stat().st_size if REAL_DB.exists() else 0)
step("keys.db unchanged", after == REAL_BEFORE,
     f"{REAL_DB} exists={after[0]} (was {REAL_BEFORE[0]})")

for store in stores:
    store.close()
shutil.rmtree(TMP, ignore_errors=True)
print()
print("ALL CHECKS PASSED" if ok else "SOMETHING FAILED")
sys.exit(0 if ok else 1)
