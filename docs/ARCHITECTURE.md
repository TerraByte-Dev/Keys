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

Synthesia and PianoBooster already do falling notes better than this ever will, so Keys deliberately doesn't
build them. The failure mode to watch for is three weeks in with a gorgeous zone editor and no calluses.

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
