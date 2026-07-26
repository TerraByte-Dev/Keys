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
from backend import config

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


def main() -> int:
    parser = argparse.ArgumentParser(description="Keys -- MIDI piano practice app")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-browser", action="store_true")
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

    audio = config.HARDWARE
    buffer_ms = audio["period_size"] / audio["sample_rate"] * 1000
    print()
    print("  Keys")
    print(f"  {buffer_ms:.2f} ms buffer, WASAPI exclusive, {audio['sample_rate']:.0f} Hz")
    print(f"  1 ms timer: {'yes' if fine_timer else 'unavailable'}")
    print(f"  {url}")
    print()
    print('  The \'Device "__none__" does not exists\' error below is intentional --')
    print("  it is what stops FluidSynth grabbing the piano and doubling every note.")
    print()

    if not args.no_browser:
        threading.Thread(
            target=open_when_ready, args=(url, args.host, port), daemon=True,
        ).start()

    try:
        uvicorn.run(api, host=args.host, port=port, log_level="warning", access_log=False)
    except KeyboardInterrupt:
        pass
    finally:
        drop_timer_resolution()
    print("\nbye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
