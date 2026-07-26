# Keys

A MIDI piano practice app for a Yamaha P-71B on Windows 11. Plug in, run one command, play.

**3.00 ms** MIDI-to-sound latency (WASAPI exclusive, 48 kHz/16-bit) — measured, not estimated.

## Play right now

```powershell
.venv\Scripts\python.exe backend\play.py
```

`1`–`8` switch instrument · `SPACE` panic (all notes off) · `q` quit

## If something is wrong

```powershell
python tools\midi_probe.py        # zero dependencies. Is the piano reaching Windows at all?
.venv\Scripts\python.exe tools\audio_check.py    # is the audio path still good?
```

`midi_probe.py` needs no venv and no packages — stock Python + ctypes. It's the first thing to
run when nothing works, and it prints a velocity histogram so you can see whether the piano's
touch response is on.

## Setup (already done on this machine)

| | |
|---|---|
| FluidSynth 2.5.7 | `C:\tools\fluidsynth\bin` — [GitHub release](https://github.com/FluidSynth/fluidsynth/releases), `-cpp11` zip. **Not in winget.** |
| Python deps | `python -m venv .venv` then `pip install -r requirements.txt` |
| SoundFont | [GeneralUser GS 2.0.3](https://github.com/mrbumpy409/GeneralUser-GS) → `soundfonts/GeneralUser-GS.sf2` |

Python **3.11 or 3.12 only** — `python-rtmidi` has no wheel for 3.13+.

## Layout

```
CLAUDE.md            project canon — read this first
docs/FEASIBILITY.md  every verified fact + where the original plan was wrong
docs/ROADMAP.md      M0-M10, what's built, what to skip
backend/play.py      M1 — the thing that works
tools/               midi_probe.py (diagnostic) · audio_check.py (regression test)
soundfonts/ midi/    gitignored, big files
```

## Three things that will bite you

1. **Set `PATH` before `import fluidsynth`.** `os.add_dll_directory()` does not work — pyfluidsynth
   uses `ctypes.util.find_library()`, which reads `PATH` and nothing else.
2. **`sys.setswitchinterval(0.0008)`.** Not 0.001 — that changes nothing. Below 1000 µs the
   MIDI callback's median delay drops from 14.5 ms to 0.53 ms.
3. **Neutralize FluidSynth's own MIDI driver** or every note plays twice.

All three are explained in `CLAUDE.md`.
