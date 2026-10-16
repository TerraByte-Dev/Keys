# Keys DAW ("Studio"): Recommended Design

## 1. Verdict

Build Design B (a Python control plane over the one existing FluidSynth) as a MIDI and SoundFont DAW. It is the only option that keeps the 3.00 ms WASAPI-exclusive path. Design A needs shared-mode audio, about 10 ms live latency, and a second renderer. Design C needs a device handoff and a second live engine, and its own author concedes the cheaper shared-synth route may make it unjustifiable.

B has two weaknesses, and this design fixes both:

- **"Provably safe" is overstated.** Python scheduling shares the GIL with the MIDI callback, and nothing has been measured. So Phase 0 captures a baseline and runs spikes S1-S5, and the DAW's transport ships only if hard relative gates pass.
- **Server-side model and undo.** B parses, serialises and undoes project data in the live process. Instead, the browser owns the model, undo and drag preview in typed arrays. Python only stores opaque bytes, receives pre-compiled binary event arrays, and talks to FluidSynth. No JSON project is ever parsed in the live process.

A third constraint is the owner's stated priority and shapes every new endpoint: **security first, latency a close second.** Keys listens on a localhost port that every web page the user visits can reach, and the DAW is the first feature that accepts large binary bodies from that port and feeds them to C code. Section 3 is the contract; the rest of the doc refers to its requirements as R1-R20.

Delivery is staged, with the safe, human-visible value first:

1. **Editor and files** with no audio.
2. **Playback**, gated by spikes.
3. **Record.**
4. **Bounce.**

**Scope is MIDI and SoundFont only.** There are no audio clips, plugins, insert FX or meters. This is a physical limit, because exclusive WASAPI allows one client and Python and the browser may not enter the audio path. Say so in the UI and the docs.

## 2. Architecture

### 2.1 Responsibilities

| Layer | Owns | Never does |
|---|---|---|
| Browser (lazy `frontend/daw/*`) | Project model, undo/redo, selection, drag preview, canvas UI, .mid import/export (JS, in a Worker), compile to binary event arrays, autosave timer, **all parsing of untrusted files** | Touch audio. Stream positions. Hand Python a filesystem path. |
| Python `backend/daw/` | Opaque project bytes on disk, the transport worker (scheduling into the FluidSynth sequencer), ChannelPlan, mixer CC, record tap, bounce supervision, **validation of every byte it forwards to ctypes** | `json.loads`/`dumps` of projects. Edits or undo. Python client callbacks on the audio thread. Trust a client-supplied length, id, name or path. |
| FluidSynth C thread | All rendering, sequencer queue playout | Anything new |
| Bounce child process | Offline render | Run live. Open a socket. |

### 2.2 Live-path rules

- There are zero changes to `on_midi` or `Engine.note_on`.
- DAW playback shares the one synth and `engine.sequencer`, through a new sequencer client `daw` with its own source id.
- **No Python callback is registered** for that client. pyfluidsynth's `Sequencer.register_client` wraps whatever it is given in a `CFUNCTYPE` and will not take `None`, so the `daw` client is registered through the raw `fluidsynth.fluid_sequencer_register_client` cfunc with a NULL callback (a source-only client, which FluidSynth documents). S1 verifies. Playhead comes from `seq.get_tick()` on a normal thread, plus a client-side anchor.
- Events to the fluidsynth destination run as pure C on the audio thread, with no GIL.
- Live zones 0-9 keep working during DAW playback. That is the practice use case.

### 2.3 Complete list of touches to existing live-adjacent code

Each is individually gated and reviewed:

1. `Engine.start`: `synth.midi-channels` goes from 16 to 32. This is permanent and conditional on spike S2. It adds a central `ChannelPlan`.
2. `drain_loop`: one guarded call, `if daw_rec: daw.on_event(t,kind,a,b)`. It is the same tap point the looper uses and is a single append when recording.
3. `status_frame`: add a `daw` dict of at most 160 B at 1 Hz. It is omitted when the DAW is unused.
4. `AppState.shutdown`: call `daw.shutdown()` before `engine.stop()`, after `loop.shutdown()` and `metro.shutdown()`. The ordering is load-bearing.
5. `Engine.restart` hooks: `daw.suspend()` before stop, `daw.rebuild()` after start.
6. Panic wiring: the global Panic button also calls `daw.stop()`. `Engine.panic()` and the hotplug watcher still loop over GM_CHANNELS=16 only, so replugging a keyboard never silences the DAW.
7. A new `APIRouter` for `/api/daw/*`. It is the first router in the repo and is a deliberate structural change. **It inherits nothing protective**: `server.py` today has no CORS middleware (good), but also no `Host` check, no `Origin` check and no websocket origin check (verified; `keys.py` binds `127.0.0.1` by default, and that is the whole defence).
8. **One app-wide middleware in `server.py`** (R1, R2): `Host` allow-list and `Origin` check for non-GET requests and the `/ws` handshake. It is a shared change to the live server, so it is Phase 1, reviewed on its own, and the first thing `tools/daw_security_check.py` tests. It costs one header compare per request and nothing on the drain loop.

### 2.4 Backend package `backend/daw/`

- `api.py` is the router, about 150 lines. It is imported at startup and creates no threads, timers or sockets. JSON handlers are plain `def` so they run in the threadpool. The two byte-body handlers (project PUT, publish POST) are `async def` **only** to stream the body under a size cap (R6); they hand the bytes to `run_in_threadpool` and do no CPU work on the loop.
- Heavy modules are imported lazily on first Play: `transport.py`, `channels.py`, `render.py`, `record.py`. This keeps `sys.modules` clean in Play mode.
- `store.py` handles opaque project bytes. Server-generated ids (`^[0-9a-f]{32}$`) are the only path component ever derived from a request (R5); temp-then-`os.replace` with `O_EXCL` temp names and a retry on a held file (R7); `If-Match` revisions (R7); 16 MB per project, 500 projects, 2 GB per folder (R6); every path resolved and checked to sit inside `<data>/daw/`, reparse points refused (R5); one `threading.Lock` around the folder (R19). The index is the directory listing plus a tiny `index.json` the server writes from validated fields (name ≤ 120 chars, control characters stripped) sent beside the bytes, never read out of them. SQLite is not used, because `store.py` has no migration mechanism.
- `transport.py` is the lookahead scheduler (see 2.6).
- `channels.py` holds the ChannelPlan.
- `record.py` is the drain tap.
- `render.py` supervises the bounce child.

### 2.5 Edit, compile and publish

Authority sits in the browser. Python never sees edits, only compiled snapshots, and it validates every snapshot as hostile (R10) because the client may be a bug, a stale tab, or a web page that got through.

1. The browser compiles per project (not per edit) to flat columnar Int32 arrays: `tick, kind, chan, a, b, dur`.
   - `kind` is note, CC, program, bend, or click.
   - Mute and solo are applied at compile time, so skipped events cost nothing.
   - Quantize, swing and humanize are baked at compile time with a PRNG seeded per note id, so playback equals bounce.
   - Automation is thinned: a fixed 20 ms grid, deduped on the 7-bit value, at most 50 ev/s per lane and 400 ev/s total.
