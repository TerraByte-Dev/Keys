# Packaging

How Keys becomes a Windows application you can hand to someone, how that application
replaces itself when a new version exists, and what is still missing.

```bash
.venv\Scripts\pip install pyinstaller
.venv\Scripts\python tools\build_exe.py
```

That produces `dist/Keys/` — 90.7 MB across 330 files, runnable — and then, only if every
check below it passed, the two files a release is made of:

- **`dist/Keys-<version>-win-x64.zip`** — ~55 MB. The application directory with
  **nothing above it**: `Keys.exe` and `_internal/` sit at the root of the archive.
- **`dist/Keys-<version>-win-x64.zip.sha256`** — one line in `sha256sum` format, so
  `sha256sum -c` and a person reading it both work.

Both names come from `VERSION` in `backend/version.py`, so the zip cannot claim a
version the app does not. Upload both to the GitHub release **by hand** — nothing in
this repository publishes anything, and that is deliberate.

The "no wrapper folder" rule is a **gate, not an assertion**: `dist/Keys` is checked for
exactly those two entries *before* anything is written, and the zip is deleted again if it
reads back as anything else. It used to be asserted after both files existed, so a build
with the wrong shape still left a zip and a matching checksum on disk, named like a release
and ready to upload, while the script exited 1.

The script builds and then **opens the result and runs it**, because every way this
build can be broken is a way PyInstaller reports success.

## The two roots

An update replaces what the release ships, and `_internal/` goes entire. With `keys.db`
living beside `keys.py` — which is *inside* `_internal/` the moment the app is frozen —
one press of Install would delete every session you ever recorded, with no error and
nothing to undo it, discovered weeks later.

So `backend/config.py` resolves two roots:

| | Where | Contains |
|---|---|---|
| **Bundle** | `sys._MEIPASS`, i.e. `Keys/_internal/` | `frontend/`, shipped `presets/`, the SoundFont, the DLLs |
| **Data** | `%LOCALAPPDATA%\Keys` | `keys.db`, `config.local.json`, `recordings/`, presets and SoundFonts you added |

In a source checkout the two are the same directory and nothing changes — your database
sits next to `keys.py` exactly as before. They diverge only when frozen, which is the
only case where they must. `KEYS_DATA_DIR` overrides both.

Assets are searched **data first, bundle second**, so a SoundFont or preset you added
survives an update and shadows a shipped one of the same name. `tools/paths_check.py`
runs the frozen layouts in subprocesses with a fabricated `sys.frozen` and
`LOCALAPPDATA`, because the split is decided at import time.

## Why `--onedir`

1. **FluidSynth is LGPL.** The licence requires its libraries ship as replaceable
   shared objects — loose DLLs beside the executable. `--onefile` unpacks to a temp
   directory on every launch, which is not that.
2. `--onefile` re-extracts ~60 MB to `%TEMP%` at every start. On an app whose entire
   pitch is three milliseconds, a multi-second splash screen is the wrong trade.
3. An update replaces exactly what `--onedir` produces — `Keys.exe` and `_internal/`,
   two entries and nothing else — which is why the release zip carries those two at its
   root with no wrapper folder above them.

## What PyInstaller will not tell you

Every one of these builds cleanly and fails at runtime:

- **Hidden imports.** uvicorn selects its HTTP and websocket implementations by string
  at runtime. The import graph cannot see them, so they must be listed by hand.
- **The ctypes cliff.** `pyfluidsynth` is a pure ctypes binding — nothing in the import
  graph mentions `libfluidsynth-3.dll`. Omit it and you ship an app that starts, shows
  its UI, and is silent.
- **Over-eager excludes.** `numpy` is excluded on purpose: 25 MB, almost all OpenBLAS,
  imported by `pyfluidsynth` only inside `get_samples()` and `raw_audio_string()` —
  the two functions that pull rendered audio into Python, which this app can never call
  because *audio never passes through Python*. That is an architectural invariant, and
  the exclusion is safe exactly as long as it holds.

`tools/build_exe.py` therefore ends by launching the build with a throwaway
`KEYS_DATA_DIR`, playing a three-note chord, and asserting the voice count. **It makes
a sound** is the only check that distinguishes a working build from a silent one.

