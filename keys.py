"""Keys. Plug in the piano, run this, play.

    .venv\\Scripts\\python.exe keys.py

Opens http://127.0.0.1:8770 in your browser. Ctrl+C to stop.

Nothing about the audio path changed from `backend/play.py` -- same WASAPI exclusive
mode, same measured 144-sample buffer, same 3.00 ms. The browser is a display attached
to the side of that; audio never passes through Python, and the MIDI callback never
touches the socket.
"""

from __future__ import annotations

import argparse
import ctypes
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

# Importing the package is what sets sys.setswitchinterval(0.0008) and puts FluidSynth
# on PATH. It has to happen before anything imports fluidsynth, which is why it is here
# and first. See backend/__init__.py.
import backend  # noqa: F401
from backend import config, version

DEFAULT_PORT = 8770


def raise_timer_resolution() -> bool:
    """Ask Windows for a 1 ms scheduler tick.

    The default is ~15.6 ms, which quantises every asyncio.sleep in the drain loop and
    adds avoidable jitter to thread wakeups. Since Windows 10 2004 this only affects the
    calling process, so it is a cheap, local change. Released again on exit.
    """
    try:
        return ctypes.WinDLL("winmm").timeBeginPeriod(1) == 0
    except Exception:  # noqa: BLE001
        return False


def drop_timer_resolution() -> None:
    try:
        ctypes.WinDLL("winmm").timeEndPeriod(1)
    except Exception:  # noqa: BLE001
        pass


def free_port(host: str, port: int) -> int:
    for candidate in range(port, port + 20):
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((host, candidate))
                return candidate
            except OSError:
                continue
    return port


class _Null:
    """Stand-in for a stream that is not there. Swallows everything."""

    def write(self, _s: str) -> int:
        return 0

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False


def attach_output(dev: bool) -> None:
    """Make print() safe, and give a windowed build somewhere to log.

    A GUI build has no console: PyInstaller leaves ``sys.stdout`` as None, and the
    first ``print`` in the startup banner would take the whole app down with an
    AttributeError before the window ever appeared. So: a null sink by default, and
    under --dev a real file, because a packaged app still has to be debuggable and
    "run it from a terminal" is not available when there is no terminal.
    """
    if sys.stdout is not None and sys.stderr is not None and not dev:
        return
    sink = None
    if dev:
        try:
            config.DATA_DIR.mkdir(parents=True, exist_ok=True)
            sink = open(config.DATA_DIR / "keys-dev.log", "a", encoding="utf-8",
                        buffering=1)
            sink.write(f"\n{'=' * 70}\nKeys {version.VERSION} starting\n")
        except OSError:
            sink = None
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, sink or _Null())
    if sink is not None and sys.stdout is not sink:
        # There IS a console (source checkout) and --dev was asked for: mirror to the
        # file as well, so the two launch modes produce the same artefact.
        class _Tee:
            def __init__(self, *streams):
                self._streams = streams

            def write(self, s: str) -> int:
                for st in self._streams:
                    try:
                        st.write(s)
                    except Exception:  # noqa: BLE001, PERF203
                        pass
                return len(s)

            def flush(self) -> None:
                for st in self._streams:
                    try:
                        st.flush()
                    except Exception:  # noqa: BLE001, PERF203
                        pass

            def isatty(self) -> bool:
                return False

        sys.stdout = _Tee(sys.stdout, sink)