2. The browser POSTs the arrays as a raw `application/octet-stream` body with a fixed binary header (`KPUB` magic, version, event count, tempo-table count, loop region, revision) plus `X-Keys-Daw: 1` (R3). Python checks the byte length is exactly what the header implies **before** building any array, then wraps columns with `array('i').frombytes` (a memcpy) and runs the R10 range checks in 8192-element chunks so no single C call holds the GIL for more than ~0.5 ms. It then publishes the immutable snapshot with one atomic rebind, using the `_rebind_routes` idiom. A rejected publish is a 400 and the previous snapshot stays live.
3. The browser sends a publish on play, on edit-while-playing (debounced 150 ms, on pointerup only), and on tempo or mute/solo changes. The server accepts at most 4 publishes/s and one in flight (429 beyond; R17). The worker flushes only the `daw` source from now+guard (150 ms) and refills.
4. Tick-to-ms conversion happens in Python at schedule time using a cumulative tempo table and bisect (single-tempo fast path). It always uses absolute positions, never accumulated deltas, in Python ints; every absolute sequencer time is asserted `< 2**32` before it reaches ctypes, because `fluid_sequencer_send_at` takes a `c_uint` and ctypes masks out-of-range ints instead of raising (R11).
5. Binary publish payload is about 24 B/event. 20k notes is roughly 1 MB (note-on and note-off as one event with `dur`). Cap: 100k notes, 4 MB per publish, 4096 tempo entries, 20 minutes of project at its slowest tempo.

### 2.6 Transport worker (in `transport.py`)

- One thread, created on first Play and joined on Stop. It sleeps on `threading.Event.wait(timeout)`.
- The 500 ms lookahead refills when under 250 ms remains, so about 4 wakes/s.
- **Anchor:** a back-to-back `seq.get_tick()` and `perf_counter()` pair on this normal thread (looper-style, under 3 ms error), refreshed on each seek, loop wrap and tempo edit.
- **Per refill:**
  - Bisect each snapshot window.
  - Send in slices of at most 128 events, with `time.sleep(0)` between slices and at most 1024 events per refill. These are also the DoS ceiling: no payload can push more than 3000 sequencer events/s whatever it contains.
  - Notes go through `seq.note(source=daw)`.
  - CC, bend and program go through ctypes `fluid_event_control_change`, `fluid_event_pitch_bend`, `fluid_event_program_change` and `fluid_event_bank_select` plus `fluid_sequencer_send_at`. **pyfluidsynth 1.4.0 binds none of those four** (verified: only `fluid_event_note/noteon/noteoff/timer` exist), so each is bound once with `fluidsynth.cfunc` and explicit argtypes, and each may come back `None` on an older DLL; a `None` disables that event kind with a status warning rather than ever making a NULL call (the `_remove_events is not None` idiom). This is spike S1. One event object per thread is created once, checked non-NULL, reused since `send_at` copies it, and deleted in a `finally` at thread exit (R12).
  - Belt and braces at the C boundary: channel, key and value are re-masked (`& 0x7F`, channel ∈ ChannelPlan DAW set) immediately before each `fluid_event_*` call. At ≤ 1024 events per refill it is free.
- **Hitch recovery:** resync the cursor to `now+LEAD_IN`. No catch-up burst.
- **Stop, seek, loop wrap, edit-in-window:**
  1. `fluid_sequencer_remove_events(source=daw)` — only ever our own source id.
  2. CC123 and CC64=0 on DAW channels only.
  3. State chase: bank, program, CC7/10/11/91/93, bend, sustain per track. The data is precomputed by the browser compile as a chase table and validated like any other publish column.
  4. Refill.
- **MVP seek behavior:** notes already sounding at the playhead are not retriggered. The UI shows a note.
- **Click:** compiled into the same event stream, a woodblock key on a reserved drum-bank DAW channel from the same tempo map. This resolves the tempo collision. Starting the DAW calls `loop.stop()` and mutes the old Metronome for the duration. `ghost.js` stays independent.
- **Lifecycle:**
  - `suspend()` stops and **joins** the worker before the sequencer is deleted, so no event object or client id outlives the Synth it belongs to. An ordering bug here is a crash, not an exception.
  - `rebuild()` re-registers the client, re-sends state, re-seeks, and resumes only if it was playing.
  - SoundFont loads and `program_select` for many tracks happen at project open on a worker, behind a "loading" state. They never happen on the transport path. SoundFont names are basenames validated against `engine.list_soundfonts()`; a path never reaches `sfload` from a request (R13).
  - Known limit: the sequencer tick is ms since the Synth started and wraps at 2^32 (49.7 days of uptime). The transport refuses to start past 2^32 - 30 min and says so.

### 2.7 Channels

- `ChannelPlan` in `engine.py`: 0-9 zones, 10-14 looper, 15 metronome, **16-30 DAW tracks (15), 31 DAW click**.
- Enforce the plan in the zone editor (reject 16+) and in `looper._free_channel`. The publish validator rejects any DAW event whose channel is outside the DAW set, so a payload can never address a live, looper or metronome channel (R10).
- Drums are any DAW channel with a bank-128 program.
- Fallback if spike S2 fails: keep 16 channels and let tracks that share a program, gain and pan share a channel. That caps the product at about 5 tracks, and the owner is told before Phase 2 proceeds.
- Raising to 64 channels (48 tracks) is a later phase, requiring a repeat of S2.

### 2.8 Recording (MVP, not later)

- An armed track switches the existing live zone's channel and program through `set_zones`. Monitoring therefore stays on the zero-Python hot path.
- The drain tap appends `(t_perf, kind, a, b)` to a preallocated list. It uses the callback-entry timestamp from `Hub.push`, so timing is accurate to the ms. The conversion perf-to-tick uses the anchor, with under 3 ms error.
- The list is bounded at 262,144 events (~20 MB). Past that the take is closed, flagged `truncated`, and recording disarms, so a track left armed overnight cannot grow memory without limit (R17).
- On stop, the take is delivered to the browser as a small JSON array and becomes a clip. Python parses nothing.
- If `hub.dropped_estimate` moved during the take, the take is flagged incomplete.
- Count-in and pre-roll (70 ms, the looper constant) are included.
- Non-destructive quantize is applied afterwards in the browser.

### 2.9 Bounce child (`render.py`)

- A spawned process (`multiprocessing`, with `freeze_support()` in the frozen entry point). No shell, no user strings in `argv`: the child receives a job id only and finds its inputs under the server-created job directory `<data>/daw/bounce/<uuid>/` (R13).
- Priority and lifetime:
  - `IDLE_PRIORITY_CLASS` via ctypes, affinity to the last 1-2 cores and never core 0.
  - One bounce at a time.
  - A Windows Job Object with `KILL_ON_JOB_CLOSE`, `JOB_OBJECT_LIMIT_PROCESS_MEMORY` (768 MB) and `JOB_OBJECT_LIMIT_ACTIVE_PROCESS` (1), so it can never orphan, balloon or fork.
  - Wall-clock timeout of 3× project length + 60 s, and a 30 s no-progress watchdog; either kills the job and removes its directory.
  - Cancel and progress polled over REST at 2 Hz.
  - Killed in `shutdown()` before `engine.stop()`, which also covers `updater.apply()` → `request_exit()`.
  - `stdin`/`stdout`/`stderr` to DEVNULL plus a log file in the job dir; `close_fds=True`; a minimal environment.
- Synth setup:
  - Its own `fluidsynth.Synth` with `midi-channels=32`, the same SoundFont (a basename validated against the asset search path, R13), and reverb, chorus and gain copied from the live engine.
  - The synth is never started, so there is no audio driver and no winmidi.
  - It runs the same compiled snapshot file on a sample-counted loop and pulls 1024-frame blocks with the `fluid_synth_write_s16` cfunc into a `ctypes.create_string_buffer`, written straight to the WAV. **Not `Synth.get_samples()`**: that method imports numpy, and `packaging/keys.spec` excludes numpy (verified), so the frozen child would die on its first block. S5 is rewritten to test the cfunc path.
  - A 3 s tail is appended, because release tails are suspected of being cut.
  - Stdlib `wave` writes 16-bit PCM to `<job>/mix.wav.part`, renamed to `mix.wav` on success.
- Parity claim: matches live to about 1 ms sequencer quantization. It is not a null-test claim.
- Start conditions:
  - Refuse or queue if `voice_count > 150` or `cpu_load > 70%`.
  - Refuse if the project is over 20 minutes (≈ 230 MB of WAV) or free disk is under 2× the expected output.
  - Pass the snapshot by file path, never over a pipe.
