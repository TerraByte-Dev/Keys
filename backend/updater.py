"""Installing an update: download, stage beside the app, hand the swap to a helper.

Three deliberate presses, never one button -- `check`, then `download`, then `apply`.
Nothing in here runs at launch, on a timer, or in the background, and none of it starts
without the previous step having been asked for. That is the same stance the check has
always had; see backend/version.py and docs/PACKAGING.md.

**The application directory cannot replace itself while it is running.** Windows holds
the running exe image and every loaded DLL under `_internal` open. So the last step is a
detached .cmd file that outlives this process, swaps the app in place, and starts the
new one.

**It swaps the CONTENTS, never the directory.** The release zip has no wrapper folder,
so "Extract Here" into Downloads makes Downloads the application directory -- and a
helper that renamed that aside and then `rd /s /q`'d it took every unrelated file in it
with it, past the recycle bin. The helper works entry by entry over a list enumerated
from the staged tree at apply time, so the only paths it can reach are the ones this
release ships plus the uniquely-suffixed aside names this run generated for them,
whatever directory the app was extracted into.

Every line of that helper is a measurement rather than a preference:

- **It waits until no lock file is left, not on our PID and not on a failing `move`.**
  The obvious PID wait has no portable spelling here: `tasklist` writes nothing at all
  when the helper has no console and `tasklist | find` hangs forever, while `timeout /t`
  needs a console but dies anyway on the redirected stdin we hand it. Neither survives
  both halves of the flag choice, and the flag is not free to pick -- see the spawn. And
  entry-wise moves are no liveness test: measured here, renaming `_internal` with its
  DLLs loaded and renaming the running `Keys.exe` both SUCCEED -- only the application
  directory itself refuses (winerror 32). `del` on a file this process holds open fails
  with "being used by another process" and leaves the file there, and the handle dies
  with the process, so the file going away *is* the process being gone. `del` reports
  errorlevel 0 either way, which is why `if not exist` is the test.
- **Every instance holds its own `update-lock-<pid>`, not just the one installing.** A
  single lock opened at apply time proved the *applying* Keys had exited and said
  nothing about any other, and there is no single-instance guard anywhere -- keys.py's
  free_port() walks 8770-8789 so a second copy starts happily. Measured: with a second
  Keys running the old build, the swap completed and `rd /s /q` deleted the `_internal`
  it was executing out of. So the lock is per process and opened for the process's whole
  life, and the helper waits for the whole `update-lock-*` pattern to go. `del` clears a
  crashed instance's lock by construction -- nothing holds a handle to it -- so this
  cannot wedge, and a second live Keys simply drives the helper to its ceiling with
  nothing moved, which is the safe outcome.
- **Paths arrive through the environment.** Written into the script body, a data
  directory belonging to "Jose" fails to encode as UTF-8, `chcp 65001` does not rescue
  it, cp1252 cannot hold Japanese at all, and a literal `%` expands as a variable.
  Through the environment every exotic name tested passes. The entry names are the one
  thing generated into the body, and `_SAFE_ENTRY` is what keeps that ASCII.
- **The helper must not run from the application directory.** Inheriting it as a cwd
  locks the very directory it is working in, with nothing logged anywhere. And because
  it therefore runs from somewhere else, the relaunch needs `/D` or the new app silently
  comes up with the wrong cwd.
- **`del "%~f0"` really does delete a running batch file**, and execution stops at that
  line -- cmd reads the script by file offset. So nothing may follow it, which is why
  the relaunch and the old-entry removal both come first. It is also the marker: every
  exit path ends there, so a script still existing and younger than HELPER_TTL means a
  swap is pending. One consequence to know rather than fight: the truncation lands
  before the `exit /b <n>` on the next line is ever read, so **every** path exits 1
  whatever it says. Nothing reads that code -- the helper outlives the process that
  spawned it -- and the distinct `keys-update.log` lines are what tell the paths apart.

A failed update leaves a working app rather than a hole: every move is checked, and
`:restore` puts back exactly the entries that were moved aside.
"""

from __future__ import annotations

import _thread
import hashlib
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from . import config
from .version import REPO, VERSION, check

CHUNK = 65536
TIMEOUT = 30.0

# The zip plus the tree it expands into. 0.5.1 is 55 MB in and 88 MB out, so three
# times the asset size is an honest floor rather than a round number.
SPACE_FACTOR = 3

# The only place bytes may come from. The URL is taken from the GitHub API rather than
# from the browser, and this is the prefix the API returns for a release asset on this
# repo. Redirects are still followed -- the bytes actually live on
# release-assets.githubusercontent.com -- but a URL that did not start here is refused.
ASSET_PREFIX = f"https://github.com/{REPO}/releases/download/"

# Long enough for the HTTP response to reach the browser. Shutting down inside the
# handler means the answer never arrives and the UI shows a network error instead of
# "restarting".
RESTART_DELAY = 0.5

HELPER_NAME = "keys-update.cmd"
LOCK_PREFIX = "update-lock-"
LOG_NAME = "keys-update.log"

# Written into the staged tree the moment that tree is created, and required before
# anything removes either staging path. sweep() runs unprompted at every launch, and a
# `Keys.new` a user extracted themselves beside the app is a plausible thing to find
# there -- rmtree(ignore_errors=True) past the recycle bin is not a thing to do to a
# directory this updater did not write.
STAGE_MARKER = ".keys-staged"

