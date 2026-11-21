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

## The keyboard is a setting, and three things shift by octaves for different reasons

`settings["instrument"] = {low, high, octave}` is the app's notion of the instrument. `config.instrument_range()`
is the only reader; `config.clamp_range()` is the only writer's funnel, and it enforces a minimum span and snaps
both ends outward onto white keys — so no other file ever has to reason about a key that exists in the model and
not in the drawing. `LOW_KEY`/`HIGH_KEY` survive as the fallback, nothing more.

There are now **three** octave shifts, they are deliberately independent, and confusing them is the mistake this
section exists to prevent:

| Shift | Lives in | Moves | Applies to |
|---|---|---|---|
| **Master** (`OCT −/+`, Z/X) | `settings.instrument.octave` | where your hands sit | sound only |
| **Zone transpose** | `Zone.transpose` | one zone's sound | sound only |
| **Piece fit** (roll bar) | `ghost.js` `model.shift` | where the music sits | the play-along only |

**The master shift is baked into the routing table, not added in `note_on`.** `Engine._rebind_routes()` folds
`master*12 + zone.transpose` into the pre-built table, so the hot path is untouched *and* note-off is correct for
free: `note_on` stores the routes it actually used in `self._held[note]`, so a key still down when the octave moves
is released at the pitch it was struck at. That is the same invariant a mid-hold zone change already relied on. It
is why pressing OCT mid-chord is safe, and why this is not a new class of stuck note.

**Zone matching stays on the raw incoming note.** A split at F#3 with the left zone an octave down stays under the
same physical key when you press OCT — which is what a hardware controller does. The corollary is that the dock
lights the key you *pressed* while a different pitch sounds, and the NOTES/CHORD readout names the untransposed
notes. That is deliberate: they describe your fingers, not your ears.

**Exercises ignore the master shift; play-alongs fit themselves. Do not "fix" one to match the other.** An
exercise's pitches are chosen by the generator, so the right answer to "the target is off my keyboard" is to
generate a target that is on it — `exercises/scales.py` fits by whole octaves and *refuses with a sentence*
(`ValueError` → HTTP 400) when even one octave will not go, because an out-of-range target does not go quietly:
`run.py` blocks on it and an untimed plan has no lookahead to escape with. A score's pitches are fixed by the
composer, so the opposite applies — `ghost.js` moves the whole piece by whole octaves to put the most of it under
your hands, keeping every interval as written, and says so on the bar. A handful of stray pitches is left alone
rather than dragging the piece, because junk from a bad export is not a register problem.

Because the shift is sound-only and the graders are fed the raw physical note, `note_event.note`,
`exercise_step.note` and `sightread_attempt.note` all stay in physical-key space. No historical row changes
meaning and there is no migration.

**Declaring a smaller keyboard is never destructive.** The pedal zone persists what you *asked for* and is clamped
where it is *used* (`Engine._pedal_span()`), so narrow-then-widen restores it exactly; `note_heatmap` and
`range_used` read all of MIDI, so a year of A0s does not vanish from your own history the day you plug in a 61;
and `reset_to_defaults` keeps `instrument.low`/`high` because how many keys you own is a fact about your hardware,
not a preference. `instrument.octave` is not kept, because that one is.