- Output: the client never names a filesystem path. The WAV is served by `GET /api/daw/bounce/{job}/file` and saved through the browser's own download. Job directories are removed on cancel, on failure, on download + 10 min, and swept at startup only when they carry a `.keys-bounce` marker (the updater's marker idiom, so a folder the user made is never deleted).

### 2.10 Frontend

- `frontend/views/daw.js` is a shim of about 1 KB. Its `mount()` does a dynamic `import('../daw/studio.js')` (the Verovio lazy-load precedent).
- Add `daw` to `VIEWS`, a nav anchor `#daw` (key 8), and `ORDER` in `app.js`/`index.html`.
- Do not use `.grid`/`attachLayout`. Use a fixed pane layout (headers | timeline | inspector) with `contain: strict`.
- Add `body.is-studio`, reusing the immersive grid-collapse. The dock keyboard stays visible but short.
- **Rendering:**
  - Canvas layer 1 is static (grid, clips, notes), redrawn only on edit, scroll, zoom, resize or theme change. Theme change is picked up by a MutationObserver on `data-theme`.
  - Canvas layer 2 is the selection and drag overlay.
  - The playhead is one DOM element moved with `transform: translateX`.
  - Notes are SoA typed arrays with binary-search culling and forward cursors.
  - There is one dirty-flag rAF that parks when idle, when `document.hidden`, or when unmounted.
  - DPR is capped at 2.
  - fillRect is batched per colour.
  - LOD draws blocks plus a density strip when a note is under 2 px tall.
  - There are no per-frame gradients and no `roundRect`-per-note.
  - Track headers and mixer strips are ordinary DOM, at most 16, built once and mutated.
  - Knobs reuse `knob()`/`slider()` with a 120 ms debounce.
- **Untrusted text (R14):** project, track, clip and marker names come from files other people wrote. They are rendered only as text nodes (`h()` children, `textContent`) or `ctx.fillText`; `frontend/daw/*` never uses `innerHTML`, `insertAdjacentHTML`, the `h()` `html:` prop, `eval` or `new Function`, and `daw_frontend_check.py` fails on any of them. Files are opened through `<input type=file>` / drag-drop as `ArrayBuffer`, never by URL. Exports are Blob URLs revoked after the click.
- **Transport sync:**
  - Python sends an anchor `{state, tick0, server_ms0, tempo segments}` only on play, stop, seek or tempo change (at most 4/s, at most 300 B, rate-limited server-side).
  - The client extrapolates with `performance.now()`.
  - A 1 Hz heartbeat in the existing status frame carries the `daw` dict `{state, bar:beat, dirty, voices, cpu}` and corrects drift.
  - There is no position stream, no DAW field in the 60 Hz `f` frame, and no new websocket.
- **Unmount:** cancel rAF, disconnect Mutation/ResizeObserver, remove window listeners, release `listenNotes`, register `onPanic`, terminate any import Worker.
- **Cost checks:** measure the `body::after` feTurbulence grain and the `.stage` gradient under a busy canvas, and disable them under `body.is-studio` if they cost more than 1 ms/frame.
- A dev-flag `PerformanceObserver('longtask')` logs anything over 50 ms.

## 3. Threat model and security requirements

### 3.1 Assets

- **A1** The live audio path: no DAW input, however hostile, may crash FluidSynth, hang the audio thread, or move `hub.service` (section 5).
- **A2** The user's files: `keys.db`, `config.local.json`, scores, loops, projects, and anything else under their account. No endpoint may read, write or delete outside `<data>/daw/`.
- **A3** The machine: no code execution beyond Keys itself (no shell, no path from a client, no deserialisation of objects).
- **A4** Privacy: nothing leaves the machine except the documented GitHub GETs and a YouTube embed the user opens. The DAW adds zero egress.

### 3.2 Trust boundaries

| Actor | Can | Cannot | Verified against |
|---|---|---|---|
| **T1 Any web page the user visits**, in any browser | Send *simple* cross-site requests to `127.0.0.1:8770-8789` (GET; POST with `text/plain`, form or no body; no custom headers) with the response unreadable; open `ws://127.0.0.1:port/ws` and **read every frame** (websockets have no CORS); via DNS rebinding become same-origin on the port and read/write the whole API | Read a non-CORS HTTP response; send `application/octet-stream`, `application/json` or a custom header without a preflight, which fails because Keys sends no CORS headers | `server.py` has no CORS middleware, no Host or Origin checks; `/ws` accepts any origin; `keys.py` walks ports 8770-8789 |
| **T2 Other local processes** | Same-user: anything already, out of scope. Other user accounts on the machine: reach the port | — | bind is `127.0.0.1`, no auth |
| **T3 Files the user opens** (`.kdaw` shared by someone else, `.mid`, `.sf2` they dropped in) | Be malformed, oversized, or built to loop a parser | — | parsed in the browser only (R8, R9); `.sf2` by FluidSynth, a pre-existing exposure |
| **T4 The Keys frontend** (trusted, buggy, or a stale tab) | Send a payload that is wrong | — | R10, R7 |
| **T5 Network** | GitHub API/asset (TLS, size and hash checked, URL prefix pinned in `updater.py`); YouTube iframe | — | DAW adds none (R15) |

Out of scope: an attacker running as the same user, physical access, pip supply chain beyond pinning, and FluidSynth's own SF2 parser on SoundFonts the user installs (that exposure exists today through zones).

### 3.3 Requirements

Each: what / enforced by / tested by. `SEC` = `tools/daw_security_check.py` (section 5.3).