## Latency

Packaging cannot affect it — FluidSynth renders on a native thread Python is never in.
Two things could:

1. **An entry point that starts a thread before `import backend` runs
   `sys.setswitchinterval(0.0008)`.** Worth ~14 ms, and silent. `keys.py` imports
   `backend` first and says why; keep it that way.
2. **An embedded browser engine grabbing the audio endpoint** before exclusive mode is
   acquired. This is the argument against bundling a webview in v1.

Verified on the frozen build: engine up, SoundFont loaded from the bundle, chord
sounded. Note that a fresh install starts in **shared** mode -- exclusive takes the
output device from every other application, which is not a thing to do to someone by
default. `tools/build_exe.py` reports whichever mode the build came up in.

## Updating in place

**Settings → About** checks GitHub for a newer release, downloads its zip, and replaces
`Keys.exe` and `_internal/` on restart. All three of those are separate presses.

### Why three steps and not one button

Nothing happens on launch, nothing happens on a timer, nothing happens in the
background, and nothing downloads or installs on its own — ever. That is the product,
not a phase it is in. An app that quietly contacts a server every time you open it is
not local-first regardless of what its README says.

Each press is the one that spends something you might not want spent:

1. **Check** is an HTTP GET for a public release list. It sends nothing about you, and it
   is the only time Keys talks to its own project at all.
2. **Download** is ~55 MB of your connection, and a file on your disk.
3. **Restart and install** interrupts whatever you were playing and replaces the
   application.

Folding them into one button decides all three for you. Note also that a staged download
is *not* consent to restart: the third press is its own question, asked after the second
one has finished.

The download runs on a worker thread. It cannot run on the asyncio loop — that loop
drains MIDI at 60 Hz, and 55 MB of `urllib` sitting on it would freeze the note display
for the length of the download.

Progress is **pulled, not pushed**. Nothing about an update rides the 1 Hz status
heartbeat the metronome and the transport use, because the About panel lives in the
settings overlay and that overlay receives no status frames at all; the panel polls
`GET /api/update/status` on a 1 Hz `setInterval` instead. Same rate, different mechanism
— and the reason a transfer survives closing the overlay is that the panel never owned
it in the first place.

### The swap, in the order it happens

**A running process cannot replace its own installation.** Windows holds `Keys.exe` and
every loaded DLL in `_internal/` — `libfluidsynth-3.dll`, `SDL3.dll`, the CPython
`.pyd`s. Measured: renaming the application directory fails with **winerror 32** when the
process's cwd is inside it and **winerror 5** when it is outside; the running `.exe` and
the loaded DLLs *can* be renamed but *cannot* be deleted. So the swap belongs to
something that runs after Keys has exited, and on a machine with nothing but Windows on
it the only thing guaranteed present is `cmd.exe`.

**It moves entries, not the directory, and that is a repair rather than a preference.**
The first version of this moved the whole application directory aside and finished with
`rd /s /q` on it. The release zip has no wrapper folder — that is the whole point of the
`--onedir` shape — so anyone who used **Extract Here** into a folder that already had
their own files in it made *that* folder the application directory. A successful update
would then have moved their files aside along with Keys and deleted them at the end, with
no error and nothing to undo it. So the helper works on the entries the release actually
ships, `Keys.exe` and `_internal`: it renames those aside in place, moves the staged ones
in, and deletes only the aside entries it created itself. A folder Keys was extracted
into badly is now a mess rather than a loss.

The aside suffix is `.old-<8 random hex>`, generated per update, and nothing is deleted to
make room for it. A fixed `.old` could collide with a name already in the application
directory — measured, a user's own `Keys.exe.old` and `_internal.old\receipts\2024.pdf`
were both destroyed by the pre-delete that cleared the way — and a delete is the one act
that turns a recoverable failure into a permanent one. A suffix that cannot collide needs
no pre-delete, and the cleanup at the end names only the entries that run created. The
cost is that debris from a *failed* swap is no longer cleared by the next install: those
entries are the only working copy of the app, which is why the paragraph below says they
are left on purpose.

