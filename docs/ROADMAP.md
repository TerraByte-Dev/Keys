# Roadmap

What's built, what's next, and what is deliberately not being built.

**The product is a workspace, not a tutor.** Every item below is judged against that: does it give someone
more to do at their own pace, or does it try to teach them? The first kind gets built.

## Status

| | Milestone | State |
|---|---|---|
| **M0** | MIDI in | ✅ `tools/midi_probe.py` — zero-dependency diagnostic |
| **M1** | Sound | ✅ **3.00 ms, WASAPI exclusive, verified on hardware** |
| **M2** | Instrument switching | ✅ 18 presets as JSON, 287 enumerated instruments, hotplug |
| **M3** | Zones: split / layer / drum pads | ✅ overlap is the layer; one-click builders + full editor |
| **M4** | See what you're playing | ✅ 88-key display, enharmonic names, chords with inversions |
| **M5a** | Practice timer | ✅ idle-gapped clock, streaks, heatmaps |
| **M5b** | Metronome + tempo ramp | ✅ audio-clock driven, ramp, drift measured |
| **M6** | Sight reading trainer | ✅ SVG grand staff, adaptive weighting |
| **M7** | Play-along / falling notes | ⬜ **deliberately skipped** — see below |
| **M8** | Recording | 🟡 the loop station records and overdubs; no `.mid` / `.wav` export |
| **M9** | Deeper stats | 🟡 timing histogram, activity calendar and key/chord heatmaps done; miss heatmap not started |
| **M10** | Packaging | 🟡 `dist/Keys` builds and runs; no installer, no in-place update |
| **M11** | Practice shelf | ✅ scales, arpeggios, sight reading; one file + one registry line adds a type |
| **M12** | Backing tracks | ✅ YouTube shelf with A/B loop points and a speed control |

Everything above M1 is covered by `tools/pipeline_check.py`, which drives synthetic notes through the real
engine, hub, drain loop and websocket — so the suite runs without a piano attached. Ten suites, 717 assertions.

## Next

**The installer, and updating in place.** `tools/build_exe.py` produces a runnable `dist/Keys` and
**Settings → About** checks GitHub for a newer release. What is missing is applying one, which means replacing
the application directory while it runs — an installer's job. The path, and the two things to settle first
(code signing, and Velopack's Python side), are in [`PACKAGING.md`](PACKAGING.md).

**Two-hand exercises with real independence.** Scales and arpeggios take `hands: R | L | Both` and
`motion: parallel | contrary` today, and hands-together steps carry both notes so onset spread falls straight out
of the data. The gap is *material*: five-finger patterns, Hanon-style cells, contrary-motion drills, and
independent rhythms between the hands. This is the stated biggest pitfall and the one thing the app can measure
that nothing else does.

**M8 — export.** The loop station already holds your notes with millisecond positions; `.mid` is a serialiser and
`.wav` is FluidSynth's file renderer. Also a rolling buffer — "save the last two minutes" — since the drain sees
every note and currently throws them away once the practice log has counted them.

**M9 — miss heatmap.** The exercise and sight-reading logs record per-note accuracy and reaction time. Rendering
that onto the 88-key display would close the loop between "what I'm bad at" and "what I'm shown next".

**M12b — browsable instruments.** 287 presets ship; the gap is navigation, not quantity. Categorised chips
(keys / organs / strings / brass / synths / mallets / world) and favourites. Dropping extra SoundFonts into the
data directory and picking them per zone already works.

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