| # | Requirement | Enforced by | Tested by |
|---|---|---|---|
| R1 | **Host allow-list.** Every request and the `/ws` handshake must carry `Host` equal to `127.0.0.1:<port>` or `localhost:<port>`; anything else is 421 before routing. Defeats DNS rebinding. `--host` other than loopback prints a warning and still applies the list to the bound address. | One middleware in `server.py` (2.3 item 8) | SEC: `Host: evil.example` → 421 on `/api/state`, `/api/daw/projects`, `/ws` |
| R2 | **Origin check.** If an `Origin` header is present on a non-GET request or the `/ws` handshake, it must be `http://127.0.0.1:<port>` or `http://localhost:<port>`; else 403 / websocket closed before `accept()`. A missing Origin (curl, `build_exe.smoke`) is allowed. | Same middleware | SEC: foreign Origin → 403; WS from foreign Origin closes with 4403; build smoke still passes |
| R3 | **CSRF-proof DAW routes.** Every `/api/daw/*` mutating route requires `X-Keys-Daw: 1`; byte routes also require `Content-Type: application/octet-stream` exactly. A custom header forces a preflight that fails (no CORS headers), so a cross-site simple request can never reach them even on a browser that has no Origin check. | Router dependency | SEC: POST without the header → 400 and no state change; `text/plain` body → 415 |
| R4 | **No CORS, ever.** No `Access-Control-Allow-*` header on any response; no `CORSMiddleware`. | Review; grep gate | SEC: response to a request with Origin carries no ACAO; `daw_isolation_check` greps `backend/` for `CORSMiddleware` |
| R5 | **Path safety.** The only request-derived path component is a project or job id matching `^[0-9a-f]{32}$`. Paths are `DAW_DIR / f"{id}.kdaw"`, resolved and required to sit under `DAW_DIR.resolve()`; `DAW_DIR` and the target are refused if `lstat` shows a reparse point (symlink/junction). Names never touch the filesystem. Reserved device names, `..`, backslashes, ADS (`:`) and UNC are impossible by regex. | `store.py` `_path(id)` | `daw_check`: ids `..`, `..\\x`, `%2e%2e`, `CON`, `a:b`, 31 and 33 chars, uppercase hex → 404, no file touched; a junction at `DAW_DIR` → 503 |
| R6 | **Size caps, streamed.** `Content-Length` required (411); over cap → 413 before a byte is read; body read via `request.stream()` with a running count that aborts at cap+1 (chunked bodies can lie). Project ≤ 16 MB, publish ≤ 4 MB, take JSON ≤ 8 MB, name ≤ 120 chars, ≤ 500 projects, `<data>/daw` ≤ 2 GB (507). | `api.py` | SEC: oversized Content-Length → 413 with no temp file; chunked over-cap → 413, no temp file |
| R7 | **Atomic writes, no clobber.** Temp `<id>.<random>.tmp` opened `O_CREAT\|O_EXCL`, fsync, `os.replace` retried 5× on `PermissionError` (AV/indexer), temp removed on any failure. Revision = 16 hex of sha256 of the bytes. PUT on an existing id requires `If-Match` (428 if absent, 412 if stale). Startup sweeps only `*.tmp` under `DAW_DIR` older than 1 h. | `store.py` | `daw_check`: two concurrent PUTs → one 200, one 412, file has valid magic and length; stray young `.tmp` survives startup; stray old one goes |
| R8 | **Project files are hostile input (browser).** Header length ≤ 2 MB; JSON parsed in try/catch into prototype-less objects (`__proto__`, `constructor`, `prototype` keys rejected); every number `Number.isFinite` + clamped; every array length checked against caps (100k notes, 16 tracks, 512 clips/track, 4096 tempo entries) and against the blob byte range (`off + count*stride <= blob.byteLength`); strings truncated to 120 chars, control characters stripped; unknown keys dropped, not round-tripped; `v` an integer in `[1, CURRENT]`. Any failure is "could not open", never a half-loaded project. | `frontend/daw/container.js` | `daw_check` Node half: fuzz corpus (truncated, oversized counts, NaN/Infinity, deep nesting, `__proto__`, offsets past end) all rejected, no exception escapes |
| R9 | **SMF import is hostile input (browser, in a Worker).** Every read bounds-checked (`DataView` RangeError caught); varlen ≤ 4 bytes per the SMF spec; ≤ 256 tracks, ≤ 100k notes, tick ≤ 2^30, tempo clamped to 10-600 bpm, file ≤ 16 MB; a 2 s budget with `worker.terminate()` on overrun. Python never parses SMF for the DAW. | `frontend/daw/smf.js` + Worker | `daw_check` Node half: fuzz corpus incl. tick overflow, zero-length tracks, running-status abuse, 1-byte files |
| R10 | **Publish payload validation (Python).** Exact byte length vs header before any array exists; `n_ev ≤ 100k`, `n_tempo ≤ 4096`; per column, checked in 8192-element chunks with C-speed `min`/`max`/`sorted` (no GIL hold > ~0.5 ms): tick ∈ [0, 2^30] non-decreasing; kind ∈ {0..4}; chan ∈ ChannelPlan DAW set only; a,b ∈ [0,127] (bend 0..16383, program bank 0..128); dur ∈ [1, 60000]; tempo usec/qn ∈ [100000, 6000000], ticks ascending from 0; loop `a < b ≤ length`; length ≤ 20 min at slowest tempo. Reject = 400, previous snapshot kept. | `transport.py` `validate()` | `daw_check`: one bad value per column → 400; a 100k valid payload validates in < 40 ms wall with no chunk > 0.5 ms; G3 probe during publish |
| R11 | **Int safety at the C boundary.** ctypes masks out-of-range ints silently, so every `c_uint`/`c_int`/`c_short` argument is range-checked in Python first: absolute time `< 2**32`, channel/key/vel re-masked per 2.6. Per-refill caps (P6) and the voice cap (P7) bound the audio-thread work regardless of payload. | `transport.py` | `daw_check` fake-sequencer run: a tick beyond 2^32 stops the transport with an error instead of wrapping |
| R12 | **ctypes lifetime.** All DAW cfuncs bound at import with argtypes/restype; `None` → feature off, never a NULL call. `new_fluid_event()` checked non-NULL. One event per transport thread, never shared, deleted in `finally`. `suspend()` joins the thread before `sequencer.delete()`; nothing holds a client id across `restart`. `remove_events` only with our own source id. | `transport.py` | audio half: 50 restarts mid-playback, 50 panics; no crash; RSS flat |
| R13 | **Bounce child containment.** No shell; `argv` = job id only; snapshot and output paths server-generated under `<data>/daw/bounce/<uuid>/`; client never supplies a path; SoundFont = basename validated against `list_soundfonts()`; Job Object (kill-on-close, 768 MB, 1 process); timeouts and watchdog per 2.9; DEVNULL stdio; marker-gated sweep. | `render.py` | audio half: parent `kill` → child gone ≤ 2 s; cancel → no `.part`; snapshot > 4 MB refused; bounce running when `update/apply` fires is dead before restart |
| R14 | **No HTML injection.** `frontend/daw/*` contains no `innerHTML`, `insertAdjacentHTML`, `html:`, `eval`, `new Function`, `document.write`; names are text nodes or `fillText`. A CSP `<meta>` is added to `index.html` in Phase 1 with measured allowances (`script-src 'self' https://www.youtube.com 'wasm-unsafe-eval'`, `frame-src` YouTube, `connect-src 'self' ws:`, `object-src 'none'`, `base-uri 'none'`). | `daw_frontend_check.py` grep gate; `index.html` | `daw_frontend_check`: grep fails the run; manual: console clean with CSP on across every tab, Verovio and a backing track |
| R15 | **Zero new egress.** No `fetch` to anything but `location.origin` in `frontend/daw/*`; no `urllib`, `socket`, `http`, `subprocess` (beyond `multiprocessing`) in `backend/daw/*`; the child opens no socket. | grep gate | `daw_isolation_check` greps; `perf_check` websocket byte counter |
| R16 | **Data lifecycle.** `<data>/daw` is a `userdata.FOLDERS` category ("Studio projects") with inventory and reset; the updater never touches `DATA_DIR` (verified: staging is beside the app dir). Export is the `.kdaw` itself; import is R8. | `userdata.py` | `userdata_check` counts `daw/`; reset removes only it |
| R17 | **DoS and latency coupling.** Per-process token bucket on `/api/daw/*` (20 req/s, burst 40, 429), publish ≤ 4/s and one in flight, save ≤ 2/s, one bounce; every body capped (R6); no CPU work on the event loop; threadpool work chunked (R10); record buffer bounded (2.8). | `api.py` dependency | G1 variant: a hostile client hammering publish at 50/s with 4 MB bodies while live notes play; `hub.service` within P2; 429s observed |
| R18 | **No leakage.** DAW error bodies are fixed strings (no absolute paths, no `str(exc)`); nothing logs project bytes or names. | review | SEC: every 4xx body matches the fixed-message list |
| R19 | **Concurrency.** Threadpool handlers run concurrently: one lock per store folder, one lock on transport state, snapshot and chase tables rebound atomically, one transport thread, one bounce job. | code | `daw_check`: 8 threads × 200 mixed calls, no exception, invariants hold |
| R20 | **Dependencies and symbols.** No new pip or native deps (section 8). S1/S3 record `fluid_version()` and every DAW cfunc's presence in `HARDWARE.md`; a missing symbol degrades, never crashes. | `transport.py` import | S1/S3 scripts; `build_exe` frozen assertion |

### 3.4 Pre-existing exposures found during this review (outside the DAW, not fixed here)

Listed so the DAW does not inherit them and so the owner can decide separately. All verified by reading the code, none exploited.