# The one part of the helper that is generated rather than constant, so it is the one
# part that can carry a hostile byte: `%` would expand as a variable and `&`, `^`, `(`
# would break the echo lines that report a failed rollback. What we publish is
# `Keys.exe` and `_internal`; anything else stops the update, because an install is not
# the place to find out whether the quoting held.
_SAFE_ENTRY = re.compile(r"[A-Za-z0-9._-]+")

# PING.EXE by absolute path is the one external tool: it is the only sleep that works
# under every combination of creation flag and stdin tested. Measured: 10 waits took
# 10.41 s, so 150 of them is a ceiling of 156 s -- 2.6 minutes. The real frozen build
# needed one.
_PING = '"%SystemRoot%\\System32\\PING.EXE" -n 2 127.0.0.1 >nul\n'
_RETRIES = 150

# How long a keys-update.cmd can possibly still be doing something. The wait loop above
# tops out at 156 s and the swap after it is renames plus one `rd` of the old _internal.
# Twice the ceiling, because the failure this guards against is the opposite one: a
# helper that died without deleting itself -- a power cut, an AV kill, a spawn that
# never happened -- used to make _pending() answer "a swap is in flight" forever, and no
# press, restart or elapsed time cleared it. A machine paging hard enough to double
# every PING is still inside 300 s.
HELPER_TTL = 300.0


def helper_script(entries: list[tuple[str, bool]], aside_suffix: str) -> str:
    """The .cmd that performs the swap, generated for exactly these top-level entries.

    `entries` is `(name, is_dir)` read from the staged tree at apply time, and it is the
    whole reach of the script: every path it names is `%KEYS_APP%\\<name>`,
    `%KEYS_APP%\\<name>.<aside_suffix>` or `%KEYS_NEW%\\<name>`. **Nothing moves or
    deletes the application directory itself.** The release zip has no wrapper folder,
    so extracting it into Downloads makes Downloads the application directory, and the
    previous `move APP -> APP.old` / `rd /s /q APP.old` deleted every unrelated file in
    it -- no recycle bin, unrecoverable. Enumerating instead of `for %%f in (*)` is what
    keeps that closed: a file Keys did not ship is unreachable by construction.

    `aside_suffix` carries a random component per update for the same reason. With the
    fixed `.old`, a user's own `_internal.old\\receipts\\2024.pdf` beside the app was
    destroyed by the pre-delete that cleared the way for the aside move -- narrow, but
    still a delete of something Keys never shipped. A suffix that cannot collide with a
    name already on disk means nothing has to be cleared first, and the cleanup at the
    end names only the entries this run created.

    What it costs: a release that DROPS a top-level file leaves the old one behind.
    Today the app is exactly `Keys.exe` + `_internal`, so there is no such case, and a
    shipped manifest to catch it would be a second source of truth about the zip.

    The wait is `del` on lock files live instances hold open, not a failing `move`:
    measured on this machine, renaming `_internal` with its DLLs loaded and renaming the
    running `Keys.exe` both succeed, so the entry-wise design does not get its liveness
    test for free the way the directory swap did.
    """
    aside, install, cleanup, restore = [], [], [], []
    for name, is_dir in entries:
        # Guarded on the entry EXISTING, because `move` on a source that is not there
        # sets errorlevel 1 -- measured, and indistinguishable from "something is holding
        # it". A release that ADDS a top-level entry therefore spun the full 156 s
        # ceiling and gave up with nothing installed and nothing relaunched: Keys closed
        # and never came back. A new entry has nothing to move aside.
        aside.append(f'if exist "%KEYS_APP%\\{name}" (\n'
                     f'move /Y "%KEYS_APP%\\{name}" "%KEYS_APP%\\{name}.{aside_suffix}" '
                     f'>nul 2>&1\n'
                     f'if errorlevel 1 goto wait\n'
                     f')\n')
        install.append(f'move /Y "%KEYS_NEW%\\{name}" "%KEYS_APP%\\{name}" >nul 2>&1\n'
                       f'if errorlevel 1 goto rollback\n')
        # Typed from the staged entry: `rd` refuses a file and `del` refuses a
        # directory, and a cleanup that silently does nothing leaves 85 MB behind.
        cleanup.append(f'rd /s /q "%KEYS_APP%\\{name}.{aside_suffix}" >nul 2>&1\n' if is_dir
                       else f'del /f /q "%KEYS_APP%\\{name}.{aside_suffix}" >nul 2>&1\n')
    for name, _is_dir in reversed(entries):
        # Restoring is guarded on the slot being EMPTY rather than on the previous
        # move's errorlevel, because `move /Y <dir>.old <dir>` with <dir> still present
        # does not fail -- it moves the old tree INSIDE the new one, and that nesting is
        # unrecoverable by anything else in this script.
        restore.append(
            f'if exist "%KEYS_APP%\\{name}.{aside_suffix}" (\n'
            f'if exist "%KEYS_APP%\\{name}" (\n'
            f'move /Y "%KEYS_APP%\\{name}" "%KEYS_NEW%\\{name}" >nul 2>&1\n'
            f'if errorlevel 1 >>"%KEYS_LOG%" echo [keys-update] could not take the new '
            f'{name} back out\n'
            f')\n'
            f'if exist "%KEYS_APP%\\{name}" (\n'
            f'>>"%KEYS_LOG%" echo [keys-update] {name} is in the way -- not restoring '
            f'over it\n'
            f') else (\n'
            f'move /Y "%KEYS_APP%\\{name}.{aside_suffix}" "%KEYS_APP%\\{name}" '
            f'>nul 2>&1\n'
            f'if errorlevel 1 >>"%KEYS_LOG%" echo [keys-update] could not restore '
            f'{name}\n'
            f')\n'
            f')\n')
    return (
        "@echo off\n"
        "setlocal\n"
        "set /a n=0\n"
        ":swap\n"
        # The pattern, not one path: every live Keys holds its own update-lock-<pid>, so
        # "no lock left" is the only question that answers "is any instance still
        # running". `del` on the pattern removes the ones nobody holds and leaves the
        # rest, which is what makes a crashed instance's lock self-clearing.
        'del /f /q "%KEYS_LOCKS%" >nul 2>&1\n'
        'if not exist "%KEYS_LOCKS%" goto aside\n'
        "set /a n+=1\n"
        f"if %n% GEQ {_RETRIES} goto giveup\n"
        + _PING +
        "goto swap\n"
        ":aside\n"
        + "".join(aside) +
        "goto install\n"
        # Reached only once the lock is gone, so this is no longer Keys holding the
        # entry -- an indexer or an open Explorer window is. Undo the asides first: a
        # half-renamed app dir that then hits the ceiling is the broken install.
        ":wait\n"
        "call :restore\n"
        "set /a n+=1\n"
        f"if %n% GEQ {_RETRIES} goto giveup\n"
        + _PING +
        "goto aside\n"
        ":install\n"
        + "".join(install) +
        # `start` on a path that is not there raises a MODAL shell dialog, which blocks
        # this script forever -- the cleanup below never runs and the install stays
        # stranded aside. Checking first is what turns that into a rollback, and it gets
        # its own label because every move having succeeded and the exe still not being
        # there is a different fault from a move that failed, and a log that called it
        # "a move failed" sent the reader looking for the wrong thing.
        'if not exist "%KEYS_EXE%" goto noexe\n'
        'start "" /D "%KEYS_APP%" "%KEYS_EXE%"\n'
        + "".join(cleanup) +
        # The marker is the last thing left in there once every entry has moved out, and
        # it is a file this updater wrote -- naming it is not the same as globbing the
        # directory. Without this the `rd` below could never succeed.
        f'del /f /q "%KEYS_NEW%\\{STAGE_MARKER}" >nul 2>&1\n'
        # No /s: every entry has been moved out, so this either is empty and goes, or is
        # not and is left for sweep() rather than recursively deleted.
        'rd "%KEYS_NEW%" >nul 2>&1\n'
        'del "%~f0"\n'
        "exit /b 0\n"
        ":noexe\n"
        '>>"%KEYS_LOG%" echo [keys-update] every entry moved but the new Keys.exe is '
        "not there -- putting the installed version back\n"
        "goto undo\n"
        ":rollback\n"
        '>>"%KEYS_LOG%" echo [keys-update] a move failed -- putting the installed '
        "version back\n"
        ":undo\n"
        "call :restore\n"
        'if not exist "%KEYS_EXE%" >>"%KEYS_LOG%" echo [keys-update] Keys.exe is missing '
        "after the rollback\n"
        'if exist "%KEYS_EXE%" start "" /D "%KEYS_APP%" "%KEYS_EXE%"\n'
        'del "%~f0"\n'
        "exit /b 1\n"
        # Nothing has been moved on either road here: the lock never went away, or an
        # aside kept failing and `:restore` has already undone the rest. So the install
        # is intact and very probably still running, and starting a second one would
        # give one keys.db two writers and two practice sessions.
        ":giveup\n"
        '>>"%KEYS_LOG%" echo [keys-update] gave up waiting -- nothing was moved and '
        "nothing was installed\n"
        'del "%~f0"\n'
        "exit /b 2\n"
        ":restore\n"
        + "".join(restore) +
        "goto :eof\n")


