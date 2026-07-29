"""Run Keys for a UI test: real app, throwaway data, and SILENT.

    .venv\\Scripts\\python tools\\ui_sandbox.py --port 8801 -- python my_playwright.py

Two things this exists to prevent, both learned the hard way:

**It makes no sound.** A test instance is a whole synthesiser pointed at the default
output. A stray click in an automation script can start a count-in, and the first
anyone knows about it is a metronome in their headphones. `audio.gain` is 0 here,
which silences the output without disabling anything -- the engine still starts,
voices still count, every endpoint still answers, so nothing under test changes.

**It touches none of your data.** KEYS_DATA_DIR points at a fresh temp directory, so
the practice database, settings and recordings a test writes are its own. The
first-run tutorial is pre-dismissed, because a modal over the app is not what any
test meant to assert about.

The child is always killed and the directory always removed, including when the
inner command fails -- an orphaned instance is the thing that caused the problem
this file is named after.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SILENT_CONFIG = {
    # Zero gain, not a bogus device: the engine still opens, reports its buffer and
    # counts voices, so what the UI shows is what it would show for real.
    "audio": {"gain": 0.0},
    # A tutorial nobody asked for, over the thing being measured.
    "ui": {"tour_seen": True},
}


def wait_for(port: int, timeout: float = 40.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as s:
            s.settimeout(0.3)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.25)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8801)
    ap.add_argument("--keep", action="store_true",
                    help="leave the sandbox directory behind for inspection")
    ap.add_argument("command", nargs=argparse.REMAINDER,
                    help="-- then the command to run against it")
    args = ap.parse_args()

    cmd = args.command
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]

    sandbox = Path(tempfile.mkdtemp(prefix="keys-ui-"))
    (sandbox / "config.local.json").write_text(json.dumps(SILENT_CONFIG), "utf-8")

    env = {**os.environ, "KEYS_DATA_DIR": str(sandbox), "PYTHONIOENCODING": "utf-8"}
    log = open(sandbox / "server.log", "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "keys.py"), "--no-browser", "--port", str(args.port)],
        cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT,
    )

    code = 1
    try:
        if not wait_for(args.port):
            print(f"  server never answered on {args.port}")
            print((sandbox / "server.log").read_text("utf-8", errors="replace")[-2000:])
            return 1
        print(f"  silent Keys on http://127.0.0.1:{args.port}   data: {sandbox}")
        if not cmd:
            print("  no command given; stopping.")
            return 0
        code = subprocess.call(cmd, env={**env, "KEYS_PORT": str(args.port)})
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
        if args.keep:
            print(f"  sandbox kept: {sandbox}")
        else:
            shutil.rmtree(sandbox, ignore_errors=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