1. No Host/Origin/websocket-origin checks: any web page can read the live websocket feed, and DNS rebinding gives a page full API access. R1/R2 fix this app-wide and are the reason the middleware is Phase 1.
2. Empty-body POSTs (`/api/panic`, `/api/loop/{action}`, `/api/metronome/{action}`, `/api/update/download`, `/api/update/apply`, `/api/presets/{pid}/load`) are cross-site triggerable as simple requests. Worst case is a forced update download + restart (pinned to the GitHub release, so annoyance, not code execution).
3. `POST /api/scores` does not check `Content-Type`, so it is cross-site triggerable with a 40 MB body (disk fill), and it is `async def` yet runs the MIDI→MusicXML conversion on the event loop. `midi_import._varlen` and the meta-event reads index past the buffer on a truncated file (IndexError → 500), and a note at a huge tick makes `n_bars` enormous and the measure loop effectively unbounded (CPU/memory, on the loop thread).
4. `POST /api/presets/save` and `DELETE /api/presets/{pid}` build `PRESET_DIR / f"{pid}.json"` from an unsanitised id; a backslash `..` id writes or deletes a `.json` anywhere the user can.
5. `GET /api/instruments?soundfont=` and the zone `soundfont` field are joined to the asset dirs unsanitised, so any file on disk can be handed to `sfload`.
6. `/api/loop/load` joins an unsanitised name (suffix-limited to `.loop.json`).
7. `/api/data` returns `data_dir` (the Windows username) and several errors return `str(exc)` with paths; harmless until R1 is missing.

## 4. Data model

### 4.1 File format

One file per project, `<data>/daw/<uuid>.kdaw`, with a binary container:

- `KDAW` magic.
- Header length, then a JSON header. The JSON header holds `v`, meta, tempo, tracks, clips, patterns' index and automation.
- Followed by binary column blobs (Int32/Uint16 arrays for notes).

Python treats it as bytes. The browser reads and writes it, and reads it as hostile (R8). Rules:

- `v` is an integer with a `migrate(n→n+1)` chain from day one, in JS. Migration runs after validation, on the validated model.
- Readers are tolerant: unknown keys are skipped and **dropped**, never written back.
- Writes are atomic (R7).
- Autosave is debounced 5 s after the last edit and is skipped while a live note arrived in the last 250 ms (the browser knows from `listenNotes`).
- Crash journal: the last 200 undo commands in `localStorage`, in try/catch, as a best-effort aid only. Note the origin is `http://127.0.0.1:<port>` and the port can move (8770-8789), so the journal may not follow a launch; that is acceptable for a best-effort aid.

### 4.2 Schema (header)

```
{ v:1, id, name, ppq:480,
  tempo:[[tick, usecPerQN, curve]], meter:[[tick,num,den]],
  loop:[a,b]|null, markers:[],
  master:{ gain, reverb:{room,damp,width,level}, chorus:{...} },
  tracks:[{ id, name, color, chan (assigned), sf, bank, prog,
            gain(CC7), pan(CC10), expr(CC11), rev(CC91), cho(CC93),
            mute, solo, arm, clips:[clipId],
            auto:{ "cc7":[[tick,val,interp]], "bend":[...] },
            quant:{grid,strength,swing,humanizeMs,humanizeVel,seed} }],
  patterns:{ id:{ len, blob:{off,count}, ccLanes? } },   // notes columnar: start[],dur[],pitch[],vel[]
  clips:[{ id, pattern, track, start, len, offset, loopLen|null, mute }] }
```

- Time is integer ticks (PPQ 480) everywhere. Milliseconds appear only at schedule time.
- Patterns are shared by clips (Ardour source/region idea), so copy and split are cheap.
- Overlapping clips on a track union their events.
- Quantize, swing and humanize are non-destructive clip params with a "bake" action.
- Hard caps: 100k notes per project, 16 tracks (v1), 512 clips per track, 4096 tempo entries, 16 MB file, 20 minutes at the slowest tempo. `sf` is a basename; `color` is an index into the theme palette, not a CSS string.
- The looper's `.loop.json` (absolute ms, tempo-dependent) is not reused. A one-way importer converts it at its saved bpm.

### 4.3 Undo (browser)

- Command objects hold inverse deltas, with a `merge_key`.
- A drag or paste groups to one entry, committed on pointerup.
- Bulk ops (bake quantize, paste of large selections, import) use a per-pattern pre-image snapshot.
- There is a saved-index for the dirty flag, and a failed step rolls back.
- Caps: 200 commands or 8 MB.
- Undo happens entirely in the browser. Python is told nothing until the next publish or save.

### 4.4 Runtime state

- Python holds only the current immutable compiled snapshot (columnar `array('i')`) and the transport state.
- No per-note Python objects.

## 5. Performance contract

### 5.0 Principle

The ranking is live note latency, then UI smoothness, then DAW features. All numeric gates are relative to a baseline captured before any DAW code. Absolute figures below are initial budgets to be tightened after Phase 0, not measured facts. Security requirements that cost CPU (R10 validation, R17 rate limits) are measured under the same gates; a check that moves `hub.service` is redesigned, not dropped.

### 5.1 Budgets and enforcement

| # | Budget | Enforced by |
|---|---|---|
| P1 | **Play mode costs nothing.** No `backend.daw.transport/render/record` in `sys.modules`. No `daw*` threads, child process, timers or sockets. The `f` frame is byte-identical. The `s` frame gains at most 160 B only after the DAW has been opened. The R1/R2 middleware is one header compare per request and does not run on the drain loop. | `tools/daw_isolation_check.py` run on a fresh start. Also asserted in the G1 harness. |
| P2 | **Live latency under DAW load.** 16 tracks, 20k notes, about 150 voices, 8 automation lanes, synthetic `engine.note_on` at 20/s for 60 s. `hub.service` p95 ≤ idle p95 + 100 µs and max ≤ idle max + 500 µs. | G1, a hard merge blocker. Also run with the R17 hostile client. |
| P3 | **Render health.** `cpu_load` ≤ 60% and `voice_count` ≤ 200 during G1. Zero underrun counter movement if the build exposes one. `cpu_load` comes from a ctypes cfunc for `fluid_synth_get_cpu_load` (not bound by pyfluidsynth 1.4.0; verify the symbol, S3). | G2 |
| P4 | **GIL.** A 1 ms-sleep probe thread during project open, publish (including R10 validation), autosave and playback: median ≤ 0.7 ms (baseline 0.53), p99 ≤ 2 ms, no gap > 5 ms. Per-wake worker GIL occupancy < 2 ms. No single C call (`sorted`, `max`, `frombytes`) over more than 8192 elements, because a C call does not yield at the switch interval. | G3 |
| P5 | **Asyncio loop lag** p99 ≤ 16 ms during save, publish and open. | G6 |
| P6 | **Worker rate.** At most 4 wakes/s, 128-event slices, 1024 events per refill, 3000 sequencer events/s sustained, automation ≤ 400 ev/s. | Unit test of the scheduler against a fake sequencer, plus counters in status. |
| P7 | **Voice cap.** DAW simultaneous notes ≤ 128 (live keeps at least half of 256 polyphony). Overflow is dropped at compile or schedule time and counted. Release tail capped via note-off no later than `dur + 1.5 s`. Try `synth.overflow.important-channels` pointing at the live channels (S3). | Compile test. G1 variant with a dense project that checks live notes still sound. |
| P8 | **Network.** Added server-to-client DAW traffic ≤ 4 anchor msgs/s (≤ 300 B) plus the 1 Hz `daw` dict. Zero on the 60 Hz frame. Publish bodies ≤ 4 MB, project bodies ≤ 16 MB. JSON handlers are sync `def`; the two byte handlers are `async def` only to stream under the cap and do no CPU work on the loop; no HTTP response > 256 KB from an async handler. | Websocket byte-counter in `perf_check.py` (delta ≤ 1 kB/s). |
| P9 | **Frontend.** Static redraw ≤ 8 ms. Playhead-only frames are a transform write. 0 rAF callbacks/s when idle or hidden. Scripting+paint ≤ 4 ms/frame with 10k visible notes at 1080p. Per-frame allocation zero. ≤ 20k notes drawn (LOD beyond). 10 consecutive frames > 8 ms drops the static layer to half rate. | `tools/daw_frontend_check.py` (Node, no DOM) for the culling and time/pixel math. Longtask log. Manual WebView2 run. |
| P10 | **Memory.** Python RSS delta at 20k notes ≤ 30 MB. Browser DAW heap ≤ 150 MB. Bounce child transient ≤ 300 MB (Job Object hard limit 768 MB), freed on exit. Undo ≤ 8 MB. Record buffer ≤ 20 MB. | `perf_check.py` RSS delta. Browser heap read manually, or via `performance.memory` in the Playwright run. |
| P11 | **Autosave and publish** never run while a live note arrived < 250 ms ago. Autosave work happens in the threadpool and moves bytes only. | Test with a fake clock. |
| P12 | **Bounce** is IDLE priority, one core, one job, with refuse and queue conditions as in 2.9. Job Object so no orphan. | Bounce tests (section 5.3). |

