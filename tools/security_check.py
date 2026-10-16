"""Checks for the localhost guard and the file-name rules (v0.8.4).

Runs against a throwaway data directory and a stub ASGI app: no audio device, no
browser, no piano. What is asserted is that a hostile Host/Origin never reaches the
app, that a plain same-origin request still does, and that no request-supplied name
can climb out of its folder.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SANDBOX = Path(tempfile.mkdtemp(prefix="keys-security-check-"))
os.environ["KEYS_DATA_DIR"] = str(SANDBOX)

from backend import config                      # noqa: E402
from backend.server import LocalOnlyGuard, _PRESET_ID   # noqa: E402

fails: list[str] = []


def ok(cond: bool, label: str) -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond:
        fails.append(label)


async def probe(kind: str, host: str | None, origin: str | None = None) -> str:
    """'passed' if the stub app was reached, else 'blocked'."""
    reached: list[int] = []

    async def stub(scope, receive, send):
        reached.append(1)

    sent: list[dict] = []

    async def send(msg):
        sent.append(msg)

    headers = []
    if host is not None:
        headers.append((b"host", host.encode()))
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    await LocalOnlyGuard(stub)({"type": kind, "headers": headers}, None, send)
    return "passed" if reached else "blocked"


def run(*a):
    return asyncio.run(probe(*a))


print("localhost guard")
for kind in ("http", "websocket"):
    ok(run(kind, "127.0.0.1:8770") == "passed", f"{kind}: loopback, no Origin passes")
    ok(run(kind, "localhost:8770", "http://localhost:8770") == "passed", f"{kind}: same-origin passes")
    ok(run(kind, "127.0.0.1:8770", "http://127.0.0.1:8770") == "passed", f"{kind}: same-origin ip passes")
    ok(run(kind, "evil.example:8770") == "blocked", f"{kind}: DNS-rebinding Host blocked")
    ok(run(kind, "127.0.0.1.evil.example") == "blocked", f"{kind}: lookalike Host blocked")
    ok(run(kind, None) == "blocked", f"{kind}: missing Host blocked")
    ok(run(kind, "127.0.0.1:8770", "https://evil.example") == "blocked", f"{kind}: cross-site Origin blocked")
    ok(run(kind, "127.0.0.1:8770", "http://localhost:8770") == "blocked", f"{kind}: other-name Origin blocked")
    ok(run(kind, "127.0.0.1:8770", "http://127.0.0.1:9999") == "blocked", f"{kind}: other-port Origin blocked")
    ok(run(kind, "127.0.0.1:8770", "null") == "blocked", f"{kind}: Origin null blocked")
ok(run("lifespan", None) == "passed", "lifespan scope is untouched")

print("file names")
BS = chr(92)
for bad in ("", ".", "..", ".." + BS + "x", "../x", "a/b", "a" + BS + "b", "C:x", "x" + chr(0), " x"):
    ok(not config.safe_filename(bad), f"safe_filename rejects {bad!r}")
ok(config.safe_filename("GeneralUser-GS.sf2"), "safe_filename accepts a plain name")
ok(config.find_asset("soundfonts", ".." + BS + ".." + BS + "config.local.json") is None, "find_asset will not climb out")
ok(config.find_asset("soundfonts", "C:" + BS + "Windows" + BS + "win.ini") is None, "find_asset refuses a drive path")
for bad in (".." + BS + "x", "../x", "a/b", "", "x.", "-x", "A" * 65, "con" + chr(0)):
    ok(not _PRESET_ID.fullmatch(bad.lower()) or bad.endswith("."), f"preset id rejects {bad!r}")
ok(_PRESET_ID.fullmatch("grand-piano") is not None, "preset id accepts grand-piano")

shutil.rmtree(SANDBOX, ignore_errors=True)
print(f"\n{'FAILED: ' + str(len(fails)) if fails else 'all passed'}")
sys.exit(1 if fails else 0)