That list is **enumerated from the staged tree** when you press install, never globbed from
the folder it is installing into, so a file Keys did not ship is out of reach by
construction. The price is a release that *drops* a top-level entry: the old one would be
left behind. Today the app is exactly those two names, so there is no such case, and a
manifest inside the zip to catch it would be a second source of truth about the zip. A
release that *adds* one is fine, but only because the aside move is guarded on the entry
existing: `move` on a source that is not there sets errorlevel 1, which is
indistinguishable from "something is holding it", so an unguarded aside spun the full
wait ceiling on a brand-new entry and then gave up — Keys closed on the third press and
never came back.

1. **Pre-flight, before a single byte is downloaded.** Beside the application directory:
   `mkdir` a probe directory, write a file in it, rename it, remove it — three distinct
   rights, exercised against the real filesystem, in microseconds. An install under
   `C:\Program Files` without elevation fails at the `mkdir` with winerror 5. Then a free
   space check for **three times the declared asset size** — 165 MB against 0.5.1's
   55,115,305-byte zip, which is that zip plus the 90.7 MB it expands into with room to
   be wrong, rather than a round number somebody liked. Never download 55 MB you have
   already been told you cannot install.
2. **Download** beside the application directory, to `Keys.update.zip` — beside it, and
   not under `%LOCALAPPDATA%`, because every step after this one is a `move` and a move
   across volumes is a copy that fails with winerror 17. Check the length, and the SHA-256
   against whatever the release publishes (see the last section — sometimes that is
   nothing, and it says so); **extract to `Keys.new`**, a sibling for the same reason and
   never inside the directory being replaced. Every member path is resolved and the
   archive is *refused*
   if any of them escapes the destination. `zipfile` already contains that attack, but it
   contains it by silently rewriting the path, and an archive that is not the shape we
   published should stop the update rather than install something rearranged. The
   extracted tree is then asserted to have `Keys.exe` and `_internal/` **at its root** —
   that check is what catches a future `build_exe.py` that starts wrapping the zip in a
   `Keys/` folder, which would otherwise install an empty tree and report success. The
   zip is deleted the instant that succeeds: the tree is the artefact from then on, and
   a staged update that kept both would sit on 146 MB instead of 91 for as long as it
   waits. The staged tree also gets a `.keys-staged` marker file written into it before
   the first byte is fetched, and **nothing removes either staging path without it** —
   `sweep()` runs unprompted at every launch, and a `Keys.new` someone extracted
   themselves beside the app was being rmtree'd past the recycle bin before the engine
   even started.
3. **You press restart.** Keys writes a helper `.cmd` into the data directory, spawns it
   hidden, and then shuts down *properly*. Not `os._exit()`: the normal teardown ends
   the practice session, which is a database write. Hard-exiting to install an update
   would silently discard the session you were in the middle of, which is the exact class
   of data loss the two-root split exists to prevent. The lock the helper waits on is
   *not* opened here — every instance has been holding one since it started, see below.