### 5.2 Auto-degrade ladder

Driven by `cpu_load`:

1. If idle `cpu_load` is above 50% before Play, refuse Play.
2. If `cpu_load` > 80% for 3 s while playing:
   - First, lower the DAW voice cap (128 → 96 → 64).
   - Then lower interpolation to linear.
   - Then, as a last resort, turn off reverb and chorus. This one also affects live sound, so it needs a toast and is restored on Stop.
3. The user is always told what changed.

### 5.3 Test and gate suite

These follow the repo style: standalone scripts under `tools/` with `step(label, passed, detail)` and a nonzero exit on failure. Audio checks call `_audio_guard.require_quiet()`.

- `tools/perf_check.py` (Phase 0). It captures:
  - `hub.service` median/p95/max.
  - Loop lag via a `call_later` drift probe.
  - GIL wake probe.
  - Process CPU and RSS.
  - Websocket frames/s and bytes/s.
  - `voice_count`, `cpu_load`.
  
  It runs idle, with DAW assets present but unused (must equal baseline), Studio playing, and bounce running. Results go into `docs/HARDWARE.md`.
- `tools/daw_security_check.py` (**SEC**, Phase 1, grows every phase). Starts the server on a scratch port with a temp `KEYS_DATA_DIR` (the `build_exe.smoke()` pattern) and drives it with `urllib` and a raw socket for the websocket handshake. Needs no piano; a refused audio device is fine because the routes under test do not touch the engine. It asserts R1-R4, R6, R18 and the no-side-effect half of R3 against the live app, and runs the store (R5, R7) and validator (R10, R11) against throwaway folders. It is a merge blocker from Phase 1 on, alongside G1/G3.
- `tools/daw_check.py`, pure-logic half, covering Python-side and Node-run JS-side logic:
  - Tempo-map tick↔ms round-trip, multi-tempo, no drift over a simulated 20-minute project.
  - Window bisect returns exactly `[t0, t1)`.
  - Compile: mute and solo skip events, automation thinning respects caps, humanize determinism by seed.
  - Undo property test (random ops, undo all equals original).
  - SMF read/write round-trip, plus the R9 fuzz corpus (checked-in files under `tools/fixtures/daw/`).
  - Container round-trip, the `v1→vN` migration, plus the R8 fuzz corpus.
  - Store: atomic write survives a stray `.tmp`, `If-Match` conflict and the 428 path, size cap, every R5 id case, concurrent writers (R19).
  - Publish validator: one bad value per column, exact-length mismatch, chunk timing (R10).
  - ChannelPlan never hands out 0-15 to the DAW, and the validator rejects 0-15.
  - Scheduler against a fake sequencer: slice size, no client callback registered, hitch resync, 2^32 refusal (R11).
- Audio half (guarded; makes noise):
  - Flush correctness: no hung notes, and `voice_count` returns to the live-only count within 200 ms after stop, seek, loop and edit.
  - Restart-during-playback (G4) and panic-during-playback (G5), including that hotplug panic leaves the DAW untouched, 50× each for R12.
  - Bounce: non-silent WAV with length = project + tail, cancel leaves no partial file and no orphan, the child dies if the parent is killed, the priority class is IDLE, the Job Object memory limit is set, an oversized snapshot is refused (R13).
  - Live-vs-bounce parity: note-on times differ by ≤ 2 ms.
- G1, G2, G3, G6 as in 5.1. **G7** = SEC passes, `daw_frontend_check` grep gates pass (R14), `daw_isolation_check` egress greps pass (R15).
- `tools/frontend_check.py` covers new JS syntax and import resolution automatically (verified: it syntax-checks every `frontend/**/*.js` and resolves relative and dynamic imports). The R14 grep lives in `daw_frontend_check.py`.
- `tools/build_exe.py` additions:
  - `backend.daw.*` imports in the frozen app.
  - The frozen app spawns the bounce child and renders a 1-bar non-silent WAV through the `fluid_synth_write_s16` path.
  - No numpy in the tree.
  - Zip growth is at most +0.5 MB.
- Manual, owner-only: exclusive-mode feel at the 144-sample period with the DAW playing, an audible-glitch listening pass at 16 tracks and 150 voices, WebView2 frame time with the grain overlay on and off, and one pass of every tab with the CSP meta on.
- Honest gap: end-to-end latency cannot be measured in software (WASAPI loopback is silent in exclusive mode). Only proxies are gated: `hub.service`, loop lag, `cpu_load`, GIL probe, and frame bytes.

## 6. MVP scope vs later

### MVP (v1)

- **Editor.**
  - Arrange view and piano roll: add, move, resize, delete, velocity, snap grid, copy and paste, select.
  - Non-destructive quantize with a bake action.
  - Track headers: name, SoundFont/program, volume, pan, reverb and chorus sends, mute, solo, arm.
  - Loop region, constant tempo and meter UI (the tempo map exists in the data model).
  - Undo and redo.
- **Files.** Project list, open, save, autosave. .mid import and export in JS. Opening someone else's `.kdaw` is supported and treated as hostile (R8).
- **Playback.** Play, pause, stop, seek, loop, state chase, mixer CCs, a click from the same clock, edit-while-playing, restart and panic lifecycle, channel-scoped stop.
- **Record.** Armed-track recording, count-in, an incomplete-take flag, and quantize after.
- **Bounce.** Whole-mix WAV from a background child with progress and cancel, delivered as a browser download.
- **Security.** R1-R20, the app-wide Host/Origin middleware, the CSP meta, and `daw_security_check.py`.
- **Instrumentation.** `cpu_load`, voices, scheduler lateness, dropped-event counter, rejected-publish counter, 429 counter, and the Latency panel stays visible while the DAW plays.
- **Docs.** `ARCHITECTURE.md` (DAW rules, channel plan, the localhost threat model in one paragraph), `HARDWARE.md` (baseline, cfunc symbol table), `PACKAGING.md` (stale 90.7 MB number fixed), and the provenance log.

### Later

- Tempo-map and meter-change editing UI, with the click following it.
- Automation lane editing UI (step, linear, Hermite; thinning on record).
- Stems export (one render per track).
- Sounding-note chase on seek, and takes / alternate playlists.
- A step-sequencer pattern type that compiles to the same note arrays.
- 48 tracks by raising to 64 channels, after repeating S2.
- Markers and sections, and FLAC export (needs a libsndfile ctypes binding, no numpy).
- OffscreenCanvas rendering only if profiling shows a need.
- The ROADMAP M8 "save last two minutes" buffer reuses the same SMF writer.
- A per-launch random bearer token handed to the window via the URL fragment, if R1-R3 ever prove insufficient (it is what the pywebview launch makes easy; a browser-tab launch would need the token printed).

