# Roadmap

What's built, what's next, and what is deliberately not being built.

## Status

| | Milestone | State |
|---|---|---|
| **M0** | MIDI in | ✅ `tools/midi_probe.py` — zero-dependency diagnostic |
| **M1** | Sound | ✅ **3.00 ms, WASAPI exclusive, verified on hardware** |
| **M2** | Instrument switching | ✅ 18 presets as JSON, 287 enumerated instruments, hotplug |
| **M3** | Zones: split / layer / drum pads | ✅ overlap is the layer; live visual editor |
| **M4** | See what you're playing | ✅ 88-key display, enharmonic names, chords with inversions |
| **M5a** | Practice timer | ✅ idle-gapped clock, streaks, heatmaps |
| **M5b** | Metronome + tempo ramp | ✅ audio-clock driven, ramp, drift measured |
| **M6** | Sight reading trainer | ✅ SVG grand staff, adaptive weighting |
| **M7** | Play-along / falling notes | ⬜ **deliberately skipped** — see below |
| **M8** | Recording | ⬜ rolling MIDI buffer, `.mid` + `.wav` export |
| **M9** | Deeper stats | 🟡 timing histogram and key heatmap done; miss heatmap not started |
| **M10** | Packaging | ⬜ single-file installer |

Everything above M1 is covered by `tools/pipeline_check.py`, which drives synthetic notes through the real
engine, hub, drain loop and websocket — so the suite runs without a piano attached.

## Next

**M8 — Recording.** A rolling MIDI buffer ("save the last two minutes") plus `.mid` and `.wav` export through
FluidSynth's file renderer. Cheap to build, and the obvious missing piece: the app already knows every note you
played, it just throws them away.

**M9 — Miss heatmap.** The sight-reading log already records per-note accuracy and reaction time. Rendering it
onto the 88-key display would close the loop between "what I'm bad at" and "what I'm shown next".

## Not being built, on purpose

**Falling notes (M7).** Synthesia (one-time purchase) and PianoBooster (free, GPL) both ship this today with
MIDI import and hands-separate practice. Rebuilding it is weeks of work for a worse version. The claim that
falling-notes harms sight-reading has **no controlled evidence** either way — that isn't the reason to skip it.
The reason is that it is already solved. If something in this space ever gets built here, it will be the part
they don't have: scoring against *your own* history.

**Content libraries.** Competing with flowkey or Piano Marvel on a 1,500-song catalogue is not a software
problem, it's a licensing one.

**Gamification.** No evidence base for musical skill acquisition, and a real risk it substitutes for practice.

**VST3 / SFZ.** One engine. If a sampled piano like Salamander is ever wanted, that is a second audio engine and
a separate decision — FluidSynth cannot load SFZ.

## The part that actually makes you a pianist

Get a method book — *Faber, Adult Piano Adventures Book 1* or *Alfred's Adult All-in-One* — and work through it
in order. Practise slower than feels necessary, and daily rather than in weekend blocks.

This app measures and drills. It does not sequence a curriculum, and building one would be a poor use of time
when good ones already exist for $20.