def wait_for_port(host: str, port: int, timeout: float = 30.0) -> bool:
    """Block until the server answers. Returns False if it never does."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as s:
            s.settimeout(0.25)
            if s.connect_ex((host, port)) == 0:
                return True
        time.sleep(0.1)
    return False


def open_when_ready(url: str, host: str, port: int) -> None:
    if wait_for_port(host, port, 20.0):
        webbrowser.open(url)


def window_icon() -> Path | None:
    """The .ico for the window and the taskbar, wherever it happens to live.

    config.BUNDLE is the checkout root in a source tree and the bundle when frozen,
    so one path covers both. Returns None rather than raising: a missing icon is
    cosmetic, and refusing to open the window over one would make it fatal.
    """
    candidate = config.BUNDLE / "packaging" / "keys.ico"
    return candidate if candidate.exists() else None


def run_window(url: str, dev: bool) -> bool:
    """Open Keys in a native window. Returns False if a window was not possible.

    **Order is the whole point.** The server -- and therefore FluidSynth, and therefore
    the exclusive-mode WASAPI device -- is already up before this is called. A browser
    engine that initialises first can take the audio endpoint, and exclusive mode is
    first-come: Keys would come up in shared mode and quietly cost you 7 ms.

    This runs on the main thread because WebView2's message loop has to, which is why
    uvicorn is the one on a background thread rather than the other way round.
    """
    try:
        import webview
    except ImportError:
        print("  pywebview is not installed -- falling back to your browser.")
        print("  .venv\\Scripts\\pip install pywebview")
        return False

    try:
        webview.create_window(
            "Keys", url,
            width=1500, height=1000, min_size=(1024, 680),
            background_color="#08090a",   # --panel-0, so there is no white flash
            # text_select stays default. Turning it off suits an instrument panel right
            # up until you try to rename a layer or edit a track's notes.
        )
        # debug=True gives WebView2's devtools on F12, which is the browser half of
        # --dev: console, network, and the element inspector for the keyboard SVG.
        #
        # The icon has to be passed here even though the frozen exe already carries one
        # in its resources: that embedded icon is what Explorer and the shortcut use,
        # while the WINDOW asks its process for one at runtime. From a source checkout
        # that process is python.exe, so without this the taskbar shows Python's logo.
        icon = window_icon()
        webview.start(debug=dev, **({"icon": str(icon)} if icon else {}))
        return True
    except Exception as exc:  # noqa: BLE001 -- a missing runtime must not be fatal
        print(f"  Could not open the app window ({exc}).")
        print("  Falling back to your browser. Install the WebView2 runtime to fix:")
        print("  https://developer.microsoft.com/microsoft-edge/webview2/")
        return False


def dev_monitor(interval: float = 2.0) -> None:
    """Print what the app is actually doing, every couple of seconds.

    There is no `npm run dev` here -- the frontend has no build step and no
    package.json on purpose, so a file save plus a browser refresh IS the dev loop.
    What a dev mode can usefully add is the state you cannot see from the UI: whether
    the piano is present, how long the MIDI callback is taking, and whether the queue
    between the callback thread and everything else is shedding frames.

    Deliberately NOT auto-reload. uvicorn --reload would tear down and rebuild the
    Synth on every save, which means dropping and re-acquiring an exclusive-mode WASAPI
    device -- a second of silence, and a race with whatever is holding the port.
    """
    from backend import music
    from backend.server import app_state

    last_total = 0
    last_midi = None
    while True:
        time.sleep(interval)
        try:
            hub = app_state.hub.stats()
            lat = hub.get("latency", {})
            eng = app_state.engine
            midi = app_state.midi

            if midi.connected != last_midi:
                last_midi = midi.connected
                print(f"  [dev] MIDI {'CONNECTED: ' + (midi.pinned or 'all inputs') if midi.connected else 'not connected -- is the piano on?'}",
                      flush=True)

            rate = (hub["events_total"] - last_total) / interval
            last_total = hub["events_total"]
            held = sorted(app_state.held)
            names = " ".join(music.note_name(n, app_state.reading_key()) for n in held[:10])

            bits = [
                # On every line, not only on transitions. When a take does not record
                # or a key does nothing, "is the piano even there" is the first question
                # and it should never require scrolling back to answer.
                "midi ok " if midi.connected else "NO MIDI ",
                f"ev/s {rate:5.1f}",
                f"q {hub['queue_depth']:>3}/{hub['queue_limit']}",
                f"drop {hub['dropped']}",
                f"voices {eng.voice_count():>3}",
            ]
            if lat.get("n"):
                bits.append(f"cb {lat['median_us']:>6.1f}us p95 {lat['p95_us']:>7.1f}us")
            if app_state.metro.status()["running"]:
                bits.append("click")
            loop = app_state.loop.status()
            if loop["state"] != "stopped":
                bits.append(f"loop {loop['state']} {loop['cycle']}")
            print(f"  [dev] {' | '.join(bits)}" + (f"  held: {names}" if held else ""),
                  flush=True)
        except Exception as exc:  # noqa: BLE001 -- a monitor must never take the app down
            print(f"  [dev] monitor: {exc}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Keys -- MIDI piano practice app")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--browser", action="store_true",
                        help="open in your default browser instead of the app window")
    parser.add_argument("--no-browser", "--headless", dest="no_browser",
                        action="store_true",
                        help="server only -- open no window and no browser")
    parser.add_argument("--dev", action="store_true",
                        help="verbose logging, resolved paths, a live 2 s status line, "
                             "and devtools on F12 in the app window")
    # Not for humans. tools/build_exe.py asks the frozen app whether its window backend
    # actually got bundled, because pywebview picks the backend by platform string at
    # runtime and a missing one degrades to a browser tab instead of failing.
    parser.add_argument("--print-window-backend", action="store_true",
                        help=argparse.SUPPRESS)
    args = parser.parse_args()

    # Before the first print. A windowed build has no stdout, and the banner below
    # would be the last thing this process ever did.
    attach_output(args.dev)

    if args.print_window_backend:
        try:
            # NOT `as backend`: that binds a local of that name for the whole function
            # and shadows the module-level `import backend`, so the --dev banner's
            # backend.FLUIDSYNTH_BIN becomes an UnboundLocalError several branches away.
            import webview.platforms.winforms as winforms
            print(f"window: winforms/{getattr(winforms, 'renderer', 'unknown')}")
        except Exception as exc:  # noqa: BLE001
            print(f"unavailable: {exc}")
        return 0

    if config.find_asset("soundfonts", config.DEFAULT_SOUNDFONT) is None:
        print(f"Missing soundfont: {config.DEFAULT_SOUNDFONT}")
        print("  Looked in: " + ", ".join(str(d) for d in
                                          (config.DATA_DIR / "soundfonts",
                                           config.BUNDLE / "soundfonts")))
        print("See soundfonts/README.md -- GeneralUser GS 2.0.3.")
        return 1

    fine_timer = raise_timer_resolution()
    port = free_port(args.host, args.port)
    url = f"http://{args.host}:{port}"

    import uvicorn  # imported late so the banner appears before uvicorn's own logging

    from backend.server import api

    # Your saved settings, not config.HARDWARE. Reading the defaults here printed
    # "WASAPI exclusive" at every launch including the ones where you had switched to
    # shared -- a banner that cannot be wrong about the thing it exists to report.
    audio = {**config.HARDWARE, **(config.settings.get("audio", default={}) or {})}
    exclusive = bool(audio.get("exclusive", True))
    rate = float(audio["sample_rate"])
    buffer_ms = audio["period_size"] / rate * 1000
    print()
    print(f"  Keys {version.VERSION}")
    print(f"  {f'{buffer_ms:.2f} ms buffer, WASAPI exclusive' if exclusive else 'WASAPI shared -- Windows picks the buffer (~10 ms)'}"
          f", {rate:.0f} Hz")
    print(f"  1 ms timer: {'yes' if fine_timer else 'unavailable'}")
    print(f"  {url}")
    print()
    print('  The \'Device "__none__" does not exists\' error below is intentional --')
    print("  it is what stops FluidSynth grabbing the piano and doubling every note.")
    print()

    if args.dev:
        # Python block-buffers stdout when it is not a terminal, so piping the dev log
        # anywhere -- a file, Tee-Object, another process -- swallows it for minutes at
        # a time. A log you cannot redirect is not a log.
        try:
            sys.stdout.reconfigure(line_buffering=True)
        except (AttributeError, OSError):  # pragma: no cover
            pass
        print("  --- dev ------------------------------------------------------------")
        print(f"  data dir    {config.DATA_DIR}")
        print(f"  bundle      {config.BUNDLE}" + ("  (frozen)" if config.FROZEN else ""))
        print(f"  fluidsynth  {backend.FLUIDSYNTH_BIN}")
        print(f"  soundfont   {config.find_asset('soundfonts', config.DEFAULT_SOUNDFONT)}")
        print(f"  database    {config.DB_PATH}")
        print(f"  settings    {config.SETTINGS_PATH}")
        print(f"  switch int. {sys.getswitchinterval() * 1000:.2f} ms  (0.80 is the tuned value)")
        print()
        print("  Frontend edits need no restart -- the modules are served straight off")
        print("  disk, so save and refresh the browser. Backend edits do need one:")
        print("  auto-reload would drop and re-acquire the exclusive-mode audio device")
        print("  on every save, which is a second of silence and a race for the port.")
        print("  --------------------------------------------------------------------")
        print()
        threading.Thread(target=dev_monitor, daemon=True).start()

    server = uvicorn.Server(uvicorn.Config(
        api, host=args.host, port=port,
        log_level="info" if args.dev else "warning", access_log=args.dev,
    ))
    # uvicorn installs SIGINT/SIGTERM handlers, which only the main thread may do --
    # and the main thread belongs to the window. Ctrl+C in the console still works:
    # it lands on the main thread and the finally below asks the server to stop.
    server.install_signal_handlers = lambda: None
    thread = threading.Thread(target=server.run, name="uvicorn", daemon=True)
    thread.start()

    try:
        if args.no_browser:
            # Headless: nothing to open, so just wait on the server.
            while thread.is_alive():
                thread.join(0.5)
        elif args.browser:
            open_when_ready(url, args.host, port)
            while thread.is_alive():
                thread.join(0.5)
        else:
            # Wait for the audio device to be ours BEFORE the browser engine starts.
            # See run_window().
            if not wait_for_port(args.host, port):
                print("  The server did not come up. Nothing to show.")
                return 1
            if not run_window(url, args.dev):
                webbrowser.open(url)
                while thread.is_alive():
                    thread.join(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        drop_timer_resolution()
    print("\nbye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
