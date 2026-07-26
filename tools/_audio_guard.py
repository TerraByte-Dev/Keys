"""Refuse to open the audio device while the app is already using it.

`engine_check` and `pipeline_check` both start a real Synth and deliberately make
noise -- nine seconds of metronome, a handful of struck notes. That is correct for a
test of the sound engine and completely wrong to do behind someone's back.

In EXCLUSIVE mode a second Synth is simply refused, which is loud and obvious. In
SHARED mode it is not: both open happily, and the check's metronome and test notes
come out of the speakers mixed into whatever you were playing. It reads as the app
glitching -- audio "cutting out" and being replaced by a click and some stray notes --
and there is nothing in the app to suggest a test script is the cause.

So the checks ask first. One HTTP call, no dependencies, and it fails with an
instruction rather than a stack trace.
"""

from __future__ import annotations

import sys
import urllib.error
import urllib.request

DEFAULT_PORTS = (8770, 8771, 8772)


def app_running(ports: tuple[int, ...] = DEFAULT_PORTS) -> int | None:
    """Return the port Keys is answering on, or None."""
    for port in ports:
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/health", timeout=0.6) as r:
                if r.status == 200:
                    return port
        except (urllib.error.URLError, OSError, ValueError):
            continue
    return None


def require_quiet(what: str) -> None:
    """Exit unless the audio device is free. Call this before building an Engine."""
    if "--force" in sys.argv:
        return
    port = app_running()
    if port is None:
        return
    print(f"\n  Keys is running on port {port}.")
    print(f"  {what} opens the audio device and makes noise on purpose, which you")
    print("  would hear mixed into whatever you are playing.")
    print("\n  Close the app (or the console window running it) and try again.")
    print("  Pass --force if you really want both at once.\n")
    raise SystemExit(2)