4. **The helper waits until no lock file is left** — not on our PID, and not on a `move`
   that fails while we are alive. Neither of those works here:
   - Under `DETACHED_PROCESS`, `tasklist` writes nothing at all and `tasklist | find`
     **hangs forever**. Under `CREATE_NO_WINDOW`, `tasklist` works but `timeout /t` dies
     instantly with "Input redirection is not supported". Their console requirements are
     exactly inverted; no choice of creation flags satisfies both. And with Git for
     Windows' `usr/bin` on `PATH` — the "Unix tools" install option — `find` and `timeout`
     resolve to the GNU binaries and report a **live** process as gone.
   - **A failing `move` is no longer the test either, and that is what the entry-wise
     design costs.** Measured here: renaming `_internal` with its DLLs loaded *succeeds*,
     and so does renaming the running `Keys.exe`. Only the application directory itself
     refuses — winerror 32 — which is exactly the thing this design stopped touching. The
     directory swap got its liveness test for free; this one has to bring its own.

   So Keys holds a lock file open in the data directory and the helper spins on
   `del` against it. `del` on a file a live process holds open fails with "being used by
   another process" and **leaves the file there**; the handle dies with the process; so
   the file going away *is* the process being gone. `del` reports errorlevel 0 either way,
   which is why the test is `if not exist` and not the errorlevel. The only external tool
   is `%SystemRoot%\System32\PING.EXE`, called by absolute path, used as the sleep — it is
   the one that works under every combination of creation flags and stdin. Measured: ten
   waits took 10.41 s, so 150 of them is a ceiling of **156 s, 2.6 minutes**; the real
   frozen build needed **one**.

   **Every instance holds its own `update-lock-<pid>`, not just the one you pressed
   Install in, and the helper waits for the whole `update-lock-*` pattern to go.** A
   single lock opened at apply time proved the *applying* Keys had exited and said
   nothing at all about a second one — and there is no single-instance guard anywhere,
   because `keys.py`'s `free_port()` deliberately walks 8770-8789 so a second copy starts
   happily. Measured: with a second Keys running the old build, the swap completed and the
   final `rd /s /q` deleted the `_internal` that instance was executing out of, through
   the *success* path. `del` on the pattern removes the locks nobody holds and leaves the
   rest, so a crashed instance clears itself by construction and this cannot wedge; a
   second live Keys simply drives the helper to its ceiling with **nothing moved**, which
   is the outcome `:giveup` exists for.

   **The helper is spawned `CREATE_NO_WINDOW`, and that is not cosmetic — do not change it
   back to `DETACHED_PROCESS`.** Detached means cmd has no console, so `PING.EXE` allocates
   its *own*, and a console window pops for every pass of the wait loop. That was the first
   version, and the person it was built for watched black windows appear on their machine
   unprompted and reasonably concluded they had been compromised. An updater that looks like
   malware is a broken updater however correct the swap is. `CREATE_NO_WINDOW` gives cmd a
   console that is never shown and `PING` inherits it: measured, the script runs end to end,
   both sleeps land 1.04 s apart, and zero visible windows belong to it or any descendant.
5. Then, in this order: every entry **that exists** moved aside to `<name>.old-<hex>` →
   every staged entry moved in out of `Keys.new` → **relaunch with
   `start "" /D "<app dir>"`**, because plain `start "" "<exe>"` hands the new instance
   the *helper's* cwd → delete the aside entries → delete the `.keys-staged` marker and
   `rd` the now-empty `Keys.new`, without `/s`, so a tree that is somehow *not* empty is
   left for `sweep()` instead of being recursively deleted → `del "%~f0"`. That last one
   really does delete a running batch file, and execution stops at it, so nothing may be
   written after that line. It is also the marker: every exit path ends there, so a
   `keys-update.cmd` still sitting in `%LOCALAPPDATA%\Keys` and **younger than five
   minutes** means a swap never finished. Older than that and it is swept and ignored —
   the wait ceiling is 156 s, so a script that old cannot still be running, and treating
   its mere existence as proof of a live swap is what disabled the updater permanently
   after one failed spawn. One more thing about `del "%~f0"`: the truncation lands before
   the `exit /b <n>` on the next line is ever read, so **every** path exits 1 whatever it
   says. Nothing reads that code, and the `keys-update.log` lines are what tell the paths
   apart.

Two details in there are one-line fixes for failures that are not obvious. The cleanup is
**typed from the staged entry** — `rd /s /q` for a directory, `del /f /q` for a file —
because `rd` refuses a file and `del` refuses a directory, and a cleanup that quietly does
nothing leaves the whole old `_internal` on disk. And the relaunch is guarded by
`if not exist "%KEYS_EXE%" goto noexe`, because `start` on a path that is not there
raises a **modal shell dialog**: the script would then block forever with the install
stranded aside and nobody at the keyboard to click OK. That guard has its own label and
its own log line rather than sharing `:rollback`'s — every move succeeding and the exe
still not being there is a different fault from a move that failed, and a log that called
it "a move failed" sent the reader looking for the wrong thing.

**No failure path deletes anything.** That single rule is what makes every branch
recoverable by hand, and the branches are worth reading in full:

- **The lock never goes away** — Keys did not close. Nothing has been moved, the install is
  untouched, and the helper leaves **without starting anything**. Not relaunching is the
  point: the app it would start is almost certainly still running, and a second Keys means
  one `keys.db` with two writers and two practice sessions.