class UpdateError(Exception):
    """Something to tell the user in words they can act on."""


class UpdateBusy(UpdateError):
    """A download is already running."""


class _Cancelled(Exception):
    """The worker was asked to stop. Not an error, so not reported as one."""


def app_dir() -> Path:
    """The directory an update installs INTO -- the one holding Keys.exe, not `_internal`.

    Raises rather than guessing, because in a source checkout every plausible answer is
    wrong and none of them raises on its own: `config.BUNDLE.parent` is whatever folder
    the checkout happens to sit in (here, the one holding every other project), and
    `sys.executable` is the venv's python.exe.

    Both conditions still only prove "this is a directory with Keys.exe and _internal in
    it", which is also true of a Downloads folder the zip was extracted into. That is why
    nothing downstream is allowed to treat this path as a thing it may move or delete --
    see helper_script().
    """
    if not config.FROZEN:
        raise UpdateError("Keys is running from source -- update it with git pull.")
    app = Path(sys.executable).resolve().parent
    if app != config.BUNDLE.parent:
        # --onedir puts _MEIPASS at <app>/_internal, so the two roots bracket the
        # application directory. If that ever stops being true -- --onefile, or
        # PyInstaller's contents_directory -- the honest answer is "I do not know",
        # and the wrong one points at %TEMP%.
        raise UpdateError("unrecognised application layout -- reinstall Keys instead.")
    return app


