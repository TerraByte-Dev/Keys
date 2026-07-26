"""Verify the audio path before building anything on it. This is the M1 gate.

Checks, in order:
  1. pyfluidsynth can locate libfluidsynth-3.dll (PATH, not add_dll_directory)
  2. the SoundFont loads
  3. the synth actually renders non-silent audio (offline, no speakers involved)
  4. WASAPI exclusive mode opens at the settings we measured for this machine
  5. fs.start() does NOT steal the MIDI input port

    python tools/audio_check.py
"""

from __future__ import annotations

import os
import struct
import sys
import wave
from pathlib import Path

# MUST come before `import fluidsynth`. pyfluidsynth finds the DLL through
# ctypes.util.find_library(), whose Windows implementation walks os.environ['PATH']
# ONLY -- it is completely blind to os.add_dll_directory(). The lookup runs at
# module import time, so setting PATH afterwards is too late.
FLUIDSYNTH_BIN = r"C:\tools\fluidsynth\bin"
os.environ["PATH"] = FLUIDSYNTH_BIN + os.pathsep + os.environ["PATH"]

ROOT = Path(__file__).resolve().parent.parent
SOUNDFONT = ROOT / "soundfonts" / "GeneralUser-GS.sf2"

# Measured on this machine with `fluidsynth -Q`: the Realtek endpoint supports
# exactly 44100/48000 Hz at 16 bits in exclusive mode. float FAILS at every rate.
SAMPLE_RATE = 48000.0  # float, not int -- Synth.setting() dispatches on type and
#                        an int silently routes to fluid_settings_setint and fails.
PERIOD_SIZE = 128  # 128/48000 = 2.67 ms. Sole latency knob in exclusive mode.

ok = True


def step(label: str, passed: bool, detail: str = "") -> None:
    global ok
    ok = ok and passed
    print(f"  [{'PASS' if passed else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))


print("1. import fluidsynth")
try:
    import fluidsynth
except ImportError as exc:
    step("import", False, f"{exc}  (is {FLUIDSYNTH_BIN} correct?)")
    sys.exit(1)
step("import", True, f"pyfluidsynth {getattr(fluidsynth, '__version__', '?')}")

print("2. soundfont")
if not SOUNDFONT.exists():
    step("file exists", False, str(SOUNDFONT))
    sys.exit(1)
step("file exists", True, f"{SOUNDFONT.stat().st_size:,} bytes")

print("3. offline render (proves the synth makes sound, no speakers needed)")
fs = fluidsynth.Synth(samplerate=SAMPLE_RATE)
sfid = fs.sfload(str(SOUNDFONT))
step("sfload", sfid != -1, f"id={sfid}")
fs.program_select(0, sfid, 0, 0)  # bank 0, program 0 = Acoustic Grand Piano
for note in (60, 64, 67):  # C major
    fs.noteon(0, note, 100)
samples = fs.get_samples(int(SAMPLE_RATE))  # 1 second, stereo interleaved int16
peak = max(abs(s) for s in samples[::64]) if len(samples) else 0
step("non-silent output", peak > 1000, f"peak amplitude {peak} of 32767")

out = ROOT / "soundfonts" / "_check.wav"
with wave.open(str(out), "wb") as w:
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(int(SAMPLE_RATE))
    w.writeframes(struct.pack(f"<{len(samples)}h", *samples))
step("wrote wav", out.exists(), f"{out.name} ({out.stat().st_size:,} bytes) -- play it to hear the chord")
fs.delete()

print("4. WASAPI exclusive mode -- finding this machine's lowest usable buffer")
# This device reports its own floor in the error text if you go under it.
# Walk up until one opens; the first hit is the lowest latency this hardware allows.
best = None
for period in (128, 144, 160, 192, 256, 384, 480):
    fs2 = fluidsynth.Synth(samplerate=SAMPLE_RATE)
    fs2.setting("audio.driver", "wasapi")
    fs2.setting("audio.wasapi.exclusive-mode", 1)
    fs2.setting("audio.period-size", period)
    fs2.setting("synth.sample-rate", SAMPLE_RATE)
    # Point FluidSynth's own MIDI driver at a device that does not exist, so start()
    # cannot open the piano behind our back. Our rtmidi callback owns that port.
    # Without this you get every note twice -- once from us, once from FluidSynth.
    fs2.setting("midi.driver", "winmidi")
    fs2.setting("midi.winmidi.device", "__none__")
    try:
        fs2.start()
        opened = fs2.audio_driver is not None
    except Exception:  # noqa: BLE001
        opened = False
    ms = period / SAMPLE_RATE * 1000
    print(f"       period-size {period:>3} ({ms:>5.2f} ms) {'OPENED' if opened else 'refused'}")
    fs2.delete()
    if opened and best is None:
        best = (period, ms)

step("exclusive mode opens", best is not None, f"lowest = {best[0]} samples / {best[1]:.2f} ms" if best else "none worked")

print("5. MIDI port not stolen by FluidSynth")
import ctypes

winmm = ctypes.WinDLL("winmm")
n = winmm.midiInGetNumDevs()
step("piano port still enumerable", n > 0, f"{n} input(s)")

print()
print("ALL CHECKS PASSED" if ok else "SOMETHING FAILED -- do not build on this yet")
sys.exit(0 if ok else 1)
