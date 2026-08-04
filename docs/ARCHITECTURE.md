# Architecture

How Keys is built, and — more usefully — **why it is built this way**. Nearly every decision here exists
because the obvious alternative was measured and found wanting. The numbers behind these claims are in
[`HARDWARE.md`](HARDWARE.md); this document is the reasoning.

## The shape

One process, four threads, and a strict rule about which of them is allowed to be slow.

```
  piano ──USB──> rtmidi callback thread ──> FluidSynth (C render thread) ──> WASAPI ──> speakers
                          │                                     ▲
                          │ bounded deque                       │ sequencer (metronome)
                          ▼                                     │
                  asyncio drain @60Hz ──> websocket ──> browser UI
```

| Thread | Owns | May block? |
|---|---|---|
| rtmidi callback | routing a note to the synth | **never** |
| FluidSynth render (C) | all audio | **never** |
| asyncio loop | HTTP, websocket, database, chord detection | yes, visibly |
| metronome worker | refilling the sequencer's lookahead | yes |

Audio is rendered in FluidSynth's own C thread and **never passes through Python**. The browser is a display
bolted to the side of the audio path, not a link in it. If the UI stalls completely, the sound does not change.

## The five things that will bite you

**1. `sys.setswitchinterval(0.0008)` is the single highest-leverage line in the codebase.**
Python's default (0.005) gives a ~14.5 ms median delay before the MIDI callback thread gets scheduled — worse
than every buffer setting combined. `0.001` changes *nothing* (14.4 ms); the threshold is strictly below
1000 µs. At 0.0008 the median is 0.53 ms, which is the idle baseline. It must run before any thread starts,
which is why it lives in `backend/__init__.py` — Python guarantees a package's `__init__` executes before any
of its submodules, so there is no import order a caller can pick that skips it.

**2. Most of the traffic on a MIDI port is garbage.** A digital piano transmits MIDI Clock (`0xF8`) roughly 24
times a second forever, plus Active Sensing (`0xFE`) every ~300 ms, whether or not anyone is playing. Measured
on real hardware: 5171 messages in one session, 5096 of them noise — 98.5%. **The callback's first branch is
`if status >= 0xF8: return`**, before the status byte is even decoded. rtmidi is told to filter them too; both
belts are free and the failure mode of neither is acceptable.

**3. `fs.start()` will steal the MIDI port.** pyfluidsynth's `start()` unconditionally builds a MIDI driver that
opens a winmidi input — the same port the callback owns. Left alone, **every note sounds twice**. It is
neutralised by pointing FluidSynth's own driver at a device that cannot exist
(`midi.winmidi.device = "__none__"`). The resulting console error is intentional and is explained on startup.

**4. The obvious setting names are the wrong ones, and FluidSynth fails silently on all of them.** It is
`audio.wasapi.exclusive-mode` (not `.exclusive`), `synth.sample-rate` (not `audio.sample-rate`), and that one
must be passed as a **Python float** — an `int` routes to `fluid_settings_setint` and is ignored without
raising. `audio.periods` does nothing in exclusive mode; `audio.period-size` does nothing in shared mode.

**5. The sequencer's client callback runs on the audio thread.** `Sequencer(use_system_timer=False)` plus
`register_fluidsynth()` gets the clock advanced by FluidSynth's render thread — self-advancing, drift-free, and
the same clock the sound comes out of. (`use_system_timer=True` is deprecated in FluidSynth 2.x and delivered
no callbacks at all in testing.) The consequence: **that callback must be as cheap as the MIDI callback.**
`backend/metronome.py` only stamps an observation and sets a `threading.Event`; a worker thread woken by that
doorbell does the actual scheduling. The timing still comes entirely from the audio clock.

## Concurrency without locks on the hot path

Zone changes never mutate the routing table in place. A whole new immutable table is built and then **rebound in
one atomic assignment**, so the callback thread sees either the old table or the new one and never a
half-written one. That is the entire reason there is no lock anywhere near the hot path.

Which channels a held note was routed to is remembered at note-on, so changing zones while a key is down still
releases the right voices.

