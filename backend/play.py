"""M1 -- plug in, run this, play. Piano in, sound out, 3 ms of latency.

    .venv\\Scripts\\python.exe backend\\play.py

While it runs:  1-8 switch instrument   SPACE panic (all notes off)   q quit

Every setting in here was measured on this machine, not guessed. See docs/FEASIBILITY.md
before changing any of them -- most of the obvious-looking alternatives are silently wrong.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# --- 1. GIL tuning. Must happen before any thread starts. -------------------
# Measured: the default 0.005 switch interval gives a ~14.5 ms median delay before
# a MIDI callback gets to run. 0.001 changes nothing (14.4 ms). The threshold is
# strictly BELOW 1000 microseconds -- 0.0008 drops the median to 0.53 ms, which is
# the idle baseline. This single line is worth more than every other optimization here.
sys.setswitchinterval(0.0008)

# --- 2. DLL discovery. Must happen before `import fluidsynth`. --------------
# pyfluidsynth resolves the library via ctypes.util.find_library(), which on Windows
# walks os.environ['PATH'] and nothing else -- os.add_dll_directory() does NOT work.
# The lookup runs at module import, so this cannot be moved below the import.
FLUIDSYNTH_BIN = r"C:\tools\fluidsynth\bin"
os.environ["PATH"] = FLUIDSYNTH_BIN + os.pathsep + os.environ["PATH"]

import fluidsynth  # noqa: E402
import rtmidi  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SOUNDFONT = ROOT / "soundfonts" / "GeneralUser-GS.sf2"

SAMPLE_RATE = 48000.0  # float! setting() dispatches on type; an int routes to
#                        fluid_settings_setint and fails without raising.
PERIOD_SIZE = 144  # This machine's measured exclusive-mode floor. 128 is refused
#                    ("minimum period is 144"). Raise to 256 if you hear crackling.
CHANNEL = 0

# bank, program, name -- GM numbering, 0-indexed
PRESETS = [
    (0, 0, "Acoustic Grand"),
    (0, 4, "Rhodes"),
    (0, 5, "Chorused EP"),
    (0, 16, "Drawbar Organ"),
    (0, 19, "Church Organ"),
    (0, 24, "Nylon Guitar"),
    (0, 32, "Acoustic Bass"),
    (0, 48, "Strings"),
]


def note_name(n: int) -> str:
    return f"{('C C# D D# E F F# G G# A A# B').split()[n % 12]}{n // 12 - 1}"


def main() -> int:
    if not SOUNDFONT.exists():
        print(f"Missing soundfont: {SOUNDFONT}")
        return 1

    fs = fluidsynth.Synth(samplerate=SAMPLE_RATE)
    fs.setting("audio.driver", "wasapi")
    fs.setting("audio.wasapi.exclusive-mode", 1)  # NOT "audio.wasapi.exclusive" --
    #                                               that name is silently ignored.
    fs.setting("audio.period-size", PERIOD_SIZE)  # Sole latency knob in exclusive
    #                                               mode; audio.periods is ignored.
    fs.setting("synth.sample-rate", SAMPLE_RATE)
    fs.setting("synth.polyphony", 256)
    fs.setting("synth.gain", 0.6)
    # Stop FluidSynth opening the piano behind our back. start() unconditionally
    # builds a MIDI driver (fluidsynth.py:834); left alone it grabs the same port
    # our callback owns and every note sounds TWICE.
    fs.setting("midi.driver", "winmidi")
    fs.setting("midi.winmidi.device", "__none__")

    sfid = fs.sfload(str(SOUNDFONT))
    if sfid == -1:
        print("sfload failed")
        return 1
    fs.start()

    preset = 0

    def select(i: int) -> None:
        nonlocal preset
        preset = i % len(PRESETS)
        bank, prog, name = PRESETS[preset]
        fs.program_select(CHANNEL, sfid, bank, prog)
        print(f"\r  -> {name:<16}", end="", flush=True)

    select(0)

    midi_in = rtmidi.MidiIn()
    ports = midi_in.get_ports()
    if not ports:
        print("\nNo MIDI input. Is the piano on and plugged into USB TO HOST?")
        return 1

    def on_midi(event, _data=None) -> None:
        # HOT PATH. No I/O, no allocation, no locks, nothing that can block.
        # The P-71 sends ~24 clock bytes per second forever; 98% of all traffic on
        # this port is 0xF8/0xFE. Drop them first or we wake up for nothing.
        msg, _delta = event
        status = msg[0]
        if status >= 0xF8:
            return
        kind = status & 0xF0
        if kind == 0x90 and msg[2]:
            fs.noteon(CHANNEL, msg[1], msg[2])
        elif kind == 0x80 or kind == 0x90:
            fs.noteoff(CHANNEL, msg[1])
        elif kind == 0xB0:
            fs.cc(CHANNEL, msg[1], msg[2])  # 64 = sustain
        elif kind == 0xE0:
            fs.pitch_bend(CHANNEL, ((msg[2] << 7) | msg[1]) - 8192)

    midi_in.open_port(0)
    midi_in.ignore_types(timing=True, active_sense=True)  # belt and braces
    midi_in.set_callback(on_midi)

    print("\n(the 'Device \"__none__\" does not exists' line above is intentional --")
    print(" it is what stops FluidSynth grabbing the piano and doubling every note)")
    print(f"\n{ports[0]} -> {PERIOD_SIZE / SAMPLE_RATE * 1000:.2f} ms buffer, exclusive mode")
    print("1-8 instrument   SPACE panic   q quit\n")
    select(0)

    import msvcrt

    try:
        while True:
            if msvcrt.kbhit():
                key = msvcrt.getch()
                if key in (b"q", b"\x1b"):
                    break
                if key == b" ":
                    for ch in range(16):
                        fs.cc(ch, 123, 0)  # all notes off
                        fs.cc(ch, 120, 0)  # all sound off
                    print("\r  -> panic          ", end="", flush=True)
                elif key.isdigit() and key != b"0":
                    select(int(key) - 1)
            time.sleep(0.01)
    except KeyboardInterrupt:
        pass
    finally:
        midi_in.close_port()
        for ch in range(16):
            fs.cc(ch, 120, 0)
        fs.delete()
        print("\nbye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
