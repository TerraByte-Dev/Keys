"""End-to-end test of everything above the physical MIDI port.

Drives synthetic notes through the exact path a real key takes -- engine.note_on then
hub.push, which is what the rtmidi callback does -- and asserts on the websocket frames
that come out the other side. Covers chord detection, the practice clock, sight-reading
grading, held-note resync and the status frame.

The one thing it cannot cover is the port itself. That needs the piano, and
`tools/midi_probe.py` is the tool for it.

    .venv\\Scripts\\python.exe tools\\pipeline_check.py

Uses a temp database and temp settings, so it never touches keys.db or config.local.json.
It does open the audio device in exclusive mode -- stop the app before running it.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _audio_guard import require_quiet  # noqa: E402
from backend import config  # noqa: E402

# Redirect persistence BEFORE importing the server, because it builds its App at
# module import and that is what creates the database.
_TMP = Path(tempfile.mkdtemp(prefix="keys-pipeline-"))
config.DB_PATH = _TMP / "test.db"
config.SETTINGS_PATH = _TMP / "settings.json"
config.settings = config.Settings(config.SETTINGS_PATH)

from backend import hub as hub_mod  # noqa: E402
from backend.server import App  # noqa: E402

ok = True


def step(label: str, passed: bool, detail: str = "") -> None:
    global ok
    ok = ok and passed
    print(f"  [{'PASS' if passed else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))


class FakeSocket:
    """Stands in for a browser. Collects every frame the server broadcasts."""

    def __init__(self) -> None:
        self.frames: list[dict] = []

    async def send_text(self, text: str) -> None:
        self.frames.append(json.loads(text))

    def take(self, kind: str) -> list[dict]:
        return [f for f in self.frames if f.get("t") == kind]

    def clear(self) -> None:
        self.frames.clear()


def strike(app: App, note: int, velocity: int) -> None:
    """Exactly what midi_in's callback does for a note-on, in the same order."""
    t = time.perf_counter()
    app.engine.note_on(note, velocity)
    app.hub.push(t, hub_mod.NOTE_ON, note, velocity, 0.000012)


def release(app: App, note: int) -> None:
    t = time.perf_counter()
    app.engine.note_off(note)
    app.hub.push(t, hub_mod.NOTE_OFF, note, 0, 0.000009)


def control(app: App, cc: int, value: int) -> None:
    t = time.perf_counter()
    app.engine.control(cc, value)
    app.hub.push(t, hub_mod.CONTROL, cc, value, 0.000008)


async def settle(seconds: float = 0.12) -> None:
    """Let the 60 Hz drain loop run. Not a timing assertion -- just yielding."""
    await asyncio.sleep(seconds)


