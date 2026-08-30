# Hardware notes & measured facts

Every number here was measured on real hardware, not estimated. Where a claim is sourced rather than executed,
it says so. If you are about to "fix" a setting in `backend/engine.py`, read this first — most of the
obvious-looking alternatives are silently wrong.

Reference machine: Windows 11 (build 26200.x), a Realtek onboard audio endpoint, and a **Yamaha P-71B**
(the Amazon-exclusive P-45) over USB, on the in-box USB-audio class driver.

## The headline

**3.00 ms**, WASAPI exclusive, 48 kHz / 16-bit, at a 144-sample buffer. No audio interface, no ASIO shim, no
hardware purchase. That is interface-class latency from onboard audio.

## 1a. If Windows sees your keyboard TWICE

Many USB controllers expose **two** MIDI input ports. An Alesis V49 enumerates as:

```
  [0] V49
  [1] MIDIIN2 (V49)
```

Only one of them carries the keys; the other is the vendor's editor/DAW-control port. Which one is which is not
knowable from the name, is not consistent across vendors, and Windows hands out the indices in enumeration
order — so it changes when anything else MIDI is plugged in.

Keys used to open exactly one input: index 0, or a saved *index*. On a single-port piano like the P-71B that was
never wrong, so it stood for months. On a two-port controller it was a coin flip, and losing the flip looked
exactly like a broken application — **both ports listed, no error, no keys lighting up, no sound.** The same
shape as the silent-audio bug in section 3: a real failure with no signal attached to it.

**Keys now listens to every MIDI input at once**, so there is nothing to get wrong. Two things make that safe:

- A device that *mirrors* its keys onto both ports would sound every note twice. An identical status+data byte
  arriving from a **different** port within 8 ms is treated as the mirror and dropped (`DEDUPE_S`,
  `backend/midi_in.py`). The same port repeating is never dropped — that is real playing.
- Windows MIDI inputs are exclusive, so a port another application already holds throws on open. One port
  refusing no longer takes the rest down with it.

**Settings → MIDI input** now shows a message counter per port. Press a key: the port whose counter moves is your
keyboard. That is the whole diagnosis, and it used to require a terminal.

Pinning one input is still possible and is stored **by name**, not index — a saved index silently came to mean a
different device the moment anything else was plugged in. "Listen to everything" is the way back.

`tools/midi_ports_check.py` holds all of this against a simulated V49 and needs no hardware.

## 1. If Windows sees zero MIDI inputs

The most common hard failure is not the app. A vendor MIDI driver can be **blocked at load time** by Windows
Driver Policy, which as of the April 2026 security update stopped trusting drivers signed only under the
deprecated cross-certificate program:

```
USB\VID_0499&PID_160F&MI_00   "Digital Piano"   Present: True
  driver oem89.inf / service YMIDUSBW / v3.1.4.0 (2015-07-20)
  Status: Error  ->  CM_PROB_DRIVER_FAILED_LOAD (Code 39)
winmm midiInGetNumDevs() = 0
```

Device Manager appends *"An Application Control policy has blocked the file."* KORG published an equivalent
bulletin for their own drivers in March 2026, so this is not vendor-specific.

**The fix that works: unplug and replug.** Windows rebinds the device to the in-box class driver with no
uninstall needed:

```
Digital Piano   Status: OK   ProblemCode: 0
  driver wdma_usb.inf / service usbaudio / v10.0.26100.x
  Manufacturer: (Generic USB Audio)
midiInGetNumDevs() = 1  ->  port [0] "Digital Piano"
```

**The in-box class driver is the correct end state — do not reinstall the vendor driver.** On a Yamaha system
the MSI (`Yamaha USB-MIDI Driver`, product code `{2D488455-3E89-49EF-BA6E-92C2503DC89D}`) can re-push the broken
INF on a future replug or update; removing the app is recommended. Yamaha's V3.1.5 fixed a *2024* install-time
block, not this 2026 load-time one.

**Diagnosing:** `PnP Status: Unknown` with `Problem: CM_PROB_PHANTOM` on every node means the device is simply
**not present** — powered off, asleep, or on a charge-only cable — not blocked. Many digital pianos have an
auto power-off that will do this to you mid-session; on a P-45/P-71 hold the lowest key while switching on to
disable it.

`tools/midi_probe.py` needs no virtualenv and no packages — it is the first thing to run when nothing works.

## 2. What the piano actually sends

Measured over one ~2.5-minute session:

| Message | Count | Note |
|---|---:|---|
| `0xF8` MIDI Clock | 4614 | ~24/sec, **continuously, even when idle** |
| `0xFE` Active Sensing | 482 | ~every 300 ms |
| note on / note off | 35 / 35 | perfectly paired, no stuck notes |
| CC / program / pitchbend / aftertouch | 0 | the pedal was not pressed during the capture |

