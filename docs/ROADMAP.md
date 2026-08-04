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
| **M7** | Play-along / falling notes | ✅ ghost mode — silent falling roll, wait-for-me, hands separate, songs drawer |
| **M8** | Recording | 🟡 the loop station records and overdubs; no `.mid` / `.wav` export |
| **M9** | Deeper stats | 🟡 timing histogram, activity calendar and key/chord heatmaps done; miss heatmap not started |
| **M10** | Packaging | 🟡 `dist/Keys` builds and runs; no installer, no in-place update |
| **M11** | Practice shelf | ✅ scales, arpeggios, sight reading; one file + one registry line adds a type |
| **M12** | Backing tracks | ✅ YouTube shelf with A/B loop points and a speed control |

Everything above M1 is covered by `tools/pipeline_check.py`, which drives synthetic notes through the real
engine, hub, drain loop and websocket — so the suite runs without a piano attached. Eighteen scripts: fourteen
need nothing plugged in at all and carry 869 assertions between them. `engine_check`, `looper_check` and
`pipeline_check` want the audio device to themselves and refuse to run while Keys is open; `audio_check` also
wants a piano actually connected, and fails its last step without one.

## M7 got built, and the estimate that said not to was wrong

This file used to say falling notes were "weeks of work for a worse version" of Synthesia and PianoBooster, and
that if anything in the space was ever built here it would be the part they don't have — scoring against your
own history. Both halves are worth correcting rather than quietly deleting.

**The estimate was wrong because the expensive parts were already paid for.** MIDI import, MusicXML conversion,
staff assignment, the score library and `GET /api/scores/{id}/notes` all shipped with M6 and M11 — that
endpoint's docstring already said it was "what playback and, later, following are driven from". What was
actually missing was a projection change in `frontend/roll.js` and a control bar — no new endpoint, no schema
change, and no work anywhere in the note pipeline. The backend contributed two settings keys in `config.py` and
one unrelated bit of insurance in `scores.py` (keeping the original `.mid` beside the conversion, because
converting is a one-way door). An estimate that prices a feature without checking what it can reuse will be
wrong by that much.

**Ghost mode does NOT satisfy the escape clause, and must not be read as having done so.** Nothing is written
to the database, nothing is measured across sessions, and there is no scoring. What shipped is a transport and
a target: the piece falls, you play it, and the roll shows you the gap. That gap is *visible* — your bar and its
ghost fall at the same rate, so the distance between them is your timing error and does not drift — but it is
not *recorded*. The honest one-line summary is that Keys now has a better-integrated Synthesia, not something
Synthesia cannot do.

What the escape clause would still cost, so nobody re-derives it: a seventh SQLite table, plus registration in
`userdata.TABLES`, `userdata.inventory`, `Store.discard_session`'s hand-written cascade and
`tools/merge_history.py`'s `SESSION_CHILDREN` — and `tools/store_check.py` hard-asserts the current table and
index counts, so it goes red the moment `SCHEMA` changes. A separate, arguable day of work. Still unbuilt.

**Wait-for-me is a deliberate step toward tutor, and it is on by default.** It is Synthesia's core feature, it
is the thing that makes "following a MIDI gets me there" true rather than aspirational, and it is the one named
exception to "a workspace, not a tutor" at the top of this file. That line stands everywhere else — there are
still no points, no streaks tied to it, and nothing that nags. One button turns waiting off.

**On the sight-reading worry:** the claim that falling notes harm sight-reading has **no controlled evidence**
either way. It was not the reason to skip this and it is not a reason to regret it. Sheet music is still what
`Read` is for, and the two do not compete.

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

**M13 — a real soft piano. SHIPPED.** `soundfonts/OsirisUnaCorda.sf3`, built by
[`tools/make_osiris.py`](../tools/make_osiris.py) from [Osiris Piano](https://github.com/sfzinstruments/Osiris_Piano)
(Versilian Studios × Karoryfer, **CC0-1.0**) — a worn Yamaha C2 recorded at half-stick with the soft pedal down and
very low dynamics. Two recorded velocity layers, no round robins. Presets: *Soft Grand* and *Soft Grand + Halo*.

Four things learned doing it, so nobody re-learns them:

- **FLAC's compression does not survive into SF2.** 26.6 MB of source FLAC is 206 MB of raw PCM stereo. The format
  stores PCM; that is the whole size problem.
- **SF3 solves it, in-process.** libsndfile is already linked in to decode the FLAC and it encodes Vorbis too, so the
  build writes SF3 directly — no `sf3convert`, no Polyphone, no GUI step. 52.7 MB of SF2 becomes **5.8 MB**. The cost
  is decode-at-load: ~170 ms per MB, so 880 ms for this font, paid once when a preset that names it is chosen.
- **A malformed SF3 does not error, it faults.** `--sf3 0.0` produces ~141 KB per-sample Ogg streams and FluidSynth
  reads off the end of a buffer and takes the process with it. 0.05 (129 KB) is fine. The exact threshold is inside
  FluidSynth. This is why `make_osiris.py` verifies **in a subprocess**: a font that crashes the synth has to be a
  failed build, not a written file and exit 0.
- **Osiris names an octave below scientific pitch** — its `C3` is MIDI 60. Comparing a rendered middle C against the
  file called `C4` will convince you the mapping is broken when it is not.

Still open, if the felt timbre specifically is wanted: Osiris is *soft-pedal* soft, not felt-hammer soft. The best
felt recording found was [Fuchs & Möhr](https://www.polyphone.io/en/soundfonts/pianos/683-fuchs-mohr-felt-piano)
(3.7× lower spectral rolloff than the GM grand), but the only circulating small build is a third party's derivative
with no licence grant of its own, and the author's clean 1 GB original would need its own reduction. And beware:
the top search hits for "felt piano soundfont" are auto-sampled rips of Spitfire LABS and Spectrasonics Keyscape,
tagged with open-source licences their uploaders had no right to apply.

## Not being built, on purpose

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