def _staging(app: Path) -> tuple[Path, Path]:
    """Where an update is assembled: siblings of the application directory.

    Siblings rather than somewhere under %LOCALAPPDATA% because the final step is a
    `move`, and a move across volumes is a copy that fails with winerror 17. Never
    inside the application directory -- that is the thing being written into.

    There is deliberately no `<app>.old` here any more. The incumbent now goes aside
    entry by entry *inside* the application directory: a sibling of an app extracted
    straight into Downloads is `Downloads.old`, and sweep() rmtree-ing that on the next
    launch is the same catastrophe through a different door.
    """
    parent = app.parent
    return (parent / f"{app.name}.update.zip",
            parent / f"{app.name}.new")


def _pending() -> Path | None:
    """The helper apply() wrote and the shell has not finished, if there is one.

    Every exit path in the generated script ends `del "%~f0"`, so the file still being
    there means a swap is about to happen or is in flight. sweep(), start() and apply()
    all have to see it: `rmtree(ignore_errors=True)` across a tree a helper is midway
    through moving leaves a PARTIAL tree, and the install ends up stranded aside.

    The age check is the other half, and it is what stops "the file exists" from being a
    permanent answer. A helper that never started, or one killed by a power cut during
    the ~156 s window, leaves a script nothing in this program ever deleted -- and every
    later launch then refused Download with "Keys is installing an update and is about
    to restart" and Install with "an update is already installing", both untrue, with no
    press or restart able to clear either. Past HELPER_TTL the shell cannot still be
    running, so the file is swept here rather than believed.

    The margin is what keeps that sweep safe, and it is worth knowing why rather than
    trusting it: deleting a running .cmd succeeds, and the shell stops **on the spot** --
    measured, mid-script. So a helper that somehow outlived HELPER_TTL would be killed
    wherever it stood, possibly between the asides and the installs. It is unreachable
    today (the ceiling is a measured 153 s against a 300 s TTL, and the swap after it is
    renames), which is the only reason this is a comment and not a handshake.
    """
    script = config.DATA_DIR / HELPER_NAME
    try:
        age = time.time() - script.stat().st_mtime
    except OSError:
        return None
    if age > HELPER_TTL:
        _remove(script)
        return None
    return script


def staged_entries(new: Path) -> list[tuple[str, bool]]:
    """The top-level `(name, is_dir)` the helper may touch, re-stat'd from the tree.

    Read here rather than remembered from the download, because the staged tree can be
    hollowed out between the two: server.py sweeps at startup, so a second instance
    removes a tree this one still reports as `staged`, and pressing Download again would
    `_remove` the very tree a pending helper is about to move.
    """
    try:
        # The marker is ours, not the release's -- handing it to the helper would move a
        # file into the application directory that no version of Keys ever shipped.
        names = sorted(p.name for p in new.iterdir() if p.name != STAGE_MARKER)
    except OSError as exc:
        raise UpdateError("the staged update is no longer on disk -- "
                          "download it again.") from exc
    if "Keys.exe" not in names or "_internal" not in names:
        raise UpdateError("the staged update is not a Keys application directory any "
                          "more -- download it again.")
    entries: list[tuple[str, bool]] = []
    for name in names:
        if not _SAFE_ENTRY.fullmatch(name):
            raise UpdateError("the staged update contains a name the installer cannot "
                              f"handle: {name}")
        entries.append((name, (new / name).is_dir()))
    return entries


def _remove(path: Path) -> None:
    """Best effort. A leftover we cannot delete must not stop the next attempt."""
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    except OSError:
        pass


def _clear_staging(archive: Path, new: Path) -> None:
    """Remove what a previous attempt left beside the app -- and nothing else.

    Gated on the marker inside the staged tree, because the two staging paths are named
    after the application directory and a user is free to have made either. Measured: a
    `Keys.new` someone extracted themselves next to the app was rmtree'd past the recycle
    bin at the next launch, before the engine even started, on no press at all.

    The zip is keyed off the tree's marker rather than one of its own: _work() creates
    the marked tree before it opens the socket, so a download killed halfway leaves both,
    and a `Keys.update.zip` sitting there with no marked tree beside it is not ours.
    """
    # An empty directory counts as ours too, and that is a repair rather than a
    # loosening. The helper deletes the marker and then tries `rd`; if that `rd` loses --
    # an Explorer window or a scanner holding the directory, which is the whole reason it
    # carries no /s -- the tree is left empty and UNMARKED, and gating on the marker
    # alone then refused it forever. A successful install would permanently disable the
    # updater and blame the user for a folder Keys made. Nothing of anyone's is lost by
    # removing an empty directory.
    if not (new / STAGE_MARKER).is_file() and any(new.iterdir() if new.is_dir() else [1]):
        return
    _remove(archive)
    _remove(new)


def _note(text: str) -> None:
    """Append one line to keys-update.log, the file the helper already writes to.

    Keys has no logger and a windowed build has no console, so without this a download
    that verified no hash at all looks exactly like one that verified two.
    """
    try:
        with open(config.DATA_DIR / LOG_NAME, "a", encoding="utf-8") as fh:
            fh.write(f"[keys-update] {text}\n")
    except OSError:
        pass


