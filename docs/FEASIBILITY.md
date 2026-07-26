# Keys — Feasibility & Verified Facts

> Everything here was checked on **this machine** on **2026-07-25**, either by running it or by
> a 14-agent research fleet whose findings were then adversarially refuted. Where a claim is
> only sourced and not executed, it says so. The original `piano-practice-app-plan.md` was a
> good plan with roughly 40 factual errors in it; they are catalogued below so they don't get
> re-introduced.

## Verdict

**Feasible, and better than the plan predicted.** The one thing the plan called "the gate" —
latency — is already won: 3.00 ms WASAPI exclusive, no hardware purchase, no ASIO shim. The
real risks are not technical:

1. **The piano was sending fixed velocity** (every note vel=64). Fixed by a setting on the
   instrument, not in code. Until that's off `Fixed`, dynamics practice is impossible.
2. **Scope.** Synthesia and PianoBooster already ship the falling-notes feature. Building it
   again produces nothing.
3. **The app is not the teacher.** See "Learning" below — some of the plan's pedagogy is folklore.

## 1. The blocker that started everything

The piano was plugged in and Windows saw **zero MIDI inputs**.

```
USB\VID_0499&PID_160F&MI_00   "Digital Piano"   Present: True
  driver oem89.inf / service YMIDUSBW / v3.1.4.0 (2015-07-20)
  Status: Error  ->  CM_PROB_DRIVER_FAILED_LOAD (Code 39)
winmm midiInGetNumDevs() = 0
```

**Root cause:** Microsoft's Windows Driver Policy stopped trusting drivers signed only under
the deprecated cross-certificate program as of the **April 2026 security update**. The Yamaha
package shows `Attributes: Legacy` / `WHCP Version: Unknown` — exactly the profile that's
blocked. Device Manager appends "An Application Control policy has blocked the file."
KORG published an equivalent bulletin for their own drivers (2026-03-12).

**Fix that worked:** unplug + replug. Windows rebound the device to the in-box class driver
with no uninstall needed. Verified after:

```
Digital Piano   Status: OK   ProblemCode: 0
  driver wdma_usb.inf / service usbaudio / v10.0.26100.8521
  Manufacturer: (Generic USB Audio)
midiInGetNumDevs() = 1  ->  port [0] "Digital Piano"
```

**Outstanding risk:** the MSI `Yamaha USB-MIDI Driver 3.1.4.1`
(`{2D488455-3E89-49EF-BA6E-92C2503DC89D}`) is **still in Add/Remove Programs** and can re-push
the broken INF on a future replug or update. Recommended: uninstall the app. Do **not** install
Yamaha's V3.1.5 — it fixed a *2024* install-time block, not this 2026 load-time one.

## 2. What the piano actually sends

Measured over one ~2.5-minute session with `tools/midi_probe.py`:

| Message | Count | Note |
|---|---:|---|
| `0xF8` MIDI Clock | 4614 | ~24/sec, **continuously, even when idle** |
| `0xFE` Active Sensing | 482 | ~every 300 ms |
| note on / note off | 35 / 35 | perfectly paired, no stuck notes |
| CC / program / pitchbend / aftertouch | 0 | pedal was not pressed during the capture |

**98.5% of all traffic is real-time noise.** Hence the hot-path rule. Upside: the piano
transmits its own tempo clock, so M5's metronome could sync *to* the instrument.

**Every velocity was exactly 64** — 35/35, zero variation. That is the P-45/P-71's `Fixed`
touch setting. Fix on the instrument: hold `[GRAND PIANO/FUNCTION]` and press one of
`A2 / A#2 / B2 / C3` = `Fixed / Soft / Medium / Hard`. The P-45's lowest key is **A-1**, which
fixes Yamaha's octave convention at middle C = **C3** — so those four keys are `A`, `A#`, `B`
immediately left of middle C, plus middle C itself. **`B2` = Medium = factory default.**

## 3. Audio — the numbers that matter

`fluidsynth -Q` on this machine. Realtek endpoint, **exclusive** mode:

| Rate | 16-bit | float |
|---|---|---|
| 44100 Hz | **OK** | FAILED |
| 48000 Hz | **OK** | FAILED |
| everything else | FAILED | FAILED |

float fails at every rate — the plan's "16-bit only in exclusive mode" gotcha, confirmed on
this exact hardware. FluidSynth 2.5.7 has no 24/32-bit WASAPI output; that was merged
2026-02-14 under milestone **2.6** and is not in any released build.

Buffer ladder, measured by `tools/audio_check.py`:

