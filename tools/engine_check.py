"""Regression test for the synth layer: zones, presets, drum banks, metronome timing.

Runs without the piano and without the browser. If this passes, the sound engine is
good and any problem is above it.

    .venv\\Scripts\\python.exe tools\\engine_check.py

It makes noise on purpose -- the metronome section plays about six seconds of clicks.
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _audio_guard import require_quiet  # noqa: E402
from backend import config, engine as engine_mod  # noqa: E402
from backend.engine import Engine  # noqa: E402
from backend.metronome import Metronome  # noqa: E402

# Throwaway settings file. A test that writes to config.local.json leaves the app
# configured however the last assertion happened to leave it -- which is exactly how
# a stale ramp ceiling silently held the tempo at 100 bpm the first time around.
SCRATCH = config.Settings(Path(tempfile.mkdtemp(prefix="keys-check-")) / "settings.json")

ok = True


def step(label: str, passed: bool, detail: str = "") -> None:
    global ok
    ok = ok and passed
    print(f"  [{'PASS' if passed else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))


require_quiet("engine_check")

print("1. engine starts on the measured settings")
eng = Engine(SCRATCH)
eng.start()
st = eng.status()
step("started", eng.started, f"{st['buffer_ms']} ms buffer, exclusive={st['exclusive']}")
step("sequencer registered", eng.sequencer is not None and eng.seq_dest is not None,
     f"dest={eng.seq_dest}")

print("2. soundfont + preset enumeration")
presets = eng.list_presets()
drums = [p for p in presets if p["drums"]]
step("presets enumerated", len(presets) > 150, f"{len(presets)} presets, {len(drums)} drum kits")
step("bank 0 program 0 exists", any(p["bank"] == 0 and p["program"] == 0 for p in presets),
     next((p["name"] for p in presets if p["bank"] == 0 and p["program"] == 0), "?"))

print("3. presets/*.json load")
files = engine_mod.load_presets()
step("preset files parsed", len(files) >= 8, f"{len(files)}: {', '.join(sorted(files))}")

print("4. zone routing")
grand = files.get("grand-piano")
warn = eng.set_zones(grand.zones, grand.id, grand.name)
step("single zone applies", not warn and len(eng.routes[60]) == 1, f"warnings={warn}")
step("route respects range", eng.routes[20] == () and len(eng.routes[21]) == 1,
     "A0 routed, nothing below it")

split = files.get("bass-split")
eng.set_zones(split.zones, split.id, split.name)
step("split: low key -> ch0", len(eng.routes[36]) == 1 and eng.routes[36][0][0] == 0)
step("split: high key -> ch1", len(eng.routes[72]) == 1 and eng.routes[72][0][0] == 1)
step("split: no key in both", all(len(eng.routes[n]) <= 1 for n in range(21, 109)))

layer = files.get("piano-strings")
eng.set_zones(layer.zones, layer.id, layer.name)
step("layer: middle C hits two channels", len(eng.routes[60]) == 2,
     f"channels {[r[0] for r in eng.routes[60]]}")

pads = files.get("drum-pads")
warn = eng.set_zones(pads.zones, pads.id, pads.name)
step("drum zone applies with bank 128", not warn, f"warnings={warn}")
step("drum kit selected on ch9", eng.fs.channel_info(9)[1] == 128,
     f"channel_info(9) = {eng.fs.channel_info(9)}")

print("5. held-note bookkeeping survives a zone change")
eng.set_zones(layer.zones, layer.id, layer.name)
eng.note_on(60, 100)
time.sleep(0.05)
voices_layered = eng.voice_count()
eng.set_zones(grand.zones, grand.id, grand.name)  # zones change WHILE the key is down
eng.note_off(60)
time.sleep(0.05)
step("note-on under layer used 2 channels", voices_layered >= 2, f"{voices_layered} voices")
step("held note released cleanly", eng.held_notes() == [], f"held={eng.held_notes()}")

print("6. velocity curves")
lin = engine_mod.curve_for("linear")
soft = engine_mod.curve_for("soft")
hard = engine_mod.curve_for("hard")
fixed = engine_mod.curve_for("fixed", 77)
step("linear is identity", all(lin[v] == v for v in range(1, 128)))
step("soft lifts quiet notes", soft[40] > lin[40], f"40 -> {soft[40]}")
step("hard suppresses them", hard[40] < lin[40], f"40 -> {hard[40]}")
step("fixed is constant", len(set(fixed[1:])) == 1 and fixed[1] == 77)
step("nothing maps a played note to silence", all(c[1] >= 1 for c in
     (lin, soft, hard, fixed, engine_mod.curve_for("harder"), engine_mod.curve_for("compress"))))

print("7. metronome -- audio-clock driven, six seconds of clicks")
metro = Metronome(eng, SCRATCH)
step("event cancellation available", metro.status()["can_cancel_events"],
     "fluid_sequencer_remove_events bound")
metro.configure({"bpm": 120, "beats_per_bar": 4, "subdivision": 1})
metro.start()
time.sleep(6.0)
st = metro.status()
fired = st["bar"] * 4 + st["beat"] + 1
expected = 6.0 / (60.0 / 120)  # 12 beats
step("beats fired at the right rate", abs(fired - expected) <= 2,
     f"{fired} beats in 6.0 s, expected ~{expected:.0f}")
slope, intercept, n = metro.clock_fit()
step("clock model converged", n >= 8 and abs(slope - 0.001) < 5e-5,
     f"n={n} slope={slope:.8f} (ideal 0.001 s per tick) -> "
     f"audio clock runs {(slope/0.001 - 1) * 1e6:+.0f} ppm vs perf_counter")

print("8. tempo change mid-run cancels queued clicks")
# configure() re-schedules from scratch, which zeroes the beat counter, so the
# count after the sleep is the count *since the change*.
metro.configure({"bpm": 200})
time.sleep(3.0)
gained = metro._beats_fired  # noqa: SLF001
step("no doubled clicks after retempo", 8 <= gained <= 12,
     f"{gained} beats in 3.0 s at 200 bpm, expected ~10 "
     "(uncancelled 120 bpm clicks would push this to ~16)")
metro.stop()
step("stopped", not metro.status()["running"])

print("9. ramp arithmetic")
metro.configure({"bpm": 80, "ramp_enabled": True, "ramp_bars": 2,
                 "ramp_bpm_step": 10, "ramp_bpm_max": 100})
metro._ramp_steps = 0  # noqa: SLF001
cfg = metro.cfg()
seq_bpms = []
for _ in range(5):
    seq_bpms.append(metro._current_bpm(cfg))  # noqa: SLF001
    metro._ramp_steps += 1  # noqa: SLF001
step("ramp climbs then caps", seq_bpms == [80, 90, 100, 100, 100], str(seq_bpms))
metro._ramp_steps = 5  # noqa: SLF001
metro.ramp_setback()
step("setback drops one step", metro._ramp_steps == 4, f"steps={metro._ramp_steps}")  # noqa: SLF001

print("10. override() lets an exercise borrow the tempo without stealing it")
metro.configure({"bpm": 96, "ramp_enabled": False})
saved = SCRATCH.get("metronome", default={}) or {}
step("the saved tempo is 96", saved.get("bpm") == 96, f"config says {saved.get('bpm')}")
metro.override({"bpm": 60})
step("cfg() reports the override", metro.cfg()["bpm"] == 60, f"cfg says {metro.cfg()['bpm']}")
still = SCRATCH.get("metronome", default={}) or {}
step("but nothing was written to disk", still.get("bpm") == 96,
     f"config still says {still.get('bpm')} -- an exercise must not rewrite your tempo")
metro.release()
step("release falls back to what you saved", metro.cfg()["bpm"] == 96,
     f"cfg says {metro.cfg()['bpm']}")
step("release on a clean metronome is a no-op", metro.release()["bpm"] == 96)

# Regression: a leftover ramp ceiling must never drag the chosen tempo down.
# This silently held the metronome at 100 bpm no matter what tempo was asked for.
metro._ramp_steps = 0  # noqa: SLF001
fast = metro.cfg()
fast["bpm"] = 200          # ramp_bpm_max is still 100 from above
step("ramp ceiling never lowers the base tempo", metro._current_bpm(fast) == 200,  # noqa: SLF001
     f"asked 200 with ramp ceiling 100 -> got {metro._current_bpm(fast):.0f}")  # noqa: SLF001

metro.shutdown()
eng.stop()
print()
print("ALL CHECKS PASSED" if ok else "SOMETHING FAILED")
sys.exit(0 if ok else 1)