The queue between the callback and everything else is a `collections.deque(maxlen=N)`. When the UI falls behind,
appends keep succeeding and the oldest events fall off the back: **dropped frames, not audio glitches**. A 1 Hz
status heartbeat carries the engine's own held-note set, so a UI that missed a note-off un-sticks itself within
a second rather than staying wrong until the next keypress.

## Two clocks

The sequencer tick clock is derived from the sound card, and `time.perf_counter()` from the CPU. They are
different clocks and drift against each other by hundreds of parts per million. Comparing a player's note onsets
to the metronome grid therefore means *relating* them, which `Metronome.clock_fit()` does by least squares over
many observations — individual samples jitter by tens of milliseconds, because they are delivered to Python on
the audio thread, and the fit averages that out.

## What cannot be measured

**Nothing in software can measure true end-to-end latency.** WASAPI loopback taps the post-mix engine, misses
the driver, DMA, the DAC and the acoustics entirely, and returns pure silence when the output is
exclusive-mode. Any "latency self-test" claiming a round-trip number is lying.

Keys measures the one segment it can actually see — MIDI callback entry to synth call returning — reports it in
microseconds, and states plainly in the UI what it excludes.

## Exclusive mode is a trade, not a free win — so it is not the default

Exclusive mode is where the 3.00 ms comes from, and it takes the output device away from every other application
on the machine for as long as Keys runs. Not "turns them down": Spotify goes silent, Discord goes silent, a
browser reports an audio rendering error. That is WASAPI working as designed.

**A practice app you leave open for an hour cannot also be the application that breaks the computer's sound.**
Seven milliseconds does not buy that, so Keys ships in shared mode and exclusive is one click away. Shared is
roughly 10 ms, which is inside the range a real piano action already spans between a soft and a hard keystroke.

Three positions, exposed in **Settings → Audio output**:

1. **Shared** (default) — everything coexists; Windows chooses the buffer, so expect roughly 10 ms.
2. **Pin Keys to an output Windows is not using** — an interface, headphones on a second endpoint, an idle HDMI
   device. Exclusive there costs nothing, because nothing else wants it: 3 ms *and* your music.
3. **Exclusive on the default device** — the tightest possible feel, and everything else goes quiet until you
   close Keys or switch back.

Rate, buffer, mode and device are all negotiated when the WASAPI stream opens, so changing any of them requires
closing and reopening the stream. `Engine.restart()` does that while preserving zones and the loaded preset, and
suspends the hot path first — emptying the routing table and channel list so the callback returns at its first
branch instead of calling into a Synth that is being freed.

## The frontend

No build step, no framework, no `package.json`, no `node_modules`. Vanilla ES modules served straight off disk.
This is a deliberate constraint: the app must start with one command on a machine with nothing installed but
Python, and must keep working in five years without a dependency archaeology expedition.

Views build their DOM once in `mount()` and mutate specific nodes afterwards. Nothing re-renders a subtree
sixty times a second. The 88-key component diffs against the previous held-note `Set` and touches only the keys
that changed.

The cost of no build step is that nothing catches a syntax error until the browser silently refuses to run the
module and the page comes up empty. `tools/frontend_check.py` is the gate that replaces the missing compiler —
it parses every module with Node, verifies every import and asset reference resolves, and checks that every CSS
custom property the keyboard reads is actually defined.

### The roll is full screen or it is not open

There used to be a 150px strip as well, and it was the worst of both ends: at 100 px/s
it holds about a second and a half, which is neither enough to read a song coming nor
enough to read one you just played — and it took a third of the stage to say so. One
mode now, and it is the good one. `V`, `F` and the ROLL button are three names for the
same door.

That is also where music comes in. `frontend/songs.js` is a drawer inside the roll, and
a `.mid` dropped anywhere on that screen lands on the roll rather than in the engraver —
importing a file in order to learn a song should not hand you a page of notation you
did not ask for, on a screen you have to leave before you can play.

### The printed page is a view of the roll, not another room

