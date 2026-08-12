# Contributing to Keys

Thanks for your interest in Keys. It's a local-first MIDI practice app built around one hard constraint —
**a note must sound in three milliseconds** — and contributions of all sizes are welcome: bug reports, docs,
presets, features.

This guide gets you from clone to merged PR.

## Ground rules

- Be respectful and constructive. Assume good intent.
- By contributing, you agree your contributions are licensed under the project's [MIT License](LICENSE).
- **Never commit secrets or local state** (`config.local.json`, `keys.db*`, SoundFonts, recordings). They're
  gitignored — keep it that way.

## Getting set up

See the [README quickstart](README.md#get-started). In short:

```bash
git clone https://github.com/TerraByte-Dev/Keys.git
cd Keys
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python keys.py
```

Requirements: Python 3.11 or 3.12 (no `python-rtmidi` wheel exists for 3.13+), FluidSynth 2.x on `PATH`, and a
SoundFont at `soundfonts/GeneralUser-GS.sf2`. Node is optional and only used by the frontend syntax check.

## Before you open a PR

Run the suite locally. It is eighteen scripts, needs no test runner, and takes about two minutes. Thirteen of
them need nothing plugged in and no browser, and carry 946 assertions between them; `ui_check` also wants
Playwright, and the last four want the audio device:

```bash
.venv\Scripts\python tools\music_check.py       # theory: spelling, intervals, chords, scales
.venv\Scripts\python tools\theory_check.py      # the theory surfaces the UI reads
.venv\Scripts\python tools\store_check.py       # SQLite: sessions, streaks, local-day boundaries
.venv\Scripts\python tools\userdata_check.py    # the data directory and what lives in it
.venv\Scripts\python tools\timing_check.py      # onset analysis against synthetic signals
.venv\Scripts\python tools\exercise_check.py    # fingering table, generators, grader, metrics
.venv\Scripts\python tools\backing_check.py     # YouTube URL parsing and the track shelf
.venv\Scripts\python tools\paths_check.py       # the bundle/data split -- runs frozen layouts in subprocesses
.venv\Scripts\python tools\pedal_check.py       # sustain, decay, and what counts as held
.venv\Scripts\python tools\score_check.py       # the score library: import, list, rename, remove
.venv\Scripts\python tools\midi_check.py        # .mid to MusicXML, over a whole corpus if you have one
.venv\Scripts\python tools\ghost_check.py       # ghost mode: gates, the re-strike rule, the falling projection
.venv\Scripts\python tools\ui_check.py          # the DOM helpers the views are built from
.venv\Scripts\python tools\frontend_check.py    # ES module syntax + asset wiring
.venv\Scripts\python tools\audio_check.py       # the device, the buffer, and the port FluidSynth must not steal
.venv\Scripts\python tools\engine_check.py      # zones, presets, drum banks, metronome timing
.venv\Scripts\python tools\pipeline_check.py    # end-to-end above the MIDI port
.venv\Scripts\python tools\looper_check.py      # loop transport, takes, the pedal, the seam
```

`audio_check` wants a piano actually plugged in and will fail its last step without one; that is the check
doing its job, not a broken suite.

`engine_check`, `pipeline_check` and `looper_check` open the audio device **and make noise on purpose**, so they refuse to run
while Keys is up rather than playing a metronome into whatever you were doing — close the app first (`--force`
overrides). In shared mode a second engine opens happily and its clicks come out of your speakers mixed into
your playing, which reads as the app glitching rather than as a test script.

Every check uses a temp database and temp settings. A test that writes to `config.local.json` or `keys.db` is a
bug, and has caused a real one (a stale ramp ceiling silently pinned the metronome at 100 bpm).

**A test whose expected value comes from the thing under test is worthless.** This project has shipped two: an
`infer_key` clamp assertion that only inspected scores which are always positive, and a fingering-table
completeness check that compared the table against a hand-written list in the same file. Both passed for weeks
with the bug sitting next to them.

If you touched anything on the hot path, also play the piano for a minute and watch **Settings → Event pipeline**
for dropped frames.

## Workflow

1. **Open an issue first** for bugs, features, or anything non-trivial (skip it for typos / one-line fixes).
   Describe the problem, repro, and acceptance criteria.
2. **Branch** off `main`: `type/short-desc` (e.g. `feat/12-record-buffer`, `fix/stuck-sustain`). Types:
   `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `perf`, `build`, `ci`.
3. **Commit** with [Conventional Commits](https://www.conventionalcommits.org/): `type(scope): imperative subject`.
4. **Open a pull request** into `main` with a short summary and a test plan. Reference the issue (`Closes #N`).
5. Keep PRs focused.

## The rules that are not negotiable

These are what the app is. Breaking one of them is a regression even if every test still passes — see
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full reasoning and
[`docs/HARDWARE.md`](docs/HARDWARE.md) for the measurements behind them.

- **The MIDI callback is sacred.** No print, no logging, no `await`, no dict allocation, no lock, no I/O.
  Route the note, call the synth, append a tuple to the bounded queue. UI work happens on the drain.
- **Never `sleep` or `setInterval` for musical timing.** Use FluidSynth's sequencer, which rides the audio
  clock. A worker thread may *wait on a doorbell* from the sequencer; it may not *be* the clock.
- **The sequencer's client callback runs on the audio thread.** Keep it to a stamp and an `Event.set()`.
- **Audio never passes through Python.** It is rendered in FluidSynth's C thread. Keep it that way.
- **Don't guess FluidSynth setting names.** Most of the obvious ones are wrong and fail *silently*:
  it is `audio.wasapi.exclusive-mode`, not `.exclusive`; `synth.sample-rate`, not `audio.sample-rate`; and that
  one must be passed as a Python `float` or it routes to the int setter and is ignored. Verify against the
  library before trusting a name.

## Code style & conventions

- **Python 3.11**, `from __future__ import annotations`, type hints on public functions. Standard library
  first — the runtime dependency list is deliberately five packages long.
- **Module docstrings explain *why* the module is shaped the way it is**, not what each function does.
  Comments carry measurements and non-obvious decisions; no comment should restate the code.
- **Frontend: no build step, ever.** Vanilla ES modules, no framework, no `package.json`. Views build their DOM
  once in `mount()` and mutate specific nodes afterwards — nothing re-renders a subtree at 60 Hz.
- **Design system:** the UI is CSS-variable driven (a studio-hardware aesthetic in `frontend/style.css`). Use the
  tokens (`--amber`, `--ink`, `--panel-*`, `--hairline`…) and existing classes; don't hardcode colors. Colour
  carries meaning here — amber means *sounding*, cream means *label*, grey means *inactive*.
- **Add a check for pure logic.** New standalone logic belongs in one of the `tools/*_check.py` suites, in the
  same `step(label, passed, detail)` style.
- **Prose in code is plain ASCII** — the codebase uses `--` for a dash, not an em-dash.

## Reporting bugs

Open an issue with: what you expected, what happened, steps to reproduce, your OS, your MIDI keyboard, and the
output of `tools/midi_probe.py` and `tools/audio_check.py`. Audio bugs are perceptual and easy to misattribute —
those two scripts settle most of them before anyone reads code.

## Reporting security issues

**Please don't open a public issue for vulnerabilities.** Use GitHub's private reporting:
**Security → Report a vulnerability** on the repo.

---

Built by [TerraByte Solutions LLC](https://github.com/TerraByte-Dev). Go practise. 🎹