**Never bound a range `<input>` by the declared keyboard when its value is a saved preference.** This is the trap
that broke the rule above twice, and it is not obvious: a range input silently clamps any value assigned to it
into `[min,max]`, and these panels post back whatever the slider holds. Bounding the pedal sliders at the
keyboard meant a saved A0–B3 zone was clamped to C2 on arrival and then written to disk by the next unrelated
nudge of the Decay slider — the setting destroyed by touching a different control, with the label beside it still
confidently reading A0. The sight-reading window went the same way via its *adaptive checkbox*. Both sliders are
now built `min:0 max:127`, narrowed only to fit the value they are about to show, and the panels state the
effective range (`pedal.eff_lo/eff_hi`, and Read's own note) rather than moving your slider behind your back.

## The frontend

No build step, no framework, no `package.json`, no `node_modules`. Vanilla ES modules served straight off disk.
This is a deliberate constraint: the app must start with one command on a machine with nothing installed but
Python, and must keep working in five years without a dependency archaeology expedition.

Views build their DOM once in `mount()` and mutate specific nodes afterwards. Nothing re-renders a subtree
sixty times a second. The keyboard component diffs against the previous held-note `Set` and touches only the keys
that changed.

The cost of no build step is that nothing catches a syntax error until the browser silently refuses to run the
module and the page comes up empty. `tools/frontend_check.py` is the gate that replaces the missing compiler —
it parses every module with Node, verifies every import and asset reference resolves, and checks that every CSS
custom property the keyboard reads is actually defined.

### Which tab holds what, and why

| Tab | Holds | The rule it follows |
|---|---|---|
| **Play** | Instruments (with Profiles), Effects, the metronome (Tempo, Meter, Tempo ramp, Click kit), Loop station, Scale highlighter | what you reach for *while* your hands are on the keys |
| **Practice** | Jam, Now, Timing, Exercises, Songs | what you work *on* |
| **Layers** | What this is, Layout, Build one, Zone editor | how the instrument is *built* |
| **Tools** | Touch response, Pedal, Chords & scales, Backing tracks | the rig you calibrate *once* |
| **Read** | Staff, This run, Exercise setup, Weakest notes | sight-reading |
| **Stats** | fifteen panels of history | what you *did* |
| **Settings** | MIDI input, Latency, Audio output, Reading key, Startup sound, SoundFonts | the machine, not the music |

Two of these moved for the same reason and it is worth writing down: the **metronome** went to Play because
you look at it while you use it, and **Touch response** and **Pedal** left Play for Tools because you set them
up once and then stop thinking about them. "Where is it built" lost to "where are you looking when you need it."

**Panel identity is the slug of the panel's rendered title** (`layout.js` `panelId()`), stored per-view in
backend settings under `ui.layout`. Two consequences that are easy to trip over: renaming a `mod()` title costs
that panel its saved position once, and *moving* a panel into a view that already has one with the same title
silently hands the newcomer the incumbent's saved slot and width. The metronome's kit panel is called
**Click kit** rather than "Sound" for exactly this reason — Play's old master-volume panel slugged to `sound`
too, and the collision would have applied a `col-3` width to a `col-6` panel, differently at different window
widths.

**Panels are lego bricks; a width is a preference; what it renders at is measured.** The arrows step a
panel through spans 3, 6 and 12 of the 12-column grid (a quarter, a half, all of it), one brick a click
(12 -> 6 -> 3 and back), and that choice is what is saved. Bricks pair into rows of 12 (3+3+6, 6+6,
3+3+3+3), so a row you build is a row that closes. 0.14-0.15 stepped in thirds (4, 6, 8, 12): one click
off full made two thirds, which pairs with nothing but another third. Every panel has a **quarter form**:
a quarter is 250-370px at the windows Keys is used in, always the mini tier (`style.css`, "a quarter:
every panel's mini form"), where the same controls go a size smaller, rows stay rows, and at that one step
secondary text goes (empty-state sentences, the tail of a long label via `data-short="mini"`, an
explanation the (i) already holds); Build one shows one card at a time behind a picker, Exercises folds
to chips. What a panel renders at is the larger of its saved span and its **minimum span**: `layout.js`
`fit()` measures each panel's content at min-content, and then laid out, with the panel set to each
span's pixel width in turn (so its `@container panel` queries answer for that width) and takes the
smallest that holds it with nothing running past its box and no select cutting off its choice. The
measurement is against the grid's own width, so a custom page's pane is just a narrower grid: there a
quarter may be too narrow and its arrow says so, a panel rises to what it needs, up to the whole pane,
and the page builder warns when a picked panel's minimum (`pages.js` `PANELS[].min`, held against the DOM
by `ui_check`) is wider than a pane at the current window. The folding rule holds on every step, and
the arrows enforce it: a step that would make a panel more than 15% taller is not offered ("a quarter
would make this panel taller here"), which in a pane, where a quarter can be 160px, is most panels'
quarter. `ui_check` "folding" measures every tab panel at a full, a half and a quarter of the tab, at
fixed widths and with the masonry's draw-down off, and holds each to the rule and to fitting at a
quarter; only the Zone editor's eleven controls, one row at the full width of a wide window, are excused
(in a wide window its arrow stops at full). An open exercise run takes the whole row whatever Exercises was sized
to (`data-min-span`), with Setup over This run in a pane under 760px, and gives the size back on
'Back to the shelf'.

Two render-only adjustments keep the masonry tidy. A panel nobody has sized, with empty columns beside it
for its whole height, is widened over them to the widest brick they make room for (a half beside three
free columns stays a half: three quarters is not a brick); a panel sized with its arrows is not (it is marked `sized` in
the saved layout), because widening it back is what made a click that narrowed it change nothing on
screen, and the columns it leaves are room for the next panel narrowed to move up into. And a panel with
nothing under it until something further down has its card drawn down to meet it, by at most 96px, so
the gaps left above a wide panel close and a bigger hole shows as one rather than as a stretched, empty
card. **Every arrow click changes what you see:** a click steps from the span the panel shows, and each
step is placed first and taken only if the panel then shows narrower (or wider) and the grid gains no
hole narrower than a quarter, where no panel could ever go; an arrow with no such step is disabled, with a
tooltip saying why. A saved layout keeps every panel's order but only the widths of panels the user
sized, as bricks: a sized 4 or 8 from 0.15 becomes a 3 or a 6. The rest of a saved layout is the widths
its view authored when it was saved, so those panels take what their view authors today (a 0.15 Tools
saved as 4+8 by someone who only dragged a panel draws as today's 6+6, not 3+9).

### Pages: whole views, mounted side by side

A **page** is a tab you build yourself (`frontend/pages.js`), and it is made of the four
working tabs rather than of loose panels. A tester soloing over Jam wanted the scale
highlighter and Chords & scales beside it, which live in Play and Tools; moving panels
between tabs would break the table above, so a page instead **mounts whole views into
panes** -- Play, Practice, Layers and Tools, as many as the page holds -- and shows only the
panels you ticked in each. Saved as `ui.pages`, up to six, the first three on `7`, `8`, `9`;
the route is `#page/<id>`.

**A page is columns, not a grid.** Each pane is saved with the column it is in
(`pane.col`, 0 to `cols - 1`) and the page is that many vertical stacks: each pane as tall as
what it holds, 14px under the one above it, and each column scrolling on its own. There are
no rows across columns. 0.14 and 0.15 laid panes out in a grid filled row by row, so a
pane's second row started wherever the taller pane above it ended and four panes at two
columns read as quarters; a page saved then, with no `col`, opens with pane *i* in column
*i* % `cols`, which is where it stood, in the order it had. A column nothing was put in is
left out, not drawn empty. The builder draws the page as its columns, each pane with arrows
to move it a column over or up and down its own; a new column count shares the panes out
in reading order. The scroller to hold still across a rebuild (`layout.js`, `stats.js`) is
the column.

**The nav opens under the pointer.** At rest it is a 52px strip of icons; the pointer over
it (after 80ms, so a pointer crossing it does not flash it) or keyboard focus in it opens it
to full width as a layer over the stage, which does not move or repack, and leaving folds
it after 250ms. It stays open while a page's right-click menu or its rename field is up.
**Keep open** pins it (`ui.nav_pinned`, off by default) as the full-width column the stage
sits beside. It replaced 0.15's Fold button, which had to be pressed every time.

**Each view at most once per page**, and that one rule is what makes it cheap. Every view
module is a singleton with module-scoped state and document-wide ids: two copies of Play in
one document would collide, one each of four different views never does. So the views mount
exactly as they do on their own tab and do not know they are on a page. The router keeps a
list of what is mounted (one entry on a tab, one per pane on a page) and fans frames,
status and refocus out to all of it; a pane's refresh remounts that pane alone, so loading a
preset in Play does not tear down the Jam beside it. The builder's panel list is a static
registry, because it has to offer panels of views that are not mounted, and `ui_check`
mounts each view and holds the registry against the real `data-panel` slugs so it cannot
drift. Stats and the Sound tab are not offered.

Several views in one document share one docked keyboard, so the keyboard's highlight and
its labels have **owners**. `kb.setHighlight(midi, owner)` keeps one set per owner and draws
their union: Play's scale highlighter (`scale`), Chords & scales (`theory`, every key of the
scale or chord across the whole dock) and the touch-setting keys (`touch`). A writer replaces
and clears only its own owner; with one shared set the last writer won, and turning the touch
keys off blanked the scale you had just picked. Labels work the same way:
`setKeyLabel(midi, text, owner)` shows the newest label on a key and clearing it shows the one
beneath, so Jam relabelling its landing key every bar no longer wipes FIXED / SOFT / MEDIUM /
HARD. The router's `clearHighlights()` and `clearAllLabels()` are the only "everything off".
Jam's marks are a separate layer and are not owned. The two layers do not share a look:
while the band plays (`kb--flow`) a highlight is a dashed ring in `--key-mark` and changes
no fill, because as a fill it lost to Jam's on every key Jam marks and kept Jam's chord cyan
on exactly the keys Jam leaves dark. Chords & scales paints the dock only with its Show on
keys on, which a page starts off.

### Jam: a level you pick, and a scale pinned over the key

`frontend/jam.js` plays a band under you and lights what sounds good over the chord playing
now. Two 0.14 additions change what the lights mean, so they are written down here.

**Levels are presets, never unlocks.** Nothing suggests the next one and nothing is saved
about how you did. For Pop and Minor: 1 is one common four-chord loop, played over and over;
2 rotates the four-chord loops; 3 is the whole songbook with its variations; 4 is 3 with a
key change at every second phrase, announced before it lands. Blues has its own four (two
chords, the plain 12-bar, the 12-bar mixed with the quick change, and a turnaround that
moves). Level 4 moves **by a fifth, never to the relative key**: the relative changes none
of the notes, so it would change none of the scale you are meant to be following. Every key
Jam offers has a key a fifth away on offer -- which is why D♭ major and B♭ minor are in the
list: without them F♯ major and E♭ minor had none, and fell back to the relative. The two are
each other's relative as well, so every key also has one to move to when Music switches
between Pop and Minor (B major, the other fifth from F♯, has no relative minor on offer and
would leave that switch on a major key). `jam_check` asserts both by its own arithmetic.

**The scale pin: outline is "in your scale", fill is "good on this chord".** `Scale` pins one
scale over the whole key -- the key's own, its pentatonic or its blues, spelled by the server
like every scale -- and draws it as an outline on every key of it across the keyboard. It
holds still until the key changes. The per-chord fill is unchanged and sits on top, so the
two never contradict: an outlined key without fill is in the scale but rubs on this chord
(rubs are never lit), and a held key in the pin is never painted "spicy". Under a Pentatonic
or Blues pin the band only plays chords the pin does not rub (`harmony.js` `pinSafe()`), so
every outlined key really is good on every chord; under Key there is no such chord set, and
the pin is display only. Guide Dark hides the pin with the rest of the lights.

### The roll is full screen or it is not open

There used to be a 150px strip as well, and it was the worst of both ends: at 100 px/s
it holds about a second and a half, which is neither enough to read a song coming nor
enough to read one you just played — and it took a third of the stage to say so. One
mode now, and it is the good one. `V`, `F` and the ROLL button are three names for the
same door.

That is also where music comes in. `frontend/songs.js` is a drawer inside the roll — not to be
confused with the **Songs** panel, which is `frontend/library.js` and now lives in Practice — and
a `.mid` dropped anywhere on that screen lands on the roll rather than in the engraver —
importing a file in order to learn a song should not hand you a page of notation you
did not ask for, on a screen you have to leave before you can play.

### The printed page is a view of the roll, not another room

Sheet music used to be a panel at the bottom of Practice. That panel is gone; the printed
page is now **a mode of the roll screen and nothing else**, reached by the Sheet button on a
song row where Roll sits beside it. Two doors, one room. They were never different pieces of
music: `backend/midi_import.py`
converts a `.mid` to MusicXML **before storage**, so `/api/scores/{id}/file` and
`/api/scores/{id}/notes` are two projections of one stored artifact and **every** score has
both, whatever it was imported as. Swapping between them is a render, not a conversion.

One rule makes it work: **there is one clock, and it is the ghost clock.** The sheet in the
roll is a *rendering*, never a player. `ghost.js` says so at the top and this is what it is
protecting: ghost mode makes no backend call and no sound, which is why a play-along still
works with the audio engine down. Both modes are therefore play-*along* — the only thing
making sound is you, on the page exactly as on the falling notes.

**What that cost, deliberately.** The deleted panel owned the only frontend caller of
`POST /api/scores/{id}/transport/{action}` — a real player, with play/pause/seek/scrub and
its own tempo, sounding the score through FluidSynth. Merging it into this screen was the
obvious move and is exactly what `ghost.js` forbids, because it would give you two playheads
at two independently-set tempos with one of them audible. So it was dropped rather than
merged. `backend/scoreplay.py` and its route are intact and **unreferenced by the frontend**:
if "play it to me" comes back it should come back as its own surface, not as a second clock
bolted to this one.

What is deliberately *not* built is a moving cursor on the page. Verovio's timemap is
milliseconds against Verovio's reading of the file; the ghost clock is quarter notes against
`backend/score.py`'s reading. Two readers of one file, agreeing on clean engravings and
diverging on repeats, pickups and grace notes — the exact disagreement the engraver was kept
out of the timeline to avoid.

What *is* built is following at the level of the measure, and only that. After a load,
`engrave.js`'s `measureMap()` asks the toolkit two structural questions — the MEI it holds
(`getMEI()`: which measures exist, their ids and written numbers) and `getPageWithElement()`
(which page prints each) — and never touches the timemap. `app.js` then gives each printed
measure a ghost bar **by position** when the two lists are the same length (both are the first
part's measures in document order, so position is exact), and by written number only when
`score.py` folded a repeated number and the lengths differ. On top of that map: the current
bar is tinted, the loop's bars are banded, clicking a measure seeks to its downbeat, and the
page turns or scrolls to the current bar when playback starts, on each new bar while playing,
and on any deliberate move (a bar step, Go to, a scrub, Restart). A page turned by hand
mid-bar stays put until one of those happens. The clock is still ghost.js's alone; the page
only says where its bars are printed.

`frontend/engrave.js` owns the toolkit because there are two callers now, `loadData` mutates
the single instance, and the symptom of a clash is the wrong music on screen rather than an
error. Rendering therefore takes a score id rather than trusting whatever was loaded last.
The 7 MB of WebAssembly is still fetched on demand — on the first press of **Sheet**, never
on opening a song — so someone who only plays along never downloads it.

**A page turn swaps in a page that is already engraved.** Rendering a page is synchronous
-- tens of milliseconds for a sparse page, ~200 ms measured on a dense one -- and the roll caps a frame's step at 0.1 s, so a turn that engraved
in the frame it happened cost the song about 100 ms every time. `engrave.js` keeps up to 24
pages by score id and page, and `prerenderAhead()` engraves the pages after the playhead's,
one per `requestIdleCallback`, **only while the song is stopped**. Doing it while playing,
in the last bars before a turn, only moved the cost: an idle callback that starts a render
runs it to the end, and that frame cost the song the same 100 ms a bar or two earlier. So
each queued page asks again as it comes up and is dropped if Play was pressed meanwhile; a
page that is not ready at a turn (Play pressed straight after opening) is engraved at the
turn, as before. Never during a scrub drag, which would engrave every page the drag
crosses; the page the drag lets go on gets its pages then. It only ever renders the score
already in the toolkit, so it can never be what downloads the engraver, and it touches no clock. The pages are emptied on every
parse, because Verovio mints element ids per load and the measure map is matched on them.
The SVG scales to its box, so a resize or fullscreen changes nothing that is kept. While
playing, the idle render is still one main-thread render: it moves from the turn itself to
a gap in the page's last two bars, just before it.

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

## Two different things are called reverb, and only one of them is a knob

A zone's `reverb` is a **send** — CC 91, how much of that channel goes to the room. The room
itself is FluidSynth's global reverb unit: size, damping, width and level, which is what the
sliders in **Play → Effects** drive. Neither substitutes for the other — send with no room is a louder
cupboard, a cathedral with the send at zero is silence in a cathedral — which is why turning the
send up never produced a concert hall.

Presets used to carry a room of their own and push it onto those sliders when loaded. That was
removed: browsing the shelf silently rewrote a reverb somebody had tuned, which is a bad price
for a room nobody asked to be moved into.

It came back, narrowed to the case that asked for it. A **profile** — a preset you saved
yourself, `saved: true` — carries an `effects` dict holding the two units, and loading one
restores them. The 62 presets that ship with Keys carry `{}` and therefore still cannot touch
your reverb, which is the whole difference between the two and why this is not the old bug
returning. The gate is literally `if preset.effects:` in `server.load_preset`. Everything else
still holds: **the settings store is the sole writer of the reverb unit** — those knobs post to
`/api/settings`, which is the only route into `apply_reverb`, and a profile load goes through
`settings.update()` to get there rather than around it.
`POST /api/fx/send` writes the sends instead — live, on every enabled zone at once, and back
onto the Zone as well as down the wire, because `set_zones()` re-sends each zone's stored send
and would otherwise undo it at the next zone edit.

**The sends are the only per-instrument tone control there is, and that is a measured limit
rather than a gap nobody has got to yet.** MIDI CC 71 (resonance), 72 (release), 73 (attack),
74 (brightness/cutoff) and 75 (decay) do **nothing** in FluidSynth 2.5.7: rendered output is
byte-identical at 0, 64 and 127 across Grand Piano, Warm Pad and Strings, and the GS NRPNs
(1:32, 1:33, 1:99, 1:102) are just as dead. FluidSynth installs the ten default modulators the
SF2.04 spec requires and none of those CCs is among them. So there is no brightness knob and no
attack knob — they would be placebos with a number beside them. What is real, and measured:
`set_reverb`, `set_chorus`, `synth.gain`, and the two sends — CC 91 lengthens the tail by ~140%,
CC 93 widens the stereo image by ~604%.

Two traps in the same corner. `Synth.get_chorus_level()` in pyfluidsynth 1.4.0 is a copy-paste
bug whose body calls `fluid_synth_get_reverb_level`, so the chorus level must never be read back
from the synth. And nothing in FluidSynth clamps any of these values — the UI's slider bounds
are the only guard, which is why `Engine.set_send` clamps as well.

### The quiet end: what was tried, and what it actually proved

Two attempts at a soft piano shipped and were then removed, because the person they were for
tried them and did not want them. What they measured is still true, and is the reason not to
reach for either again.

**GeneralUser-GS's grand has one recording, not eight.** Bank 0 program 0 has 49 preset zones
across eight velocity bands, and every one of them points at the *same* instrument, #257
`Stereo Grand Mellow`, which has no velocity splits at all and 17 samples mapped by key alone.
The bands vary attenuation and filter cutoff, with a velocity→cutoff modulator on the softest.
(34 other instruments in the same font *do* split by velocity, so this is the font's design and
not a parser artifact.) A velocity curve that caps the keyboard is therefore **an attenuator and
a low-pass** — real and measurable, rendered C4 rising in 3.9 ms at velocity 80 against 18.3 ms
at 49 with the spectral centroid down to 0.83× — and genuinely more than turning `synth.gain`
down. It is still not a different instrument, and a real felt upright measures a ~31 ms rise at
*every* velocity. You cannot filter your way to a different excitation.

**A different instrument was not the answer either.** A CC0 Yamaha C2 recorded at half-stick
with the soft pedal down does have the physics — 32 ms rise at middle C against the GM grand's
4, spectral centre 266 Hz against 504 — and shipped as a 5.8 MB SF3. It went out along with the
two presets that were its only reason to exist. The finding worth keeping is about SF3 rather
than about pianos: **FluidSynth peak-normalises each Ogg sample as it loads it**, so the balance
between notes is not the recording's — it is whatever each sample's rms-to-peak ratio happens to
be. Scaling the sample data cannot change it; only the `initialAttenuation` generator can,
because the voice applies that after the loader. Anyone building an SF3 for this app meets that
on day one; the rest of what it cost is in [`ROADMAP.md`](ROADMAP.md).

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