Sheet music used to be a panel at the bottom of Practice. It is now also a toggle in the
rollbar, because the two things were never different pieces of music: `backend/midi_import.py`
converts a `.mid` to MusicXML **before storage**, so `/api/scores/{id}/file` and
`/api/scores/{id}/notes` are two projections of one stored artifact and **every** score has
both, whatever it was imported as. Swapping between them is a render, not a conversion.

One rule makes it work: **there is one clock, and it is the ghost clock.** The sheet in the
roll is a *rendering*, never a player. The score transport in Practice is a different thing
that makes sound through FluidSynth, and wiring it to this screen would give you two
playheads at two independently-set tempos with one of them audible. `ghost.js` says so at
the top and this is what it is protecting: ghost mode makes no backend call and no sound,
which is why a play-along still works with the audio engine down.

What is deliberately *not* built is a moving cursor on the page. Verovio's timemap is
milliseconds against Verovio's reading of the file; the ghost clock is quarter notes against
`backend/score.py`'s reading. Two readers of one file, agreeing on clean engravings and
diverging on repeats, pickups and grace notes — the exact disagreement the engraver was kept
out of the timeline to avoid. If following is ever wanted, the honest route is measure-level
only: `ghost.bar()` returns a written measure number, and both sides agree on bar numbers by
construction.

`frontend/engrave.js` owns the toolkit because there are two callers now, `loadData` mutates
the single instance, and the symptom of a clash is the wrong music on screen rather than an
error. Rendering therefore takes a score id rather than trusting whatever was loaded last.
The 7 MB of WebAssembly is still fetched on demand — on the first press of **Sheet**, never
on opening a song — so someone who only plays along never downloads it.

### The note roll has two directions, and they are modes rather than a mixture

`roll.js` owns pixels; `ghost.js` owns the clock, the gates and the hand filter. The split is what lets the
rules be checked without a canvas — `tools/ghost_check.py` drives the real module from Node with no DOM, no
audio device and no piano.

In free play a bar is born at the key you pressed and **rises**, so its length is how long you held the note. In
ghost mode the whole paper **falls** instead: the printed piece and your own playing descend together past a
now-line 56 px above the keys.

The obvious alternative — keep your playing rising and let the piece fall to meet it — was measured and found
wanting. Both bars would be anchored at the same edge, so they grow from it *together* rather than tessellating,
and at the halfway point of a held note the target and the answer are exactly coincident. Running both downward
instead buys the property the mode exists for: two edges descending at one rate, so the vertical gap between
your bar and its ghost is your timing error and does not drift as the pair travels. `ghost_check` asserts that
over four seconds of frames, not once.

Ghost mode makes **no backend call and no sound**. The timeline is fetched once from
`GET /api/scores/{id}/notes` and everything after that is arithmetic. That is deliberate, and it is why a
play-along still works with no SoundFont, no audio device and the headphones unplugged — unlike the score
transport, which 503s when the engine is down.

## A preset carries its room, and two different things are called reverb

A zone's `reverb` is a **send** — CC91, how much of that channel goes to the room. A preset's
`space` is the **room** — FluidSynth's global reverb unit, its size, damping, width and
level, which is the same unit the sliders in Settings drive. Neither substitutes for the
other: send with no room is a louder cupboard, and a cathedral with the send at zero is
silence in a cathedral. That is why turning the send up never produced a concert hall, and
why every shipped preset now states its room even when that room is the default. Loading one
moves the Settings sliders with it, because they are that unit and showing a room you are not
in would make them lie.

`whisper` is the other half, and it is worth being precise about what it is, because the first
version of this section was wrong.

The curve is a **ceiling at velocity 74**. `soft` and `softer` do the opposite of what their
names suggest — they are named for the touch they *reward*, and they lift quiet notes, so a
light hand sounds louder.