def preflight(app: Path, need: int) -> None:
    """Prove we could install, before spending 55 MB proving we could not.

    Three separate rights, exercised against the real filesystem rather than inferred
    from the path or from whether we are elevated: create a directory next to the app,
    write in it, rename it, remove it. Program Files unelevated fails at the first one
    with winerror 5, which is the case this exists for.
    """
    parent = app.parent
    # Per-caller, because two browser tabs pressing Download at the same moment both
    # land here before either is refused as busy, and a shared probe path would make
    # one of them fail for a reason that has nothing to do with the update.
    tag = threading.get_ident()
    probe = parent / f".{app.name}-probe-{tag}"
    moved = parent / f".{app.name}-probe-{tag}-moved"
    _remove(probe)
    _remove(moved)
    try:
        probe.mkdir()
        (probe / "w").write_bytes(b"")
        probe.rename(moved)
    except OSError as exc:
        raise UpdateError(
            f"Keys cannot install an update into {parent} ({exc.strerror or exc}). "
            "Move Keys somewhere you own, or reinstall it from the website."
        ) from exc
    finally:
        _remove(probe)
        _remove(moved)

    free = shutil.disk_usage(parent).free
    if free < need:
        raise UpdateError(f"not enough room on {parent.drive or parent}: "
                          f"{need >> 20} MB needed, {free >> 20} MB free.")


