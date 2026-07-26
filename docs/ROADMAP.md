# Keys — Roadmap

> Adapted from `piano-practice-app-plan.md` after everything in it was verified against real
> hardware. Milestone numbers kept so the two documents line up. The discipline is the order:
> **M1 is the gate and it has passed** — don't regress it chasing features.
> Facts behind every claim here → `FEASIBILITY.md`.

## Status board

| | Milestone | State |
|---|---|---|
| **M0** | Hello, MIDI | ✅ **done** — `tools/midi_probe.py`, 35/35 notes, C4=60 confirmed |
| **M1** | Sound | ✅ **done & verified** — `backend/play.py`, 3.00 ms exclusive |
| **M1.5** | Fix the piano's touch setting | ⬜ **do this first** — one button combo, no code |
| **M2** | Instrument switching | 🟡 **partial** — 8 presets on keys 1–8, hardcoded. Needs JSON + hotplug |
| **M5a** | Practice timer | ⬜ **next** — the plan says build this before the fun stuff, and it's right |
| **M3** | Zones: split / layer / drum pads | ⬜ |
| **M4** | See what you're playing | ⬜ |
| **M5b** | Metronome + tempo ramp | ⬜ |
| **M6** | Sight reading trainer | ⬜ ← *the highest-value learning feature* |
| **M7** | Play-along / falling notes | ⬜ ← *reconsider; Synthesia already does this* |
| **M8–M10** | Record, stats, package | ⬜ |

## Do these two things before writing any more code

**1. Take the piano off Fixed velocity.** Hold `[GRAND PIANO/FUNCTION]`, press **`B2`** — the
white key immediately left of middle C — release. That's Medium, the factory default. Verify
with `tools/midi_probe.py`: you want a spread across the histogram, not one spike at 64.
Until this is done, dynamics are impossible and M3's velocity curves operate on a constant.

**2. Uninstall the Yamaha USB-MIDI Driver app.** It's still in Add/Remove Programs and can
re-push the INF that broke MIDI in the first place. `{2D488455-3E89-49EF-BA6E-92C2503DC89D}`.

## M2 — Instrument switching *(1 evening)*
`play.py` already switches 8 presets on number keys. To finish it:
- Move `PRESETS` into `presets/*.json`: `name, sf2, bank, program, gain, reverb, chorus`.
- Load a second SoundFont for a better piano (MuseScore_General.sf3, 39.9 MB — its Steinway
  Model D is a real step up from GeneralUser's grand) and prove multi-SF2 switching works.
- **Done when:** piano → Rhodes → organ with no gap and no click.

## M5a — Practice timer *(2 evenings, do it early)*
The original plan argues this should come right after M1 because it's the feature that keeps
you honest, and nothing else on the list is worth much if you don't sit down daily. Agreed.
- SQLite (`store.py`): `session(id, started_at, ended_at, active_seconds, note_count, preset)`.
- Idle detection: pause the clock after N seconds with no note-on. That's what makes
  "34 minutes today" mean minutes *playing*, not minutes with the app open.
- **Done when:** you can see what you actually practiced without having tracked anything.

## M3 — Zones *(1 weekend)*
Model as overlapping zones; overlap **is** the layering mechanism.
```jsonc
{ "lo": 21, "hi": 47, "channel": 0, "bank": 0,   "program": 32, "transpose": 0, "gain": 0.9 }
{ "lo": 48, "hi": 83, "channel": 1, "bank": 0,   "program": 4,  "transpose": 0, "gain": 1.0 }
{ "lo": 84, "hi": 108,"channel": 9, "bank": 128, "program": 0,  "mode": "oneshot" }
```
- **Drums:** channel index 9, and select the kit with an explicit
  `program_select(9, sfid, 128, prog)` — **do not** try to send bank 128 over the wire, it does
  nothing under FluidSynth's default `gs` bank-select mode. Kits: 0 Standard, 8 Room, 16 Power,
  24 Electronic, 25 808/909, 32 Jazz, 40 Brush, 48 Orchestral, 56 SFX. Notes: 36 kick,
  38 snare, 42 closed hat, 46 open hat, 49 crash (all verified correct).
- Velocity curves per zone (linear/soft/hard/fixed) — **meaningless until M1.5 is done.**
- The P-71B has 88 keys, MIDI 21–108. Hardcode that range; it's the only keyboard this targets.

## M4 — See what you're playing *(1 weekend)*
88-key display, note names with correct enharmonic spelling, chord detection with inversions
and slash chords, scale highlighting. This is where the FastAPI + WebSocket + browser split
starts. **Rule:** MIDI events go onto a bounded `deque` drained by a separate async task —
the callback never touches the socket.

## M5b — Metronome *(2 evenings)*
- Schedule clicks inside FluidSynth's sequencer. Never `time.sleep`, never `setInterval`.
- **Tempo ramp** (start 80, +4 bpm every 8 bars, drop back on a miss) is the single feature
  here with the best evidence behind it.
- Measure **drift**, not just per-beat error — that's the failure mode research actually found.
- Free option worth trying first: the piano transmits its own MIDI Clock at ~24 ppqn, so the
  app could follow the instrument's metronome instead of running a competing one.

## M6 — Sight reading *(1 weekend)* — build this before M7
VexFlow renders a random measure; wait for the right notes; log per-note accuracy and reaction
time; then weight generation toward your worst notes. **This is the milestone with the clearest
learning payoff**, and unlike falling-notes it isn't already solved by a $29 app.

## M7 — Play-along / falling notes — *decide before building*
Synthesia ($29 one-time) and PianoBooster (free, GPL) both ship this today, with MIDI import
and hands-separate practice. Rebuilding it is weeks of work for a worse version. Either skip
it and use Synthesia, or build only the part they don't have: scoring against *your* history.
The claim that falling-notes harms sight-reading has **no controlled evidence** either way —
don't avoid it for that reason, avoid it because it's already built.

## M8–M10 — Record, stats, package
Rolling MIDI buffer ("save the last 2 minutes"), `.mid` + `.wav` export via FluidSynth's file
renderer, timing histogram, miss heatmap, PyInstaller + Tauri. All fine, all later.

## Non-goals
- Competing with Synthesia/flowkey/Piano Marvel on content. They have 1,500+ song libraries.
- Supporting any keyboard but the P-71B. Every measurement here is device-specific.
- VST3 / SFZ / `pedalboard`. One engine. If Salamander's sound is wanted, that's a second
  engine and a separate decision.
- Gamification. No evidence it helps musical skill; real risk it substitutes for practice.

## The part that actually makes you a pianist
Get *Faber, Adult Piano Adventures Book 1* or *Alfred's Adult All-in-One* and work through it
in order. Practice slower than feels necessary; practice daily rather than in weekend blocks.
The app measures and drills — it does not sequence a curriculum, and building one is a bad use
of time when good ones exist for $20.