- **An entry will not move aside** even though the lock is gone — so it is an indexer or an
  open Explorer window holding it, not us. The asides already done are undone and the whole
  phase is retried on the same counter, because a half-renamed application directory that
  then hits the ceiling *is* the broken install this is trying to avoid — and if it never
  takes, the helper gives up with everything put back.
- **A staged entry will not move in** — every aside entry goes back and the version that
  was already installed is relaunched. Any new entry that did land is taken back out to
  `Keys.new` first, because restoring is guarded on the destination being **empty**
  rather than on the previous move's errorlevel: `move /Y <dir>.old <dir>` with `<dir>`
  still present does not fail, it moves the old tree *inside* the new one, and nothing else
  in the script can undo that nesting.
- **The worst case** is an application directory left holding aside entries and no
  `Keys.exe`, and there are two roads to it. The helper can be killed between the asides
  and the installs — power loss, Task Manager, an antivirus quarantining a freshly-written
  exe — or the restore can itself fail, which is the one of the two the helper sees and
  says so about. **Nothing repairs either on its own**, because the thing that sweeps
  leftovers is the app that no longer starts. The repair is renaming those entries back by
  hand, and it always works precisely because nothing was deleted. Every failure the helper
  *does* see writes a line to `keys-update.log` beside the database.

Two footguns worth writing down, because both are one-argument mistakes that produce no
error anywhere:

- **Paths reach the helper through the environment, never formatted into the script.**
  `%LOCALAPPDATA%` contains the username. A path written into a `.cmd` body breaks three
  separate ways — UTF-8 mangles `José`, `mbcs` cannot encode Japanese at all, and a
  literal `%` is eaten by variable expansion. With the paths in environment variables the
  script body stays pure ASCII whoever runs it. The entry names are the one thing written
  into that body, and `[A-Za-z0-9._-]+` is what keeps them ASCII too: a `%` in a name would
  expand as a variable and an `&`, `^` or `(` would break the lines that report a failed
  rollback. A name outside that pattern stops the update — an install is not the place to
  find out whether the quoting held.
- **The helper must not run with the application directory as its cwd.** A process whose
  cwd is inside a directory pins it — that is the winerror 32 measured at the top of this
  section — so a helper standing in the folder it came to rewrite is holding it open for
  as long as it runs. `Popen` inherits the parent's cwd unless told otherwise, and Keys'
  cwd *is* its own directory. Running from somewhere else is also why the relaunch needs
  `/D`.

### `%LOCALAPPDATA%\Keys` is not in the blast radius

The swap replaces the entries the release ships and nothing else. Your database,
`config.local.json`, your recordings and the presets and SoundFonts you added are not among
them — they are in the data root, on the other side of the split described at the top of
this file. **That split exists for exactly this moment**, and it is the reason
`tools/build_exe.py` fails the build if `keys.db` or `config.local.json` ever end up inside
`dist/Keys`.

Two things can quietly accumulate anyway, and the **Your data** panel accounts for neither.
The staged tree `Keys.new`, ~91 MB, sits beside the application directory: a download that
fails or is cancelled clears it, and one a killed process left behind is swept at the next
startup, before the engine starts — the earliest moment anything is allowed to delete it,
and skipped entirely while a helper is still pending, because sweeping a tree that is
midway through being moved is how you get a partial one installed. **That sweep only ever
touches a tree carrying the `.keys-staged` marker this updater wrote**, and the zip beside
it is keyed off the same marker; a `Keys.new` or `Keys.update.zip` you made yourself is
left alone, by the sweep and by a fresh Download alike — the download refuses to start
rather than overwrite one. The aside entries sit *inside* the application directory, and a
failed swap leaves them there **on purpose**: until the swap succeeds they are the only
working copy of the app, so deleting them is the one act that would turn a recoverable
failure into a permanent one. Since the suffix is unique per update, nothing clears them
afterwards either — that is the price of never colliding with a name you own, and they are
removed by hand. The downloaded zip is on neither list — it is gone the moment extraction
succeeds.

### What is verified about a download, and what is not

