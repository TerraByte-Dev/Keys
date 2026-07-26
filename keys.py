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


def open_when_ready(url: str, host: str, port: int) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        with socket.socket() as s:
            s.settimeout(0.25)
            if s.connect_ex((host, port)) == 0:
                webbrowser.open(url)
                return
        time.sleep(0.15)


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
                print(f"  [dev] MIDI {'CONNECTED: ' + midi.port_name if midi.connected else 'not connected -- is the piano on?'}",
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
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--dev", action="store_true",
                        help="verbose logging, resolved paths, and a live 2 s status line")
    args = parser.parse_args()

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

    if not args.no_browser:
        threading.Thread(
            target=open_when_ready, args=(url, args.host, port), daemon=True,
        ).start()

    try:
        uvicorn.run(api, host=args.host, port=port,
                    log_level="info" if args.dev else "warning",
                    access_log=args.dev)
    except KeyboardInterrupt:
        pass
    finally:
        drop_timer_resolution()
    print("\nbye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