```
128 ( 2.67 ms) refused   <- "minimum period is 144"
144 ( 3.00 ms) OPENED    <- this machine's floor
160 ( 3.33 ms) OPENED
192 ( 4.00 ms) OPENED
256 ( 5.33 ms) OPENED    <- fall back here if it crackles
480 (10.00 ms) OPENED    <- Windows default
```

`audio.periods` is **irrelevant** in exclusive mode — `audio.period-size` is the sole latency
factor. (In *shared* mode it's the reverse: period-size is ignored entirely.)

Also confirmed: `allowExclusive=1` is already set on the Realtek endpoint, and the `default`
device negotiates 44.1/48k 16-bit too, so the device name need not be hardcoded.

**Do not buy an audio interface.** The plan's "a used Scarlett Solo ends this conversation" is
sound advice in general and unnecessary here — 3 ms is already interface-class.

**Microsoft's in-box low-latency USB ASIO driver does not exist in any usable form.** The
`microsoft/low-latency-audio` README still reads "There is no public release of the driver,
yet." Latest public status is a Nov 2025 devblog. Build 26200.8655 has no way to enable it.
FlexASIO is dead (last commit 2024-06-15). Neither is needed.

## 4. Corrections to the original plan

Grouped by severity. **Blockers would have cost an evening each.**

### Blockers
| Plan says | Reality |
|---|---|
| `winget install FluidSynth.FluidSynth` | **No FluidSynth package exists in winget at all.** Download `fluidsynth-v2.5.7-win10-x64-cpp11.zip` (2,657,270 B, verified byte-exact) from GitHub releases, extract, put `bin/` on PATH. |
| `pip install fluidsynth` | That's an **abandoned 2012 package** (v0.2). You want `pip install pyfluidsynth` (1.4.0). |
| Use `os.add_dll_directory()` to find the DLL | **Backwards.** pyfluidsynth uses `ctypes.util.find_library()`, whose Windows implementation walks `os.environ['PATH']` *only* and is blind to `add_dll_directory`. Must be set **before** `import fluidsynth`. |
| `sys.setswitchinterval(0.001)` tames GIL jitter | Measured on this machine: 0.001 → 14.4 ms median, statistically identical to the 0.005 default. Threshold is strictly **below** 1000 µs. **0.0008 → 0.53 ms.** |
| A self-test can measure end-to-end latency | It cannot. Loopback capture misses driver/DMA/DAC/acoustics and returns **silence** under exclusive mode. |
| Salamander Grand for the piano sound | **SFZ + WAV only. FluidSynth cannot load SFZ.** Needs a second engine (sfizz/sforzando). |
| FluidR3_GM from the canonical source | No live canonical source — `member.keymusician.com` refuses TCP 443; Debian records upstream as "not existing any more". |

### Wrong
| Plan says | Reality |
|---|---|
| `audio.wasapi.exclusive=1` | It's `audio.wasapi.exclusive-mode`. The wrong name is **silently ignored**. |
| `audio.sample-rate` | No such setting. It's `synth.sample-rate`, and via pyfluidsynth it must be a **Python float**. |
| Tune `audio.periods` and period-size together | `audio.periods` does nothing in exclusive mode. |
| `-o audio.sample-format=float` for precision | Not supported by any released FluidSynth, and float fails on this device anyway. |
| Drum mode = send bank 128 over MIDI | "Bank 128" is an **SF2-file convention, not a wire value**. FluidSynth's default `synth.midi-bank-select=gs` takes CC0 literally; CC0=120 does nothing. Call `program_select(chan, sfid, 128, prog)` explicitly. |
| Multi-client MIDI needs the new API | Backwards — Microsoft replumbed WinMM itself, so python-rtmidi gets multi-client with **zero code change**. No loopMIDI needed. |
| Reinstall / update the Yamaha driver | Re-triggers the same block. Remove it. |
| Windows MIDI Services must be installed | The in-box service + WinMM shim are already there. The out-of-band SDK is for diagnostics only, and is still Release Candidate. |

### Stale / nitpick
- FluidSynth is **2.5.7** (released 2026-07-25), not 2.4.x. Asset names embed a literal `v` and
  an OSAL suffix — a hardcoded 2.4.x URL 404s. Use `-cpp11`: identical features to `-glib`,
  4 fewer DLLs. Only `sndfile.dll` is a hard import; `SDL3.dll` is for the exe only.
- `midisrv` being **Stopped is correct** — it's demand-start by design, not a fault.
- python-rtmidi 1.5.8 is the only PyPI release (Nov 2023); master has commits to Jan 2026 but
  no release. **cp311 wheel works.** No cp313+ wheel → pin Python to 3.11/3.12.
- python-rtmidi **virtual ports raise `NotImplementedError` on Windows** (reproduced live).
  For synthetic testing use loopMIDI (`winget install TobiasErichsen.loopMIDI`) — but note it
  dates from **January 2020** and is effectively abandonware.
- GM percussion note numbers in the plan (36/38/42/46/49) are **all correct**. Program numbers
  (0/8/16/24/25/32/40) also correct; add 48 Orchestra, 56 SFX.
- `mido` works but `symusic` 0.6.0 is far faster for file parsing (cp311 wheel available).
  `pretty_midi`/`miditoolkit` are built *on* mido and inherit its speed — not upgrades.

## 5. SoundFonts — what's legal and what's live

| Pack | Size | License | Verdict |
|---|---:|---|---|
| **GeneralUser GS 2.0.3** | 32,319,396 B | Explicitly permissive: "use without restriction… private or commercial", "feel free to use it in your software projects" | **Installed. The default.** 261 presets, 13 drum kits |
| MS Basic.sf3 (MuseScore 4) | 51,278,610 B | MIT | Best-maintained FluidR3 successor |
| MuseScore_General.sf3 | 39,900,972 B | — | Steinway Model D piano, materially better than FluidR3's |
| FluidR3_GM | 148,345,256 B | MIT | Only via archive.org / Debian mirrors now |
| Nice-Steinway-v3.8 | 214,870,414 B | **murky** | Best-sounding SF2 piano, but soundfonts4u is defunct and the only mirror is HF-tagged CC-BY-NC-SA by the *mirror maintainer*, not the author |
| Salamander Grand | 488 MB–1.45 GB | **Public domain** (author reconfirmed 2022) | Cleanest license of all — but **SFZ, won't load in FluidSynth** |
| Arachno | — | "not allowed to copy, reproduce… for public use" | **Never bundle or auto-download** |

Downloader traps: `musical-artifacts.com` returns **403** to non-browser clients;
`schristiancollins.com` is an SPA that returns **HTTP 200 + index.html for every path**, so a
naive fetch "succeeds" and writes HTML into a `.sf2`. Always verify Content-Length + SHA256.
GeneralUser GS's license also asks you not to hotlink its download from a website.

## 6. Learning — where the plan repeats folklore

The research fleet was asked to be blunt about pedagogy. Findings worth keeping:

- **"Hands separate, then together" is the weakest-supported claim in the plan.** Duke, Simmons
  & Cash (2009, *JRME* 56(4):310–321) found hands-*together*-early was one of eight behaviours
  that **distinguished the top-ranked pianists**. Yokoi, Bai & Diedrichsen (2017, *J Neurophysiol*)
  found no transfer between unimanual and bimanual training.
- **Slow practice is supported but narrower than assumed.** Furuya, Nakamura & Nagata (2013,
  *BMC Neuroscience* 14:133, n=12): 4 days at 500 ms inter-keystroke roughly doubled max speed
  and held 2 months — but only in the trained hand, only for similar sequences.
- **Gamification has no evidence base for musical skill.** The retention numbers everyone
  cites are Duolingo's, and measure engagement in a social product, not skill acquisition.
- **Metronome: keep it, instrument it differently.** Bock & Duke, "Not My Tempo" (ISME 2026,
  n=36): accuracy is better *with* an audible click, but without one players hold consistent
  inter-tap spacing while drifting in overall tempo. Measure drift, not just error.
- **"30 minutes a day" is folklore**; the daily-frequency principle is sound, the specific
  numbers trace to music-school marketing blogs, several misciting Macnamara et al. 2014.
- **The competition is real.** Synthesia $29 one-time; PianoBooster free/GPL; flowkey 1,500+
  songs; Piano Marvel ships SASR, a university-used MIDI-scored sight-reading assessment.
  Building falling-notes from scratch buys nothing.

## 7. Research provenance
14 agents (7 research + 7 adversarial refuters), 678k tokens, 283 tool calls, 0 failures.
Refuters killed ~40 claims — almost all mis-sourcing and version drift rather than substance;
those corrections are folded in above. Notable refuter catches: a 2002 Sound-on-Sound latency
figure being used to set a 2026 budget, a Wikipedia cite where a Yamaha primary source existed,
and an invented `get_compiled_api()` return shape that would have crashed diagnostic code.
One agent was not given the milestone list and answered M0–M10 questions partly from inference —
its pedagogy findings stand, its milestone mapping does not.
