"""Regression test for the loop station: takes, timing, the pedal, and the seam.

    .venv\\Scripts\\python.exe tools\\looper_check.py

Opens the audio device and makes noise -- it plays a real loop back at the end, which
is the only way to know the sequencer actually got the events. Refuses to run while
Keys is up, like every other audio check here.

The interesting assertions are the ones about time. A looper is easy to write and hard
to write *correctly*: the failure modes are all fractions of a beat, they all sound
like "you played it badly", and none of them show up in a screenshot.
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _audio_guard import require_quiet  # noqa: E402
from backend import config  # noqa: E402
from backend.engine import Engine, Zone  # noqa: E402
from backend.hub import CONTROL, NOTE_OFF, NOTE_ON  # noqa: E402
from backend.looper import (  # noqa: E402
    LAYER_CHANNELS, MAX_LAYERS, PLAYING, RECORDING, STOPPED, Layer, LoopStation, Note,
)
from backend.metronome import Metronome  # noqa: E402

SCRATCH_DIR = Path(tempfile.mkdtemp(prefix="keys-loop-"))
SCRATCH = config.Settings(SCRATCH_DIR / "settings.json")

ok = True


def step(label: str, passed: bool, detail: str = "") -> None:
    global ok
    ok = ok and passed
    print(f"  [{'PASS' if passed else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))


def wait_until(pred, timeout: float = 12.0, poll: float = 0.01) -> bool:
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        if pred():
            return True
        time.sleep(poll)
    return False


require_quiet("looper_check")

eng = Engine(SCRATCH)
eng.start()
metro = Metronome(eng, SCRATCH)
loop = LoopStation(eng, metro, SCRATCH)
# 120 bpm / 4-4 => 500 ms a beat, 2000 ms a bar. Round numbers make every timing
# assertion below readable as milliseconds rather than as arithmetic.
SCRATCH.update({"metronome": {"bpm": 120, "beats_per_bar": 4, "ramp_enabled": False}})
eng.set_zones([Zone()], "default", "Acoustic Grand")

print("1. the grid comes from the metronome, not from a second tempo field")
beat_ms, bar_ms, beats = metro.grid()
step("beat is 500 ms at 120 bpm", abs(beat_ms - 500.0) < 0.01, f"{beat_ms:.1f} ms")
step("bar is 4 beats", beats == 4 and abs(bar_ms - 2000.0) < 0.01, f"{bar_ms:.1f} ms")
step("no tempo key of its own", "bpm" not in loop.cfg() and "bars" in loop.cfg(),
     str(sorted(loop.cfg())))

print("2. config clamps rather than trusts")
SCRATCH.update({"loop": {"bars": 999, "count_in_bars": 77}})
step("bars clamped to 32", loop.cfg()["bars"] == 32)
step("count-in clamped to 4", loop.cfg()["count_in_bars"] == 4)
SCRATCH.update({"loop": {"bars": 1, "count_in_bars": 0, "click": False}})
step("bars floor is 1", loop.cfg()["bars"] == 1)

print("3. events -> notes, with the pedal resolved into length")
# Hand-built event stream at known offsets from a synthetic take start. The anchor is
# forced so _tick_at is the identity in milliseconds and every number below is exact.
loop.loop_ms = 2000.0
loop._anchor = (0.0, 0.0)  # noqa: SLF001


def ev(ms: float, kind: int, a: int, b: int = 0) -> tuple[float, int, int, int]:
    return (ms / 1000.0, kind, a, b)


loop._events = [  # noqa: SLF001
    ev(0, NOTE_ON, 60, 100), ev(400, NOTE_OFF, 60),
    ev(500, NOTE_ON, 64, 90), ev(900, NOTE_OFF, 64),
]
notes = loop._build_notes(0.0, 2000.0)  # noqa: SLF001
step("two notes recorded", len(notes) == 2, f"{[n.key for n in notes]}")
step("positions exact", [round(n.pos) for n in notes] == [0, 500],
     str([round(n.pos, 1) for n in notes]))
step("durations exact", [round(n.dur) for n in notes] == [400, 400],
     str([round(n.dur, 1) for n in notes]))
step("velocity preserved", [n.vel for n in notes] == [100, 90])

loop._events = [  # noqa: SLF001
    ev(0, CONTROL, 64, 127),            # pedal down
    ev(100, NOTE_ON, 60, 80), ev(200, NOTE_OFF, 60),
    ev(300, NOTE_ON, 67, 80), ev(400, NOTE_OFF, 67),
    ev(1200, CONTROL, 64, 0),           # pedal up -- both stop here
]
notes = loop._build_notes(0.0, 2000.0)  # noqa: SLF001
step("pedal holds both notes", len(notes) == 2, str(len(notes)))
step("held to the pedal lift, not the key lift",
     all(abs((n.pos + n.dur) - 1200) < 0.5 for n in notes),
     str([round(n.pos + n.dur) for n in notes]))

loop._events = [ev(-40, NOTE_ON, 60, 90), ev(160, NOTE_OFF, 60)]  # noqa: SLF001
notes = loop._build_notes(0.0, 2000.0)  # noqa: SLF001
step("a note 40 ms early lands on the downbeat", len(notes) == 1 and notes[0].pos == 0.0,
     "pre-roll, not a wrap to the end of the loop")

loop._events = [ev(-400, NOTE_ON, 60, 90), ev(-200, NOTE_OFF, 60)]  # noqa: SLF001
step("a note 400 ms early is not in the take", loop._build_notes(0.0, 2000.0) == [],  # noqa: SLF001
     "outside the pre-roll window")

loop._events = [ev(100, NOTE_ON, 60, 90)]  # noqa: SLF001
notes = loop._build_notes(0.0, 2000.0)  # noqa: SLF001
step("a note never released is clamped to the loop end",
     len(notes) == 1 and abs(notes[0].pos + notes[0].dur - 2000.0) < 0.5,
     f"{notes[0].dur:.0f} ms")

loop._events = [  # noqa: SLF001
    ev(0, NOTE_ON, 60, 90), ev(300, NOTE_ON, 60, 100), ev(600, NOTE_OFF, 60),
]
notes = loop._build_notes(0.0, 2000.0)  # noqa: SLF001
step("a retrigger without a note-off closes the first",
     len(notes) == 2 and [round(n.pos) for n in notes] == [0, 300],
     "two notes, not one 600 ms one")

loop._events = []  # noqa: SLF001

print("4. layer sound is taken from the zone you played the take in")
low = Zone(id="lo", name="Bass", lo=21, hi=59, channel=0, bank=0, program=32)
high = Zone(id="hi", name="Piano", lo=60, hi=108, channel=1, bank=0, program=0)
eng.set_zones([low, high], "split", "Split")
bass = Layer(id="a", name="x", channel=10)
loop._adopt_sound(bass, 40)  # noqa: SLF001
step("a take starting low adopts the bass zone", bass.program == 32 and bass.name == "Bass",
     f"program={bass.program}")
treble = Layer(id="b", name="x", channel=11)
loop._adopt_sound(treble, 72)  # noqa: SLF001
step("a take starting high adopts the piano zone", treble.program == 0,
     f"program={treble.program}")

print("5. channel allocation dodges live zones and stops at the ceiling")
eng.set_zones([Zone(channel=10)], "clash", "Clash")
step("a zone on 10 makes 10 unavailable", loop._free_channel() == 11)  # noqa: SLF001
eng.set_zones([Zone()], "default", "Acoustic Grand")
loop.layers = [Layer(id=str(i), name=f"L{i}", channel=c) for i, c in enumerate(LAYER_CHANNELS)]
step("full house has no free channel", loop._free_channel() is None,  # noqa: SLF001
     f"{MAX_LAYERS} layers")
loop.arm()
step("arming a full loop explains itself", "ceiling" in loop.last_error,
     loop.last_error[:48])
step("and does not start the transport", loop.state == STOPPED)
loop.layers = []
loop.last_error = ""

print("6. save and load round-trip through recordings/")
config.RECORDING_DIR.mkdir(parents=True, exist_ok=True)
saved_path = config.RECORDING_DIR / "keys-selftest.loop.json"
loop.layers = [Layer(id="z", name="Test", channel=10, program=48,
                     notes=[Note(pos=0.0, key=60, vel=100, dur=400.0),
                            Note(pos=1000.0, key=67, vel=90, dur=400.0)])]
loop.bars = 2
loop.save("keys-selftest")
step("file written", saved_path.exists(), saved_path.name)
step("appears in the saved list",
     any(s["name"] == "keys-selftest" for s in loop.saved()))
loop.layers = []
loop.load("keys-selftest")
step("layers restored", len(loop.layers) == 1 and len(loop.layers[0].notes) == 2)
step("note data restored exactly",
     loop.layers[0].notes[1].pos == 1000.0 and loop.layers[0].notes[1].key == 67)
step("tempo restored with it", abs(metro.cfg()["bpm"] - 120.0) < 0.01,
     f"{metro.cfg()['bpm']} bpm -- a loop recorded at 92 is not a loop at 120")
loop.load("no-such-loop-at-all")
step("a missing loop is an error, not a crash", "no saved loop" in loop.last_error)
saved_path.unlink(missing_ok=True)
loop.layers = []
loop.last_error = ""

print("7. the transport runs, and the click shares its grid")
SCRATCH.update({"loop": {"bars": 1, "count_in_bars": 1, "click": True}})
# Deliberately ON in the saved settings, so the next two assertions can tell the
# difference between "the loop suppressed the ramp" and "the ramp was already off".
SCRATCH.update({"metronome": {"ramp_enabled": True}})
loop.start()
step("transport rolls", loop.state in ("counting", "playing"), loop.state)
step("bar 1 is exactly one count-in bar after click 0",
     abs((loop.origin - metro.start_tick) - bar_ms) < 1.0,
     f"gap={loop.origin - metro.start_tick:.1f} ms, bar={bar_ms:.0f} ms")
step("tempo ramp suppressed while looping", metro.cfg()["ramp_enabled"] is False,
     "a click that speeds up cannot share a fixed-length loop")
step("suppression is an overlay, not a saved setting",
     SCRATCH.get("metronome", "ramp_enabled") is True,
     "override() must never write to config.local.json")
step("tempo is reported as locked while running", loop.status()["tempo_locked"])
step("count-in state is reported", loop.status()["state"] in ("counting", "playing"))
step("reached playing", wait_until(lambda: loop.state == PLAYING, 8.0), loop.state)
pos_a = loop.status()["position"]
time.sleep(0.35)
pos_b = loop.status()["position"]
step("the playhead moves", pos_a != pos_b, f"{pos_a:.3f} -> {pos_b:.3f}")
step("and stays inside the loop", 0.0 <= pos_b <= 1.0)
cyc = loop.status()["cycle"]
step("cycles advance", wait_until(lambda: loop.status()["cycle"] > cyc, 6.0),
     f"cycle {cyc} -> {loop.status()['cycle']}")

print("8. a take is captured on the bar line and plays back")
loop.arm()
step("armed", loop.status()["armed"])
step("recording starts at a bar line", wait_until(lambda: loop.state == RECORDING, 6.0),
     loop.state)
rec_start = loop._rec_start  # noqa: SLF001
step("the take starts on a cycle boundary",
     abs((rec_start - loop.origin) % loop.loop_ms) < 0.01,
     "no fractional-bar takes")
# Play four notes through the same door a real take uses.
for i, key in enumerate((60, 64, 67, 72)):
    t = time.perf_counter()
    loop.on_event(t, NOTE_ON, key, 96)
    loop.on_event(t + 0.12, NOTE_OFF, key, 0)
    time.sleep(0.1)
step("take finishes by itself", wait_until(lambda: loop.state == PLAYING, 8.0), loop.state)
step("a layer was created", len(loop.layers) == 1, f"{len(loop.layers)} layers")
if loop.layers:
    layer = loop.layers[0]
    step("it caught the notes", len(layer.notes) == 4, f"{len(layer.notes)} notes")
    step("on a reserved channel", layer.channel in LAYER_CHANNELS, f"ch{layer.channel}")
    step("in order and inside the loop",
         all(0 <= n.pos < loop.loop_ms for n in layer.notes)
         and [n.pos for n in layer.notes] == sorted(n.pos for n in layer.notes))
    step("voices sound on the next pass",
         wait_until(lambda: eng.voice_count() > 0, 6.0), f"{eng.voice_count()} voices")

    print("9. mute is immediate, delete is within a bar")
    loop.update_layer(layer.id, {"muted": True})
    step("mute reported", loop.status()["layers"][0]["muted"])
    step("mute silences within a bar",
         wait_until(lambda: eng.voice_count() == 0, 5.0), f"{eng.voice_count()} voices")
    loop.update_layer(layer.id, {"muted": False, "gain": 0.5, "name": "Bassline"})
    step("gain and name applied",
         loop.status()["layers"][0]["gain"] == 0.5
         and loop.status()["layers"][0]["name"] == "Bassline")
    loop.delete_layer(layer.id)
    step("deleted", not loop.layers)

print("10. the click and the loop do not cancel each other")
loop.arm()
wait_until(lambda: loop.state == RECORDING, 6.0)
for key in (55, 59):
    t = time.perf_counter()
    loop.on_event(t, NOTE_ON, key, 90)
    loop.on_event(t + 0.15, NOTE_OFF, key, 0)
    time.sleep(0.12)
wait_until(lambda: loop.state == PLAYING, 8.0)
step("second take recorded", len(loop.layers) == 1, f"{len(loop.layers)} layers")
# This is the whole reason both schedulers tag their events with a source client: a
# tempo change used to flush the entire sequencer queue, loop included.
metro.configure({"bpm": 132})
time.sleep(0.4)
step("metronome still running after a tempo change", metro.status()["running"])
step("the loop survived the tempo change", len(loop.layers) == 1 and loop.state == PLAYING,
     "source-scoped remove_events")
step("the loop still sounds", wait_until(lambda: eng.voice_count() > 0, 6.0),
     f"{eng.voice_count()} voices")
step("but says so -- the bar length no longer matches the take",
     loop.status()["desynced"],
     "a recorded layer is absolute milliseconds; moving the tempo under it is a lie")

print("11. stop puts everything back")
loop.stop()
step("stopped", loop.state == STOPPED)
step("ramp override released", metro._overlay == {}, str(metro._overlay))  # noqa: SLF001
step("layers kept, not discarded", len(loop.layers) == 1)
step("nothing left sounding", wait_until(lambda: eng.voice_count() == 0, 4.0),
     f"{eng.voice_count()} voices")
loop.clear()
step("clear empties the loop", not loop.layers)

metro.shutdown()
loop.shutdown()
eng.stop()

print()
print("ALL CHECKS PASSED" if ok else "FAILURES ABOVE")
sys.exit(0 if ok else 1)