async def main() -> int:
    require_quiet("pipeline_check")
    print("1. app starts")
    app = App()
    app.startup()
    step("engine started", app.engine.started, f"{app.engine.status()['buffer_ms']} ms buffer")
    step("preset loaded", bool(app.engine.zones), app.engine.preset_name)
    if not app.engine.started:
        print("\nengine did not start -- is the app already running and holding the audio device?")
        return 1

    # The default has to be ONE zone across the whole keyboard. A split at startup is a
    # keyboard cut in half with nothing on screen saying why, and it is what you get by
    # accident the moment loading a preset is allowed to pin it.
    step("startup default is one zone end to end",
         len([z for z in app.engine.zones if z.enabled]) == 1
         and app.engine.zones[0].lo == config.LOW_KEY
         and app.engine.zones[0].hi == config.HIGH_KEY,
         f"{len(app.engine.zones)} zone(s): {app.engine.preset_name}")

    split = next((p for p in app.presets.values() if len(p.zones) > 1), None)
    if split is not None:
        was = app.settings.get("preset")
        app.engine.set_zones(split.zones, split.id, split.name)
        app.practice.preset = split.id
        step("a split can still be loaded", len(app.engine.zones) > 1, split.name)
        # The regression this exists for: trying a split out of curiosity must not
        # change what Keys opens with tomorrow.
        step("loading it does NOT become the startup sound",
             app.settings.get("preset") == was, f"still {was!r}")
        app.settings.update({"preset": split.id})
        step("but choosing it deliberately does",
             app.settings.get("preset") == split.id)
        app.settings.update({"preset": was})

    sock = FakeSocket()
    app.clients.add(sock)
    task = asyncio.create_task(app.drain_loop())
    await settle(0.2)

    print("2. a C major triad")
    sock.clear()
    for n in (60, 64, 67):
        strike(app, n, 96)
    await settle()
    frames = sock.take("f")
    step("frames broadcast", len(frames) > 0, f"{len(frames)} frame(s)")
    last = frames[-1] if frames else {}
    step("held set is right", sorted(last.get("held", [])) == [60, 64, 67], str(last.get("held")))
    step("chord detected", (last.get("chord") or {}).get("symbol") == "C",
         str((last.get("chord") or {}).get("symbol")))
    step("note names spelled", last.get("names") == ["C4", "E4", "G4"], str(last.get("names")))
    step("engine agrees", app.engine.held_notes() == [60, 64, 67], str(app.engine.held_notes()))

    print("3. first inversion is a slash chord")
    sock.clear()
    release(app, 60)
    strike(app, 72, 96)
    await settle()
    last = sock.take("f")[-1]
    step("C/E", (last.get("chord") or {}).get("symbol") == "C/E",
         str((last.get("chord") or {}).get("symbol")))

    print("4. sustain pedal")
    sock.clear()
    control(app, 64, 127)
    await settle()
    step("sustain reported on", sock.take("f")[-1].get("sus") is True)
    control(app, 64, 0)
    await settle()
    step("sustain reported off", sock.take("f")[-1].get("sus") is False)

    print("5. all keys released")
    sock.clear()
    for n in (64, 67, 72):
        release(app, n)
    await settle()
    last = sock.take("f")[-1]
    step("nothing held", last.get("held") == [], str(last.get("held")))
    step("chord cleared", last.get("chord") is None, str(last.get("chord")))

    print("6. the practice clock counts playing, not sitting")
    p = app.practice
    step("session opened on the first note", p.session_id is not None, f"id={p.session_id}")
    step("notes counted", p.note_count == 4, f"{p.note_count} note-ons")
    # Three notes struck within a few ms of each other must credit ~0 ms, not 3 x idle.
    step("chord did not inflate the clock", p.active_ms < 500, f"{p.active_ms} ms")

    base = time.perf_counter()
    p.on_note(base, 60, 80)
    before = p.active_ms   # after the anchor note, so only the synthetic gap is measured
    p.on_note(base + 3.0, 62, 80)                 # 3 s: inside the grace window
    step("a short gap counts in full", 2950 <= p.active_ms - before <= 3050,
         f"+{p.active_ms - before} ms for a 3 s gap")

    before = p.active_ms
    # 60 s: past the grace window but short of the session gap, so it must credit
    # exactly the grace window rather than the whole minute of staring at the wall.
    p.on_note(base + 63.0, 64, 80)
    credited = p.active_ms - before
    step("a long gap credits only the grace window",
         abs(credited - p.idle_seconds * 1000) < 50,
         f"+{credited} ms for a 60 s gap, grace is {p.idle_seconds:.0f} s")

    # Past SESSION_GAP_SECONDS the session closes itself and the next note opens a
    # new one, so you never have to remember to press stop.
    old_session = p.session_id
    p.on_note(base + 63.0 + 400.0, 65, 80)
    step("a very long gap starts a new session", p.session_id != old_session,
         f"{old_session} -> {p.session_id} after a {400:.0f} s silence")

    print("6b. chords are logged once they settle, and only once per press")
    app.practice._flush()  # noqa: SLF001 -- start from a known committed state
    before = len(app.store.top_chords(days=1, limit=50))
    sock.clear()
    for n in (60, 64, 67):
        strike(app, n, 92)
    # Hold past the settle window. A chord logged instantly would be wrong: rolling
    # into a voicing reads as C, then C5, then Cmaj7 on the way to one chord.
    await settle(0.30)
    step("a held chord settles and is logged", app._chord_logged == "C",  # noqa: SLF001
         f"logged={app._chord_logged!r}")  # noqa: SLF001
    await settle(0.20)
    step("holding longer does not log it again", app._chord_logged == "C",  # noqa: SLF001
         "the same symbol is not re-logged while it is still held")

    for n in (60, 64, 67):
        release(app, n)
    await settle()
    step("release clears the latch", app._chord_logged is None,  # noqa: SLF001
         "so the same chord counts again next time you play it")

    for n in (60, 64, 67):
        strike(app, n, 92)
    await settle(0.30)
    for n in (60, 64, 67):
        release(app, n)
    await settle()
    app.practice._flush()  # noqa: SLF001
    rows = {r["symbol"]: r["count"] for r in app.store.top_chords(days=1, limit=50)}
    step("playing it twice counts twice", rows.get("C") == 2, f"top_chords={rows}")
    step("chord analytics see it", len(app.store.top_chords(days=1, limit=50)) > before,
         f"{before} -> {len(app.store.top_chords(days=1, limit=50))} distinct chord(s)")

    # A chord that is only brushed through must not be recorded.
    strike(app, 62, 90)
    strike(app, 65, 90)
    strike(app, 69, 90)
    await settle(0.05)          # well under CHORD_SETTLE_SECONDS
    for n in (62, 65, 69):
        release(app, n)
    await settle()
    app.practice._flush()  # noqa: SLF001
    rows = {r["symbol"]: r["count"] for r in app.store.top_chords(days=1, limit=50)}
    step("a chord brushed through is not logged", "Dm" not in rows, f"top_chords={rows}")

    print("7. sight reading grades and advances")
    app.sight.session_id = app.practice.session_id
    ex = app.sight.new_exercise()
    step("exercise generated", len(ex["notes"]) == ex["config"]["notes_per_measure"],
         f"{len(ex['notes'])} notes, key {ex['config']['key']}")
    step("notes are in range",
         all(ex["config"]["low"] <= n["midi"] <= ex["config"]["high"] for n in ex["notes"]))
    step("notes are named and assigned a staff",
         all(n["name"] and n["staff"] in ("treble", "bass") for n in ex["notes"]),
         ", ".join(f"{n['name']}/{n['staff'][0]}" for n in ex["notes"]))

    target = ex["notes"][0]["midi"]
    wrong = target + 1 if target < 108 else target - 1
    sock.clear()
    strike(app, wrong, 90)
    await settle()
    release(app, wrong)
    fb = next((f["sight"] for f in sock.take("f") if "sight" in f), None)
    step("a wrong note is marked wrong", fb is not None and fb["correct"] is False, str(fb))
    step("a wrong note does not advance", app.sight.index == 0, f"index={app.sight.index}")

    sock.clear()
    strike(app, target, 90)
    await settle()
    release(app, target)
    fb = next((f["sight"] for f in sock.take("f") if "sight" in f), None)
    step("the right note is accepted", fb is not None and fb["correct"] is True, str(fb))
    step("and advances", app.sight.index == 1, f"index={app.sight.index}")

    # Only the FIRST attempt at a target is scored. Hunting for the key after a miss
    # is already recorded as the miss; counting each wrong key again would bury it.
    # So miss-then-hit on one target is one attempt, scored wrong -- which is the
    # honest reading of "did you read that note correctly".
    step("one attempt scored per target, not per keypress",
         app.sight.total == 1 and app.sight.correct == 0,
         f"{app.sight.correct}/{app.sight.total} after a miss then a hit on the same target")

    for note in ex["notes"][1:]:
        strike(app, note["midi"], 90)
        await settle(0.05)
        release(app, note["midi"])
    await settle()
    step("measure completes", app.sight.active is False, f"index={app.sight.index}")

    print("8. status frame")
    sock.clear()
    await settle(1.3)
    st = sock.take("s")
    step("status broadcast at ~1 Hz", len(st) >= 1, f"{len(st)} in 1.3 s")
    s = st[-1]
    for key in ("engine", "midi", "metronome", "practice", "hub", "timing", "held"):
        step(f"status carries '{key}'", key in s)
    step("latency is measured and labelled",
         s["hub"]["latency"].get("n", 0) > 0 and "Excludes" in s["hub"]["latency"].get("note", ""),
         f"median {s['hub']['latency'].get('median_us')} us")

    print("9. a stuck UI is corrected by the engine's own held list")
    app.held = {21, 22, 23}          # pretend the browser missed three note-offs
    frame = app.status_frame()
    step("resync from ground truth", frame["held"] == [] and app.held == set(),
         "engine held nothing, so the status frame clears the phantom keys")

    print("10. dropped frames are counted, not hidden")
    for i in range(hub_mod.QUEUE_LIMIT + 64):
        app.hub.push(time.perf_counter(), hub_mod.NOTE_ON, 60, 90, 0.00001)
    step("overflow is reported", app.hub.stats()["dropped"] > 0,
         f"{app.hub.stats()['dropped']} dropped after overfilling a {hub_mod.QUEUE_LIMIT} deep queue")
    app.hub.drain()

    print("11. shutdown")
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    app.shutdown()
    step("engine stopped", not app.engine.started)
    step("real keys.db untouched", not (config.ROOT / "keys.db").samefile(config.DB_PATH)
         if (config.ROOT / "keys.db").exists() else True, f"test db was {config.DB_PATH.name}")

    print()
    print("ALL CHECKS PASSED" if ok else "SOMETHING FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