### Explicitly not planned

- Audio-clip tracks, plugin hosting, per-track inserts, EQ and compression, and audio meters. They would need a native mixing sidecar, which changes the central invariant. Revisit only as a deliberate separate decision.
- Per-track reverb beyond the CC91 send. It needs `synth.effects-groups > 1`, which has a significant CPU cost.
- A second live synth, and a Studio mode that gives up the 3.00 ms path.
- Loading SoundFonts from arbitrary paths, or any endpoint that takes a filesystem path.

## 7. Phased build plan

Every phase after Phase 0 ships only if G1, G3 and G7 pass. Any regression blocks the merge.

### Phase 0: Baseline and spikes (no feature code)

**Deliverable:**

- `tools/perf_check.py` and baseline numbers recorded in `docs/HARDWARE.md`.
- Five 30-minute spikes:
  - **S1:** ctypes `fluid_event_control_change`, `_pitch_bend`, `_program_change`, `_bank_select` through `fluid_sequencer_send_at` on a `daw` client registered with a NULL callback via the raw cfunc. Verify audibly and by timing. Record `fluid_version()` and which symbols resolve.
  - **S2:** `synth.midi-channels=32` through pyfluidsynth and the sequencer, with channels 16-31 sounding and the 3.00 ms figure unchanged.
  - **S3:** `synth.overflow.important-channels` exists and works. `fluid_synth_get_cpu_load` is callable via cfunc.
  - **S4:** does a 1000-event refill or `remove_events` move `hub.service`? This is the mutex question.
  - **S5:** a never-started `Synth` plus the `fluid_synth_write_s16` cfunc into a `create_string_buffer` renders a non-silent block with numpy absent from the process (`sys.modules` asserted), and the sequencer advances on the sample clock.
- A spike script per item that prints pass/fail with timing.

**Verify:**

- The baseline is repeatable: 5 runs give a tolerance.
- **Go/no-go for Phase 2:**
  - S2 or S4 moving the latency number kills the shared-synth playback plan.
  - If S2 alone fails, drop to the 16-channel fallback (about 5 tracks) and ask the owner.
  - If S4 shows contention, shrink windows and flush less before proceeding.
- Phases 1 and 4 do not depend on the spikes except S5, so the editor and bounce are not blocked by S1-S4.

### Phase 1: Editor and files, no audio

**Deliverable:**

- View shim, lazy load, nav entry.
- The app-wide Host/Origin/websocket-origin middleware in `server.py` (R1, R2) and the CSP meta in `index.html` (R14). Both are shared changes and get their own review.
- `backend/daw/api.py` and `store.py` (opaque bytes, atomic, `If-Match`, caps, index, R3-R7, R17-R19). `userdata.py` and `userdata_check.py` count `<data>/daw/` (R16).
- The JS model, undo, container codec with validation and migration (R8), SMF import and export in a Worker (R9), canvas arrange and piano roll.
- `daw_check.py` pure-logic half with both fuzz corpora, `daw_frontend_check.py` (Node) with the R14 grep gate, and `daw_security_check.py` (R1-R7, R18).
- The Play-isolation check with the R15 egress greps.

**Verify:**

- G0 re-run: baseline unchanged with the DAW view present and with it open and idle.
- `frontend_check.py`, `daw_frontend_check.py`, `daw_security_check.py` pass (G7).
- `build_exe.py --verify` smoke still passes with the middleware on (its `urllib` calls carry no Origin and `Host: 127.0.0.1:<port>`).
- A 10k-note project scrolls and zooms inside the P9 budgets, and rAF/timer counts are 0 when off the DAW tab.
- Manual: every existing tab, a backing track and the Sheet view work with the CSP on.

### Phase 2: Playback (gated by S1-S4)

**Deliverable:**