It was claimed here that the ceiling reaches a softly-struck *recording*. It does not.
GeneralUser-GS's `Grand Piano` has eight velocity bands, and all eight point at one instrument
(`Stereo Grand Mellow`), which has no velocity splits and 17 samples mapped by key alone.
There is a single recording of each note. The bands vary attenuation and filter cutoff, with a
velocity→cutoff modulator on the softest one. So `whisper` is **an attenuator and a low-pass**
— which is real and measurable (rendered C4 rises in 3.9 ms at velocity 80 and 18.3 ms at 49,
with the spectral centroid down to 0.83×), and is genuinely more than turning `synth.gain`
down, but it is not a different instrument.

**A soft piano is a different instrument**, so the app now ships one. The soft pedal moves the action so the
strings meet un-grooved, softer felt; the contact lengthens and the upper partials never happen. That is in the
recording, before a microphone is involved, and no filter reaches back for it. `soundfonts/OsirisUnaCorda.sf3` is a
Yamaha C2 recorded that way — at middle C it rises in 32 ms against the GM grand's 4, with its spectral centre at
266 Hz against 504 — and *Soft Grand* uses no velocity curve at all, because the curve exists to drag a bright
piano somewhere it does not want to go and this one is already there.

It is 5.8 MB because it is **SF3**: the same structure as SF2 with each sample stored as its own Ogg Vorbis stream.
FluidSynth 2.5.7 reads it, `Engine.load_soundfont` already accepted the extension, and the same libsndfile that
decodes the source FLAC encodes the Vorbis — so `tools/make_osiris.py` writes it directly with no external
converter. The trade is decode time rather than disk: ~170 ms per MB, which is why a second font belongs behind a
preset that loads it on demand and not in the boot path. Zones carry their own `soundfont`, so *Soft Grand + Halo*
layers this piano under a pad from GeneralUser with both fonts resident at once.

## Storage

SQLite, WAL mode, `synchronous=NORMAL`, one connection behind one lock. A per-thread pool would be faster; on a
single-machine practice log that speed buys nothing and costs the ability to reason about it. Every statement is
wrapped so a database error degrades the practice history rather than stopping the piano making sound.

A "day" is a **local calendar day computed in Python**. SQLite's `date()` reads a unix epoch as UTC, which files
an evening session under tomorrow — precisely when practice happens.

## The honest framing

**Keys is a workspace, not a tutor.** It does not sequence a curriculum, gate content behind progress, or tell
you what to play next. It opens, it sounds good immediately, and it gets out of the way. A method book and
daily practice are what make you a pianist; this is the room you do that in.

That framing decides features. A workspace owes you: instruments within reach, splits and layers you can build
in seconds, a metronome that cannot drift, material to work on when you want it, and a record of what you did.
It does not owe you a lesson plan, and it must never nag.

Falling notes were on that list for a long time, on the grounds that Synthesia and PianoBooster already do them.
They got built anyway, because the estimate was wrong — see [`ROADMAP.md`](ROADMAP.md). The framing survives:
ghost mode hands you a piece and gets out of the way. It does not pick the piece, score you, or keep a record.

The failure mode to watch for is unchanged and is three weeks in with a gorgeous zone editor and no calluses.

Some widely repeated practice advice did not survive checking, and the design reflects that:

- **"Hands separate, then together"** is weakly supported. Duke, Simmons & Cash (2009, *JRME* 56(4):310–321)
  found hands-*together*-early was one of eight behaviours distinguishing the top-ranked pianists.
- **Slow practice is supported but narrower than assumed.** Furuya, Nakamura & Nagata (2013, *BMC Neuroscience*
  14:133, n=12) found gains held for two months — but only in the trained hand, only for similar sequences.
- **Gamification has no evidence base for musical skill.** The retention numbers usually cited measure
  engagement in a language product, not skill acquisition. Hence: no points, no streak-shaming, no badges.
- **Metronome: keep it, instrument it differently.** Bock & Duke (ISME 2026, n=36) found accuracy is better
  *with* an audible click, but without one players hold consistent spacing while drifting in overall tempo.
  Which is why Keys measures **drift**, not just per-beat error.
- **"30 minutes a day" is folklore.** The daily-frequency principle is sound; the specific number is marketing.
  Hence a streak that asks for one real minute, and a clock that counts only what you actually played.