**Verified on every download: the length. The hash, only when the release publishes one
this build can read.** The transfer is refused if the byte count does not match the size
the API declared — that one is unconditional. A SHA-256 is taken over everything received
and compared against whatever the release actually publishes, and there are two possible
sources, both optional. GitHub computes a per-asset `digest` itself and it is really there
— `sha256:3ac5feec…b921` for the 55,115,305-byte 0.5.1 asset, and 0.3.0, cut long before
Keys knew to publish a checksum of its own, carries one too. From this build onwards
`tools/build_exe.py` also emits a `.sha256` sidecar to upload beside the zip, and that is
checked as well when it is there. Then the archive's member paths, and the shape of the
extracted tree.

**Where neither is published, nothing is hashed, and the download still installs.** That
is deliberate — refusing would break every release cut before the sidecar existed — but it
used to be silent, and so was the case that matters more: the `sha256:` prefix is matched
literally, so a GitHub API that respelled that field would turn off the only hash today's
releases carry with nothing on screen and nothing in any log. Both cases now write a line
to `keys-update.log` beside the database and are carried in `GET /api/update/status` as
`checksum`, which reads either "sha256 checked against …" or a sentence saying it was not.
No dialog: a release from 2024 legitimately publishes neither, and a warning on every one
of them teaches people to click through warnings.

**Not verified: provenance, and there is no way to read that as better than it is.** The
size and the hash arrive in the **same TLS response as the URL they describe**. What is
proven is therefore that the transfer was not truncated, mangled by a proxy or corrupted
by a CDN — the failures that actually happen — and *nothing whatsoever* about who produced
the bytes. There is no signature and no pinning. **A compromised GitHub account is
arbitrary code execution on every machine that presses Install**, and a checksum published
by whoever published the file cannot change that. It is worth having for exactly what it
catches and must not be dressed up as more.

**Still unsigned, and still ~55 MB a time.** Code signing is not bought — it costs real
money every year — so a first-time browser download still meets SmartScreen; and every
update is the entire zip because nothing here diffs anything. Both are below, with what
they would cost.

One thing that is *not* a problem here: `urllib` does not attach a Mark-of-the-Web and
`zipfile` does not propagate one to extracted files, so an in-app update does not raise
SmartScreen even though the binary is unsigned. That is a property of this path only. The
first install, downloaded with a browser, still gets the warning.

## Still missing, and each of these costs something real

- **Code signing.** Unsigned, SmartScreen shows "Windows protected your PC" to every
  first-time user who downloads the zip in a browser. A certificate costs real money
  annually; the alternative is telling people to click through the warning, which is a
  bad habit to teach.
- **Delta updates.** Every update is the whole ~55 MB, even for a one-line fix. Nothing
  here diffs anything. If that becomes the complaint, Velopack is the answer — see below.
- **An installer.** This is still a zip you extract into a folder of its own. No Start-menu
  entry, no uninstaller, no file associations, no per-machine install.

## Velopack was considered, and the zip-swap was taken instead

[Velopack](https://velopack.io) is `electron-updater` for non-Electron apps. It would
have brought delta updates and a real installer:

```bash
dotnet tool install -g vpk
vpk pack --packId Keys --packVersion 0.5.1 --packDir dist\Keys --mainExe Keys.exe
vpk upload github --repoUrl https://github.com/TerraByte-Dev/Keys --tag v0.5.1
```

Three reasons it lost:

- **The zip-swap works with the distribution that already exists.** Six releases are
  published as plain zips of `dist/Keys`. Velopack wants its own package format and its
  own release feed, which orphans all of them.
- **It needs no .NET tool in the build.** `vpk` is a dotnet global tool; `zipfile`,
  `hashlib` and `urllib` are the standard library. The app has to still build in five
  years on a machine with nothing but Python.
- **Nobody has to re-install.** A Velopack app must be installed by Velopack's installer
  to be updated by it. Every existing user would have to start over.

The argument for waiting was that half an updater that could not be tested against a real
release would be worse than an honest link. **That premise expired** — there are six real
releases, and the mechanic above was measured against the real frozen 0.5.1 build rather
than reasoned about. What looked like an installer's job turned out to be a wait on a lock
file and seventy-odd lines of generated batch.

If delta updates or a real installer become the complaint, Velopack is still the answer,
and the two things to settle first are unchanged: **code signing**, and **driving
`Update.exe` from Python** — the apply-and-restart side has first-class bindings for C#,
Rust and JS, and Python is the one that would need testing rather than guessing.