**98.5% of all traffic is real-time noise**, which is where the hot-path rule comes from. The upside: the piano
transmits its own tempo clock, so a metronome could in principle sync *to* the instrument.

### Fixed velocity

In that same capture **every velocity was exactly 64** — 35/35, zero variation. That is the P-45/P-71's `Fixed`
Touch Sensitivity setting, and it is an instrument setting, not a software one. The keys remain
hammer-weighted; the piano just does not report how hard you hit.

Hold `[GRAND PIANO/FUNCTION]` and press one of four keys:

| Key (Yamaha naming) | Scientific / MIDI | Result |
|---|---|---|
| A2 | A3 · 57 | Fixed |
| A#2 | A#3 · 58 | Soft |
| **B2** | **B3 · 59** | **Medium** (factory default) |
| C3 | C4 · 60 (middle C) | Hard |

Yamaha's manual numbers octaves with middle C as C3; Keys uses scientific pitch notation where middle C is C4.
Same physical keys — the three immediately left of middle C, plus middle C itself. **Velocity curves are
meaningless until this is off `Fixed`**, because they would be operating on a constant.

## 3. Audio

`fluidsynth -Q` on the reference endpoint, **exclusive** mode:

| Rate | 16-bit | float |
|---|---|---|
| 44100 Hz | **OK** | FAILED |
| 48000 Hz | **OK** | FAILED |
| everything else | FAILED | FAILED |

float fails at every rate. FluidSynth 2.5.7 has no 24/32-bit WASAPI output; that was merged under milestone 2.6
and is not in any released build.

Buffer ladder, measured by `tools/audio_check.py`:

```
128 ( 2.67 ms) refused   <- "minimum period is 144"
144 ( 3.00 ms) OPENED    <- this machine's floor
160 ( 3.33 ms) OPENED
192 ( 4.00 ms) OPENED
256 ( 5.33 ms) OPENED    <- fall back here if it crackles
480 (10.00 ms) OPENED    <- Windows default
```

`audio.periods` is **irrelevant** in exclusive mode — `audio.period-size` is the sole latency factor. In
*shared* mode it is the reverse: period-size is ignored entirely and Windows picks the engine period.

`allowExclusive=1` was already set on the endpoint, and the `default` device negotiates 44.1/48k 16-bit, so the
device name need not be hardcoded. `audio.wasapi.device` accepts any endpoint name FluidSynth enumerates —
`backend/engine.list_audio_devices()` reads that option list rather than guessing, because a wrong name yields
silence, not an error.

**You do not need an audio interface.** 3 ms is already interface-class.

### Endpoint names over 31 characters

`Synth.start()` in pyfluidsynth reads `audio.wasapi.device` back through `get_setting()`, which copies strings
into a 32-byte buffer (`fluidsynth.py:799-801`) and writes the truncated stump back over the good name
immediately before opening the driver (`:825`). **Any endpoint whose name is 32+ characters could never be
opened**, and the failure was silent — `new_fluid_audio_driver` returns NULL, `start()` returns `FLUID_OK`
anyway (`:839`), and nothing raised.

Five of the seven endpoints on this machine are over the limit:

```
  7        default
 27        Speakers (Realtek(R) Audio)        <- the reference endpoint; why this never showed up
 23        Speakers (Yeti Classic)
 35  OVER  Speakers (Steam Streaming Speakers)
 36  OVER  CABLE Input (VB-Audio Virtual Cable)
 38  OVER  CABLE In 16ch (VB-Audio Virtual Cable)
 38  OVER  HISENSE (NVIDIA High Definition Audio)
```

Fixed by passing the device to `start()` directly rather than letting it fetch its own
(`backend/engine.py`, `fs.start(driver="wasapi", device=device)`), which skips the read-back entirely.

### Bluetooth

Bluetooth output **works** but is not playable. A2DP/SBC carries 100–250 ms of codec and transport latency,
aptX-LL ~40 ms, LE Audio/LC3 ~20–50 ms. None of that is under this app's control and no setting changes it —
3 ms over Bluetooth is physically impossible. Use it for score playback and backing tracks, never for playing.

Two more Bluetooth-specific traps, both of which used to present as silence:

- Windows exposes a headset as **two** endpoints. `Headphones (… Stereo)` is A2DP and is the one you want.
  `Headset (… Hands-Free AG Audio)` is 8/16 kHz mono telephony, it seizes the mic, and it will not give you
  48 kHz. `list_audio_devices()` reports both, verbatim, with no hint which is which.
- BT endpoints stop enumerating entirely when the headphones sleep, power off, or roam to a phone. A pinned
  device name then names nothing. The engine now falls back to the system default and says so, rather than
  opening no stream at all.

Bluetooth names are also almost always over the 31-character limit above — `Headset (WH-1000XM4 Hands-Free AG
Audio)` is 40 — which is why "it works for everyone else" held for so long: every wired tester landed on
`default` or a short Realtek name.