- ChannelPlan and the 32-channel `Engine.start`.
- `transport.py` as in 2.6, the binary publish endpoint with the R10/R11 validator, state chase, mixer CCs, the click, the status `daw` dict and anchor messages.
- Restart and panic hooks, the `cpu_load` cfunc, and the auto-degrade ladder.
- Audition (click a note, `fs.noteon` on that track's channel, channel validated against the plan).
- SEC grows: R10, R11, R17 (hostile publish client).

**Verify:**

- G1, G2, G3, G5, G6 and G7 pass. G1 is also run with the R17 hostile client.
- G4 passes (restart mid-song: no exception, resumes within 1 beat), 50× for R12.
- The flush test shows no hung notes, and the `daw` client is registered with a NULL callback (asserted by reading `Sequencer.client_callbacks`, which must not grow).
- Manual: the owner plays live while the DAW plays, and feels the same latency.

### Phase 3: Record

**Deliverable:**

- The drain tap, bounded preallocated list, count-in and pre-roll, and the incomplete and truncated take flags.
- The armed-track zone switch.
- Take delivery under the R6 cap.

**Verify:**

- Recorded onsets versus synthetic MIDI sent at known perf times are within 3 ms.
- G1 re-run with recording active; a 262,144-event synthetic take closes itself with `truncated` and RSS stays inside P10.
- The drain-loop delta is a single guarded call (diff review).

### Phase 4: Bounce

**Deliverable:**

- `render.py` as in 2.9 with R13 containment.
- Packaging changes: `keys.spec` hiddenimports and `freeze_support()`.
- Frozen-build assertion for a 1-bar bounce through the `write_s16` path.
- SEC grows: R13 (job id regex, no client path, marker-gated sweep).

**Verify:**

- G1 with a bounce running: `hub.service` unchanged. The refuse and queue rules work.
- Cancel and parent-kill leave no orphan or partial file; the Job Object limits are read back from the handle.
- Bounce + `update/apply`: the child is dead before `request_exit`.
- Parity within 2 ms.
- Zip growth at most +0.5 MB.

### Phase 5: Hardening and extras (each its own gated step)

- Tempo-map UI and click slaving.
- Automation lane editor.
- Stems export.
- 64-channel option after a repeat of S2.
- Fixes for the pre-existing exposures in 3.4, if the owner wants them in this stream.

**Verify:** the phase gates above, repeated.

## 8. Deps and bundle impact

- **Pip dependencies:** none. Do not add `numpy`, `soundfile` or `mido`. `mido` is pinned but unimported, and is not relied on. Verified versions: FastAPI 0.140.0, Starlette 1.3.1, uvicorn 0.51.0, pyfluidsynth 1.4.0, pywebview 6.2.1, Python 3.11.1.
- **Native libs:** none new. The bundled `libfluidsynth-3.dll` is reached through ctypes. The bundled `_multiprocessing.pyd` is used for the bounce child.
- **Frontend:** no vendored libraries. Estimated 80-120 KB lazy JS plus CSS, plus one Worker file for SMF import.
- **Python:** estimated 1800-2800 lines, about 120-170 KB compiled. The validator and middleware are most of the growth over the previous estimate.
- **Packaging:**
  - `keys.spec` gets hiddenimports for the lazily-imported `backend.daw.*` modules. A missing one fails silently.
  - `multiprocessing.freeze_support()` goes in the entry point.
  - `build_exe.py` gets the assertions from section 5.3.
  - numpy stays excluded; nothing in `backend/daw` may import it, including through pyfluidsynth's `get_samples`.
- **Size targets:** `dist/Keys` stays at about 82 MB and the zip at about 53 MB, within +0.5 MB. Every update re-downloads the full zip and needs 3x free space.
- **Runtime memory:**
  - Play mode: +0.
  - DAW open: a few MB in Python.
  - Bounce: a transient of about 50 MB, hard-capped at 768 MB by the Job Object.
- **Invariant:** "audio never passes through Python" and "browser never in the audio path" both stay intact. `ARCHITECTURE.md` gains the DAW rules, the channel plan, the statement that the DAW is MIDI and SoundFont only, and the one-paragraph localhost threat model.
- **Docs:** `PACKAGING.md` (stale numbers), `HARDWARE.md` (new baselines, cfunc symbol table), and `ROADMAP` (M8 is superseded for WAV and .mid export).

## 9. REA and reference-DAW usage, and legal guardrails

### 9.1 REA

- **Facts from the research:**
  - REA (`github.com/morluto/rea`) is an MCP server and CLI for inspecting binaries, JS/Electron apps, .NET and websites.
  - It is MIT licensed. The gh API showed about 287k stars at read time. That is unusual but was checked live (not re-verified in this review).
  - Documented lookalike and trojan clones exist. Setup edits the agent's MCP config and can pull Hopper, Ghidra or IDA. Windows Ghidra support is experimental.
- **Recommendation: do not install it for this project.**
  - The open-source DAWs ship source.
  - Closed DAWs' EULAs typically forbid disassembly. Their architecture is also publicly documented.
  - REA's own README example, "build a similar feature", is the legally risky use.
  - The only defensible uses are open file formats (SMF, SF2, open DAW project XML), which ordinary parsers and published specs handle better, and debugging Keys' own build.
- If a specific open-format question ever comes up, install only from `github.com/morluto/rea`, `rea.tools` or the `rea-agents` npm package, and read the setup diff first.

### 9.2 Reference DAWs

**Study for ideas only**, re-implementing from scratch:

- **Ardour (GPL):**
  - Source/region split.
  - Presorted event arrays.
  - SessionEvent queue.
  - Overwrite on edit.
  - `midi_chase`.
  - Freewheel export (run the same scheduler offline).
  - Automation thinning.
- **LMMS (GPL):** automation interpolation modes (discrete, linear, Hermite). Do not copy its play-handle or mutex-handshake model.
- **openDAW (AGPL):** delta-log undo with grouped marks and a saved-index, visible-range rendering, tolerant readers plus migration.
- **Zrythm (AGPL):** edit model versus a realtime cache with an explicit publish step.
- **Licensing:** Keys is MIT. Never copy or port code from any of these, including paraphrased translations. Reimplement from the idea.

### 9.3 Closed DAWs (FL, Ableton, Reaper, etc.)

- Black-box use as a user only.
- Public manuals and tutorials, plus Task Manager measurements from outside.
- Do not point any disassembler, decompiler or REA at them, their plugins or their DLLs.
- Do not copy their UI layouts, skins, icons, factory content, presets or protected file formats. Do not decrypt or crack project files.
- Clean-room pattern: an observer writes a behavior spec in their own words from public sources, and the implementer works only from that spec.
- Interoperability goes through open formats: SMF, WAV and, later, DAWproject.

### 9.4 Provenance log

Keep `docs/PROVENANCE-daw.md`. Each design idea gets one line: source (spec, open-source project and license, or own measurement), what was taken (idea only), and the date. This protects the project if provenance is ever questioned. Nothing here is legal advice.

## 10. Open questions for the owner

1. **Scope acceptance:** Are you OK with v1 being MIDI and SoundFont only (no audio-clip, plugin, EQ or meter features)? This is a physical limit of keeping 3 ms exclusive mode. Anything beyond it means giving up that mode or building a separate native audio sidecar.
2. **Track count versus risk:** The MVP is 15 tracks, via 32 synth channels, if spike S2 passes. If S2 fails, the fallback is about 5 tracks with shared channels. Do you want to continue in that case, or stop at the editor and bounce phases?
3. **Permanent channel-count change:** `synth.midi-channels` goes to 32 for everyone and for all of Keys, not just in the DAW. Are you OK with that, given the 3.00 ms spike check? Say no and Phase 2 must use the 16-channel fallback.
4. **Degrade policy:** When `cpu_load` is high, is it acceptable for the ladder to drop voices first, then interpolation quality, then (last resort) reverb and chorus on live sound, with a toast? Or should it only ever shed DAW voices?
5. **Hardware sign-off:** Only you can run the exclusive-mode feel test at the 144-sample period with the DAW playing, and the 16-track listening pass for glitches. Phases 2 to 4 do not close until you do.
6. **REA:** Confirm you are skipping it, as recommended. If you want it anyway, say what specific question it would answer.
7. **Naming and placement:** Confirm the nav label and slot ("Studio" or "DAW", key 8), and that the project lives in `06-library/Keys` (not an LLC product).
8. **App-wide middleware (R1, R2):** it touches `server.py` and every existing route, and it is the fix for 3.4 item 1. Authorise it in Phase 1, before any DAW endpoint exists? The alternative, DAW-only checks, leaves the websocket readable by any site.
9. **CSP (R14):** it can break the YouTube backing player or Verovio if an allowance is wrong, and it is verified by a manual pass. Ship it in Phase 1 behind that pass, or defer to Phase 5?
10. **Bounce delivery:** the client never names a save path; the WAV arrives as a browser download from `/api/daw/bounce/{job}/file`. Acceptable, or do you want a native Save-As dialog (pywebview `create_file_dialog`, which would be the first endpoint to take a path, under its own review)?
11. **Pre-existing exposures (3.4):** fix items 2-7 in this stream (small, mostly input validation) or track them separately?
12. **Shared projects:** confirm that a `.kdaw` from another person is plain data with plain-text names and nothing executable, so R8 is the whole story.

## Changed in security review

- Added section 3 (threat model, trust boundaries, R1-R20, pre-existing exposures); renumbered 3-9 to 4-10 and updated cross-references.
- Corrected: bounce cannot use `Synth.get_samples()` (imports numpy, excluded by `keys.spec`); now the `fluid_synth_write_s16` cfunc. S5 rewritten.
- Corrected: `register_client("daw")` with no callback is impossible through pyfluidsynth's wrapper; NULL callback via the raw cfunc. Phase 2 verifies `client_callbacks` does not grow.
- Corrected: `fluid_event_control_change/pitch_bend/program_change/bank_select` and `fluid_synth_get_cpu_load` are not bound by pyfluidsynth 1.4.0; each is a cfunc that may be `None`.
- Corrected: `fluid_sequencer_send_at` time is `c_uint` and ctypes masks ints; explicit `< 2**32` check and the 49.7-day wrap noted.
- Corrected P8: the two byte handlers must be `async def` to stream under a cap; "sync def" now applies to JSON handlers only.
- Added to 2.3: the router inherits no Host/Origin/websocket-origin protection; item 8 (app-wide middleware).
- Added caps: record buffer, project length, tempo entries, project count, folder size, rate limits; bounce Job Object limits, timeouts, watchdog, marker-gated sweep, no client-supplied paths.
- Added R8/R9 browser-side hostile-file rules, R14 grep gate and CSP, R15 egress greps, `tools/daw_security_check.py`, G7, and security gates per phase.
- Updated Open Questions 8-12.
- Verified by reading: `server.py`, `keys.py`, `engine.py`, `looper.py`, `metronome.py`, `scoreplay.py`, `midi_in.py`, `hub.py`, `store.py`, `userdata.py`, `updater.py`, `version.py`, `config.py`, `scores.py`, `midi_import.py`, `backing.py`, `frontend/ui.js`, `app.js`, `index.html`, `packaging/keys.spec`, `tools/build_exe.py`, `tools/frontend_check.py`, and pyfluidsynth 1.4.0's bindings; framework versions from the venv.
- Not verified: FluidSynth's own range checks in `fluid_synth_cc`/`noteon` for channels ≥ 16 under `midi-channels=32` (S1/S2 must); `synth.overflow.important-channels` behaviour (S3); whether WebView2 handles a `Content-Disposition: attachment` download without extra pywebview wiring (Phase 4 verifies); REA star count; any claim in section 9 beyond licence names.
