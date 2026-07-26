# Keys — Project Canon

> A MIDI piano practice app for one specific machine and one specific piano. Not a product,
> not a Synthesia competitor — a practice instrument built around **Tate's Yamaha P-71B**.
> On conflict, this file wins for the project; the workspace `../CLAUDE.md` is the floor.

## One-liner
Plug in the P-71B, run one command, and the piano makes good sounds with 3 ms of latency —
then, over time, the app measures what you actually practiced and drills what you're worst at.

## Status
**M1 shipped and verified on hardware** (2026-07-25). MIDI in → FluidSynth → WASAPI exclusive,
8 instruments, panic key. `backend/play.py` runs today. Everything past M2 is unbuilt.
See `docs/ROADMAP.md`.

## The hardware (measured, not assumed — see docs/FEASIBILITY.md)
| Thing | Value | Why it matters |
|---|---|---|
| Piano | Yamaha **P-71B** (Amazon-exclusive P-45), USB `VID_0499/PID_160F` | 88 GHS weighted keys, 64-note polyphony |
| Driver | in-box `wdma_usb.inf` / `usbaudio` / "(Generic USB Audio)" | **Not** the Yamaha WDM driver — that one is blocked and must stay uninstalled |
| MIDI port | `Digital Piano 0` (WinMM) | 1 input, 0 outputs from the piano |
| OS | Windows 11 25H2, build **26200.8655** | Windows MIDI Services in-box; WinMM is multi-client now |
| Audio out | Realtek, WASAPI **exclusive**, 48000 Hz, **16-bit only** | float FAILS at every rate on this device |
| Buffer floor | **144 samples = 3.00 ms** | 128 is refused: "minimum period is 144" |
| Python | 3.11.1 + `.venv` | 3.11/3.12 only — python-rtmidi has no cp313+ wheel |

## The crux (read before writing code)

1. **98% of what the P-71B sends is garbage.** It transmits MIDI Clock (`0xF8`) ~24×/second
   forever, plus Active Sensing (`0xFE`) every ~300 ms, whether or not you are playing.
   Measured: 5171 messages in one session, 5096 of them noise. **The MIDI callback's first
   branch must be `if status >= 0xF8: return`.** Everything else is downstream of that.

2. **`sys.setswitchinterval(0.0008)` is the single highest-leverage line in the codebase.**
   Python's default (0.005) gives a ~14.5 ms median delay before the MIDI callback gets to
   run — worse than every buffer setting combined. 0.001 does *nothing* (14.4 ms); the
   threshold is strictly below 1000 µs. At 0.0008 the median is 0.53 ms, the idle baseline.
   Set it before any thread starts.

3. **`fs.start()` steals the piano.** pyfluidsynth's `start()` unconditionally builds a MIDI
   driver (`fluidsynth.py:834`) that opens a winmidi input — the same port our callback owns.
   Left alone, every note sounds **twice**. Neutralize it with
   `midi.winmidi.device = "__none__"`. The resulting console error is intentional.

4. **The obvious setting names are the wrong ones**, and FluidSynth fails silently on all of
   them: it is `audio.wasapi.exclusive-mode` (not `.exclusive`), `synth.sample-rate` (not
   `audio.sample-rate`), and that one must be passed as a **Python float** — an int routes to
   `fluid_settings_setint` and fails without raising.

5. **Nothing in software can measure true end-to-end latency.** WASAPI loopback taps the
   post-mix engine, misses driver/DMA/DAC/acoustics entirely, and returns pure silence when
   the output is exclusive-mode. Any "latency self-test" that claims a round-trip number is
   lying. Measure the parts you can (callback→noteon) and label it as such.

## The honest framing
This app will not teach Tate piano; a method book and daily practice will. The app's real job
is the two things a book can't do: **make the instrument fun enough to sit at daily**, and
**measure what actually happened**. Synthesia ($29) and PianoBooster (free, GPL) already do
falling notes better than this ever will — don't rebuild them. Build what they don't:
this piano, this latency, this practice history.

**The failure mode to watch for:** three weeks in with a gorgeous zone editor and no calluses.

## Stack
- **Synth:** FluidSynth **2.5.7** at `C:\tools\fluidsynth\bin` (no winget package exists —
  GitHub release zip, `-cpp11` variant) + `pyfluidsynth` 1.4.0 (pure ctypes, bundles no DLL).
- **MIDI in:** `python-rtmidi` 1.5.8 (WinMM backend only; no MIDI 2.0, no virtual ports on Windows).
- **SoundFont:** GeneralUser GS 2.0.3 (31 MB, 261 presets, 13 drum kits, permissive license).
- **Later:** FastAPI + WebSocket → browser UI. Not built. Audio never goes through Python.

## Hard rules
- **The MIDI callback is sacred.** No print, no logging, no `await`, no dict allocation, no
  lock, no I/O. Route and call `fs.noteon()`. UI events go on a bounded deque and get drained
  elsewhere; if the UI is slow, drop frames.
- **Never `sleep`/`setInterval` for musical timing.** Use FluidSynth's sequencer.
- **Do not reinstall the Yamaha USB-MIDI driver.** It is blocked by Windows' April-2026 driver
  policy and reinstalling re-breaks MIDI. The in-box class driver is the correct state.
- **`tools/audio_check.py` is the regression test.** Run it when something feels laggy, before
  reading any code. Audio bugs are perceptual and `git diff` beats intuition.
- Velocity curves are pointless until the piano's Touch Sensitivity is off `Fixed`
  (hold `[GRAND PIANO/FUNCTION]` + `B2`, the white key left of middle C, for Medium).

## Map
- `docs/FEASIBILITY.md` — every verified fact + the 40+ places the original plan was wrong.
- `docs/ROADMAP.md` — M0–M10, what's built, what's next, what to skip.
- `backend/play.py` — M1. The thing that works today.
- `tools/midi_probe.py` — zero-dependency MIDI diagnostic. `tools/audio_check.py` — the M1 gate.