**Microsoft's in-box low-latency USB ASIO driver does not exist in any usable form.** The
`microsoft/low-latency-audio` README still reads "There is no public release of the driver, yet." FlexASIO is
dead (last commit 2024-06-15). Neither is needed.

## 4. Corrections to common assumptions

Grouped by severity. **Blockers cost an evening each.**

### Blockers

| Commonly assumed | Reality |
|---|---|
| `winget install FluidSynth.FluidSynth` | **No FluidSynth package exists in winget at all.** Download the `-cpp11` zip from GitHub releases, extract, put `bin/` on `PATH`. |
| `pip install fluidsynth` | That's an **abandoned 2012 package** (v0.2). You want `pip install pyfluidsynth`. |
| Use `os.add_dll_directory()` to find the DLL | **Backwards.** pyfluidsynth uses `ctypes.util.find_library()`, whose Windows implementation walks `os.environ['PATH']` *only* and is blind to `add_dll_directory`. Must be set **before** `import fluidsynth`. |
| `sys.setswitchinterval(0.001)` tames GIL jitter | Measured: 0.001 → 14.4 ms median, statistically identical to the 0.005 default. The threshold is strictly **below** 1000 µs. **0.0008 → 0.53 ms.** |
| A self-test can measure end-to-end latency | It cannot. Loopback capture misses driver/DMA/DAC/acoustics and returns **silence** under exclusive mode. |
| Salamander Grand for the piano sound | **SFZ + WAV only. FluidSynth cannot load SFZ.** Needs a second engine. |

### Wrong

| Commonly assumed | Reality |
|---|---|
| `audio.wasapi.exclusive=1` | It's `audio.wasapi.exclusive-mode`. The wrong name is **silently ignored**. |
| `audio.sample-rate` | No such setting. It's `synth.sample-rate`, and via pyfluidsynth it must be a **Python float**. |
| Tune `audio.periods` and period-size together | `audio.periods` does nothing in exclusive mode. |
| `-o audio.sample-format=float` for precision | Not supported by any released FluidSynth, and float fails on this device anyway. |
| Drum mode = send bank 128 over MIDI | "Bank 128" is an **SF2-file convention, not a wire value**. FluidSynth's default `synth.midi-bank-select=gs` takes CC0 literally; CC0=120 does nothing. Call `program_select(chan, sfid, 128, prog)` explicitly. |
| Multi-client MIDI needs the new API | Backwards — Microsoft replumbed WinMM itself, so python-rtmidi gets multi-client with **zero code change**. |
| Windows MIDI Services must be installed | The in-box service + WinMM shim are already there. |

### Stale / nitpick

- FluidSynth asset names embed a literal `v` and an OSAL suffix — a hardcoded old version URL 404s. Use
  `-cpp11`: identical features to `-glib`, four fewer DLLs. Only `sndfile.dll` is a hard import.
- `midisrv` being **Stopped is correct** — it is demand-start by design, not a fault.
- python-rtmidi 1.5.8 is the only PyPI release. **No cp313+ wheel → pin Python to 3.11/3.12.**
- python-rtmidi **virtual ports raise `NotImplementedError` on Windows** (reproduced). For synthetic testing use
  loopMIDI — but note it dates from January 2020 and is effectively abandonware.
- GM percussion note numbers (36 kick, 38 snare, 42 closed hat, 46 open hat, 49 crash) are all correct.
- `pyfluidsynth` does not wrap `fluid_sequencer_remove_events`, but the DLL exports it. Bind it with
  `fluidsynth.cfunc` — without it a tempo change cannot cancel queued clicks and every bpm change doubles up.

## 5. SoundFonts

| Pack | Size | License | Verdict |
|---|---:|---|---|
| **GeneralUser GS 2.0.3** | 32.3 MB | Explicitly permissive: "use without restriction… private or commercial" | **The default.** 287 presets, 13 drum kits |
| MS Basic.sf3 (MuseScore 4) | 51.3 MB | MIT | Best-maintained FluidR3 successor |
| MuseScore_General.sf3 | 39.9 MB | — | Steinway Model D piano, a step up on grands |
| FluidR3_GM | 148.3 MB | MIT | Only via archive.org / Debian mirrors now |
| Nice-Steinway-v3.8 | 214.9 MB | **murky** | Great piano, but the only mirror is tagged by the mirror maintainer, not the author |
| Salamander Grand | 488 MB–1.45 GB | **Public domain** | Cleanest license of all — but **SFZ, won't load in FluidSynth** |
| Arachno | — | "not allowed to copy, reproduce… for public use" | **Never bundle or auto-download** |

Downloader traps: `musical-artifacts.com` returns **403** to non-browser clients, and some project sites are
SPAs that return **HTTP 200 + index.html for every path**, so a naive fetch "succeeds" and writes HTML into a
`.sf2`. Always verify Content-Length + SHA256. GeneralUser GS's license also asks that you not hotlink its
download.