def _fetch_sha256(url: str) -> str:
    """The hex digest out of a `<hex>  <filename>` sidecar. Tiny; read whole."""
    req = urllib.request.Request(url, headers={"User-Agent": f"Keys/{VERSION}"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
        text = res.read(4096).decode("ascii", "replace")
    token = (text.split() or [""])[0].lower()
    if len(token) != 64 or token.strip("0123456789abcdef"):
        raise UpdateError("the published checksum file is not a sha256.")
    return token


def _verify_checksum(digest: str, info: dict[str, Any]) -> str:
    """Compare against whatever the release actually publishes; say what that was.

    Two independent sources, both optional. GitHub computes `digest` itself, so it is
    present on releases cut long before Keys knew to publish anything. A `.sha256`
    sidecar beside the zip is what tools/build_exe.py emits for new releases. Neither
    is a signature: both arrive in the same TLS response as the URL, so what they prove
    is that the transfer was not truncated, mangled by a proxy, or corrupted by a CDN --
    which are the failures that actually happen. Where a release publishes neither, the
    declared-size check is what stands, and that is worth saying out loud rather than
    warning about on every existing release.

    Which is why this returns a sentence instead of nothing. The `sha256:` prefix is
    matched literally, so a GitHub API that respells that field would turn the only hash
    today's releases carry off with nothing on screen and nothing in any log -- measured:
    a digest of `sha-256:...` staged the download exactly like a verified one. The
    no-hash outcome is now written to keys-update.log and carried in status(), because
    the one thing it must not be is invisible.
    """
    expected: list[str] = []
    sources: list[str] = []
    unread = ""
    api = str(info.get("download_digest") or "")
    if api.startswith("sha256:"):
        expected.append(api[len("sha256:"):].lower())
        sources.append("GitHub's own digest")
    elif api:
        unread = f" GitHub published a digest this build cannot read: {api[:24]}."
    sidecar = str(info.get("download_sha256_url") or "")
    if sidecar.startswith(ASSET_PREFIX):
        expected.append(_fetch_sha256(sidecar))
        sources.append("the published .sha256")
    if any(want != digest for want in expected):
        raise UpdateError("the download does not match its published checksum.")
    if sources:
        return "sha256 checked against " + " and ".join(sources)
    note = ("this release publishes no checksum -- only the declared byte count was "
            "checked." + unread)
    _note(note)
    return note


def _extract(archive: Path, dest: Path) -> None:
    """Unpack, refusing anything that is not the shape we publish.

    `extractall` already *contains* zip-slip in 3.11 -- `../escape.txt` lands at the
    root rather than outside it, and an absolute member does not escape either -- but it
    contains it by silently REWRITING the path and reporting success. An archive that
    has to be rearranged to fit is not the archive we built, so this refuses rather than
    installing something rearranged.

    `dest` is the extraction target, full stop: the release zip carries Keys.exe and
    `_internal` at its own root with no wrapping folder. Descending into a subfolder
    would ship an updater that never works, and the assertion at the end is what catches
    the zip's shape changing under us.
    """
    root = dest.resolve()
    with zipfile.ZipFile(archive) as zf:
        for name in zf.namelist():
            target = (root / name).resolve()
            if target != root and root not in target.parents:
                raise UpdateError(f"the update archive contains an unsafe path: {name}")
        zf.extractall(root)
    if not (dest / "Keys.exe").is_file() or not (dest / "_internal").is_dir():
        raise UpdateError("the update archive is not a Keys application directory.")


def request_exit() -> None:
    """Unblock keys.py's main thread so its teardown actually runs.

    Not `os._exit`: App.shutdown() ends the practice session, which is a database write.
    Hard-exiting to apply an update would silently discard the session in progress --
    precisely the data loss the bundle/data split exists to prevent.

    The two launch shapes block on different things. Destroying the window returns
    `webview.start()` and main() falls through to its `finally`; with no window the main
    thread is inside `thread.join(0.5)`, where a KeyboardInterrupt lands within half a
    second and is already caught.

    **Neither route may be the only one.** keys.py calls `create_window()` -- which
    appends to `webview.windows` -- before `start()`, so when `start()` raises (the
    documented WebView2-runtime-missing fallback) there is a window object with no
    `gui`. pywebview 6.2.1's `Window.destroy()` is `@_shown_call`: it waits 20 s on an
    event nothing will ever set and then raises `WebViewException`. Measured on apply()'s
    own timer: 20.1 s, then the raise, `interrupt_main()` never reached, Keys never
    exits -- and in a windowed build the traceback goes to keys.py's null stderr, so
    nothing says so. Hence the `gui` check, the swallow, and the unconditional fall
    through to interrupt_main().
    """
    windows = []
    try:
        import webview
        windows = list(webview.windows)
    except Exception:  # noqa: BLE001 -- pywebview is optional and may not have started
        pass
    for window in windows:
        if getattr(window, "gui", None) is None:
            continue
        try:
            window.destroy()
        except Exception:  # noqa: BLE001, PERF203 -- an exit must not depend on the UI
            pass
    _thread.interrupt_main()


class Updater:
    """Downloads a release, stages it beside the application, hands over the swap.

    The worker is a daemon thread for the same reason the metronome's refill is: a
    55 MB download and a 0.8 s extraction on the asyncio loop would freeze the note
    display for the duration, and drain_loop() owes the UI a frame every 16 ms.

    Six states -- idle, downloading, cancelling, staged, applying, error. The unpack
    deliberately does not get one of its own: it is 0.8 s against a transfer of tens of
    seconds, so that would be a state every reader has to handle and almost nobody would
    ever see. `applying` earns its place -- without it apply() left the state on
    `staged`, so reopening About offered Download again and the worker deleted the tree a
    live helper was about to move. `cancelling` earns its place because the worker cannot
    publish anything until the in-flight `res.read()` returns, and TIMEOUT is 30 s: for
    that long the panel went on saying "Downloading" over a transfer already told to stop.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._state = "idle"
        self._received = 0
        self._total = 0
        self._error = ""
        self._checksum = ""
        self._staged: Path | None = None
        self._applying_at = 0.0
        # Held open for the rest of the process's life so the helper's `del` on it fails
        # until we are actually gone. Every instance opens one, not only the one that
        # presses Install -- see helper_script() and _open_lock().
        self._liveness: Any = None
        self._open_lock()

    def _open_lock(self) -> None:
        """Announce this process to any helper, for as long as this process lives.

        Only when frozen: the lock lives in the data directory, which in a source
        checkout is the repository, and a `update-lock-<pid>` appearing in the working
        tree on every `python keys.py` is litter for a swap that can never target it.

        The stale sweep is the same construction the helper uses -- unlink fails on a
        file a live instance holds open, so this can only ever remove a lock nobody is
        holding. Without it the data directory collects one file per crashed run for
        ever, since a lock is deliberately never closed.
        """
        if not config.FROZEN:
            return
        for old in config.DATA_DIR.glob(f"{LOCK_PREFIX}*"):
            try:
                old.unlink()
            except OSError:
                pass
        try:
            self._liveness = open(config.DATA_DIR / f"{LOCK_PREFIX}{os.getpid()}",
                                  "wb")  # noqa: SIM115 -- the handle IS the signal
            self._liveness.write(b"1")
            self._liveness.flush()
        except OSError:
            self._liveness = None

    # ---------------------------------------------------------------- status
    def status(self) -> dict[str, Any]:
        with self._lock:
            return {"state": self._state, "received": self._received,
                    "total": self._total, "error": self._error,
                    "checksum": self._checksum,
                    "staged": self._staged is not None}

    def _expire_applying(self) -> None:
        """`applying` may not outlive the helper it is waiting for. Lock held.

        Nothing but the process dying used to clear this state, so an apply whose helper
        gave up -- or whose request_exit never landed -- left Download and Install both
        refused for the rest of the session with messages that were no longer true.
        """
        if (self._state == "applying"
                and time.monotonic() - self._applying_at > HELPER_TTL):
            self._state = "idle"
            self._staged = None
            self._received = self._total = 0

    def sweep(self) -> None:
        """Delete whatever a previous attempt left beside the application directory.

        Called once at startup. A download that fails or is cancelled cleans up after
        itself; a process killed mid-download cannot, so the next start that gets this
        far does it. Nothing to do in a source checkout, which has no application
        directory.

        The aside entries *inside* the application directory are deliberately not swept
        here. The helper removes its own, and a startup that went looking for them could
        only do it by pattern-matching the contents of a directory the user may have
        extracted Keys straight into. A failed swap leaves them on purpose: until the
        swap succeeds they are the only working copy of the app.
        """
        try:
            app = app_dir()
        except UpdateError:
            return
        if _pending() is not None:
            # A second instance sweeping a tree a pending helper is about to move is how
            # an install ends up with an empty application directory and the real app
            # stranded aside: rmtree(ignore_errors=True) over a partly-locked tree
            # leaves a PARTIAL one, and the helper then moves that into place.
            return
        _clear_staging(*_staging(app))

    # ------------------------------------------------------------- downloading
    def start(self) -> dict[str, Any]:
        """Check, pre-flight, then begin. Returns at once; watch `status()`."""
        app = app_dir()
        with self._lock:
            self._expire_applying()
            applying = self._state == "applying"
        if applying or _pending() is not None:
            # `_work` starts by clearing the staged tree, which is the tree a helper
            # written by apply() is about to move. Refusing here is the only thing
            # between the two, because the About panel is happy to offer Download again.
            raise UpdateBusy("Keys is installing an update and is about to restart.")
        with self._lock:
            staged = self._state == "staged"
        if staged:
            # Same reasoning one state earlier. `staged` means 88 MB is already on disk
            # and ready, and _work would clear it and pull the 55 MB again -- so pressing
            # Check for updates after a finished download and taking the Download button
            # it offers re-downloaded the release, as many times as you liked. Cancel is
            # the way back out; it removes the staged tree and returns to idle.
            raise UpdateBusy(
                "That update is already downloaded and ready to install.")
        info = check()
        if info["error"]:
            raise UpdateError(info["error"])
        if not info["newer"]:
            raise UpdateError(f"Keys {VERSION} is already the newest release.")
        url = str(info.get("download") or "")
        size = int(info.get("download_size") or 0)
        if not url.startswith(ASSET_PREFIX) or size <= 0:
            raise UpdateError(f"release {info['latest']} has no Windows download.")
        preflight(app, size * SPACE_FACTOR)

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise UpdateBusy("an update is already downloading.")
            self._stop.clear()
            self._state = "downloading"
            self._received = 0
            self._total = size
            self._error = ""
            self._checksum = ""
            self._staged = None
            self._thread = threading.Thread(target=self._work, args=(app, info),
                                            name="keys-update", daemon=True)
            self._thread.start()
        return self.status()

    def cancel(self) -> dict[str, Any]:
        """Stop a download in flight, or throw away one that already finished.

        The staged tree is another 88 MB sitting next to the application; without this
        the only ways out of "staged" are installing it and restarting Keys.
        """
        with self._lock:
            self._expire_applying()
            applying = self._state == "applying"
        if applying:
            # The helper owns this tree now. Removing it here strands the install aside
            # exactly the way a second instance's sweep() did.
            return self.status()
        self._stop.set()
        with self._lock:
            staged, self._staged = self._staged, None
            if self._state == "downloading":
                # Published here rather than left to the worker: the worker cannot reach
                # its next checkpoint until the in-flight res.read() returns, and TIMEOUT
                # is 30 s. For that long the button was disabled and the bar went on
                # claiming to download something already told to stop.
                self._state = "cancelling"
            elif self._state in ("staged", "error"):
                self._state = "idle"
                self._received = self._total = 0
                self._error = ""
        if staged is not None and (staged / STAGE_MARKER).is_file():
            # Marker-gated like every other removal. Without it this was the one way back
            # to the failure the marker exists to prevent: another instance's launch
            # sweeps our tree, the user makes a `Keys.new` of their own in the gap, and
            # Cancel in a panel still holding the old path rmtrees it past the recycle
            # bin. Narrower than the original, same ending.
            _remove(staged)
        return self.status()

    def _checkpoint(self) -> None:
        """Cancel is only honoured where it is asked about.

        The chunk loop used to be the only reader of the flag, so a Cancel pressed in
        the last second of a download did nothing at all: the `.sha256` fetch and the
        ~0.8 s extraction both ran anyway and the worker then published `staged` -- the
        panel offering "Restart and install" over 88 MB you had just asked to be rid of.
        """
        if self._stop.is_set():
            raise _Cancelled()

    def _work(self, app: Path, info: dict[str, Any]) -> None:
        archive, new = _staging(app)
        try:
            _clear_staging(archive, new)
            for path in (archive, new):
                if path.exists():
                    # Whatever this is, this updater did not put it there -- the marker
                    # says so -- and overwriting a zip or rmtree-ing a directory someone
                    # named themselves is the same destruction sweep() used to do, just
                    # behind a press.
                    raise UpdateError(
                        f"{path.name} is already beside Keys and this updater did not "
                        "put it there -- move or rename it, then download again.")
            # Marked before a byte is fetched, so a download killed halfway still leaves
            # staging that identifies itself and the next launch can clear both paths.
            new.mkdir(parents=True)
            (new / STAGE_MARKER).write_bytes(b"")
            digest = self._download(str(info["download"]), archive,
                                    int(info["download_size"]))
            self._checkpoint()      # the sidecar fetch is another network round trip
            checksum = _verify_checksum(digest, info)
            self._checkpoint()      # and the unpack is ~0.8 s of ignoring you
            _extract(archive, new)
            self._checkpoint()      # publishing `staged` after a Cancel IS the defect
            # The tree is the artefact now; keeping the zip would double the footprint
            # of a staged update for the rest of its life.
            _remove(archive)
            with self._lock:
                self._state = "staged"
                self._staged = new
                self._checksum = checksum
        except _Cancelled:
            _clear_staging(archive, new)
            with self._lock:
                self._state = "idle"
                self._received = self._total = 0
        except Exception as exc:  # noqa: BLE001 -- a worker must never take the app down
            # Marker-gated like every other removal, and it has to be: the refusal above
            # is raised precisely when these paths belong to someone else, and a bare
            # cleanup here would delete exactly what it just declined to overwrite.
            _clear_staging(archive, new)
            with self._lock:
                self._state = "error"
                self._error = str(exc)
                self._staged = None

    def _download(self, url: str, dest: Path, size: int) -> str:
        """Stream the asset to disk; return its sha256.

        Chunked rather than one `read()`, because the whole point of the worker is that
        it can say where it got to and can be stopped part way through.
        """
        sha = hashlib.sha256()
        got = 0
        req = urllib.request.Request(url, headers={"User-Agent": f"Keys/{VERSION}"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
            if urllib.parse.urlparse(res.url).scheme != "https":
                # urllib follows a redirect to plain http without complaint.
                raise UpdateError("the download was redirected off https.")
            with open(dest, "wb") as fh:
                while True:
                    if self._stop.is_set():
                        raise _Cancelled()
                    chunk = res.read(CHUNK)
                    if not chunk:
                        break
                    fh.write(chunk)
                    sha.update(chunk)
                    got += len(chunk)
                    with self._lock:
                        self._received = got
        if got != size:
            raise UpdateError(f"the download is {got} bytes and GitHub said {size} -- "
                              "the transfer was cut short.")
        return sha.hexdigest()

    # ---------------------------------------------------------------- applying
    def apply(self) -> dict[str, Any]:
        """Write the helper, launch it hidden, and start shutting down."""
        app = app_dir()
        _archive, new = _staging(app)
        if _pending() is not None:
            # Two helpers race on the same moves and the loser retries to its ceiling.
            raise UpdateError("an update is already installing -- if Keys does not "
                              "restart within a few minutes, close and reopen it.")
        if self._liveness is None:
            # The helper's entire liveness test is `del` failing against a handle this
            # process holds. With no handle it finds no lock, swaps immediately, and
            # deletes the `_internal` this process is executing out of.
            raise UpdateError(f"Keys could not open its update lock in {config.DATA_DIR}"
                              " -- it cannot install safely. Reopen Keys and try again.")
        # Read before anything is written, because everything below leaves a trace: a
        # missing SystemRoot used to raise KeyError straight out of the route as a raw
        # 500, with the helper already on disk and _pending() then answering "a swap is
        # in flight" for ever.
        system_root = os.environ.get("SystemRoot")
        if not system_root:
            raise UpdateError("SystemRoot is not set, so Keys cannot find cmd.exe to "
                              "run the installer.")
        with self._lock:
            if self._state != "staged" or self._staged is None:
                raise UpdateError("there is no staged update to install.")
            # One way and under the lock: this is what makes a second press find no
            # `staged` to act on, and it is what start() and cancel() read.
            self._state = "applying"
            self._applying_at = time.monotonic()
        try:
            entries = staged_entries(new)
        except UpdateError as exc:
            with self._lock:
                self._state = "error"
                self._error = str(exc)
                self._staged = None
            raise

        # Random per update, and nothing is pre-deleted to make room for it. The fixed
        # `.old` collided with names in the application directory that Keys never
        # shipped -- a user's own `_internal.old\\receipts\\2024.pdf` and `Keys.exe.old`
        # were both destroyed clearing the way -- and a delete is the one act that turns
        # a recoverable failure into a permanent one.
        suffix = f"old-{os.urandom(4).hex()}"
        script = config.DATA_DIR / HELPER_NAME
        try:
            # The write is inside the guard with the spawn, because a half-written script
            # is a file on disk too, and a file on disk is what _pending() answers with.
            script.write_text(helper_script(entries, suffix), encoding="ascii",
                              newline="\r\n")
            subprocess.Popen(
                # Doubled quotes: cmd /c mangles a script path containing & ^ or (
                # without them. The paths themselves go in the environment, never here.
                f'{system_root}\\System32\\cmd.exe /c ""{script}""',
                # Load-bearing, and it looks redundant: Popen inherits our cwd, which IS
                # the application directory, and a helper standing in the directory it is
                # moving entries around in locks it against itself, logging nothing.
                cwd=str(config.DATA_DIR),
                env={**os.environ,
                     "KEYS_APP": str(app), "KEYS_NEW": str(new),
                     "KEYS_EXE": str(app / "Keys.exe"),
                     "KEYS_LOCKS": str(config.DATA_DIR / f"{LOCK_PREFIX}*"),
                     "KEYS_LOG": str(config.DATA_DIR / LOG_NAME)},
                # Not for survival -- every flag combination tested outlives the parent.
                # NO_WINDOW rather than DETACHED, and the difference is the whole point.
                # DETACHED gives cmd no console at all, so PING.EXE -- the sleep in the
                # wait loop -- has to allocate its OWN, and a console window pops for
                # every retry. Someone watching their machine do that unprompted after
                # pressing Install reasonably concludes they have been compromised; it
                # happened here. NO_WINDOW gives cmd a console that is never shown and
                # PING inherits it. Measured: the real script runs end to end, both
                # sleeps land 1.04 s apart, and zero visible windows belong to it or any
                # descendant.
                creationflags=(subprocess.CREATE_NO_WINDOW
                               | subprocess.CREATE_NEW_PROCESS_GROUP),
                close_fds=True, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            # cmd.exe blocked by policy or by AV, or ERROR_ACCESS_DENIED on a managed
            # machine. Not an UpdateError on its own, so this used to leave the route
            # answering a raw 500 with the helper still on disk -- and a helper on disk
            # is what _pending() reads as "a swap is in flight", which disabled the
            # updater from then on. Nothing has been moved, so the staged tree is still
            # installable and `staged` is the truthful state to go back to.
            script.unlink(missing_ok=True)
            with self._lock:
                self._state = "staged"
            raise UpdateError(
                f"Keys could not start the installer ({exc.strerror or exc}). Nothing "
                "has been changed and the download is still ready to install."
            ) from exc

        threading.Timer(RESTART_DELAY, request_exit).start()
        return {"ok": True, "restarting": True}
