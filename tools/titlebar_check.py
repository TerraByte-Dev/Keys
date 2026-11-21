"""Checks for the app window's title bar (v0.15).

The rail is the window's title bar in the app window, and the page reaches the window
through pywebview's js_api. Anything public on that object is callable by every script
the page runs, so its surface is asserted here: exactly minimize, toggle_maximize and
close, and nothing pywebview would find by walking into it. No window is opened, no
audio device is touched; the native half (caption removal, Snap, edge resize) is
verified by hand in a real window, which no headless check can do.

    .venv\\Scripts\\python.exe tools\\titlebar_check.py
"""

from __future__ import annotations

import inspect
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SANDBOX = Path(tempfile.mkdtemp(prefix="keys-titlebar-check-"))
os.environ["KEYS_DATA_DIR"] = str(SANDBOX)

import keys  # noqa: E402

fails: list[str] = []
ALLOWED = {"minimize", "toggle_maximize", "close"}


def ok(cond: bool, label: str) -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond:
        fails.append(label)


def exposed(obj: object, base: str = "", seen: set | None = None) -> set[str]:
    """What pywebview 6 exposes from a js_api object -- the same walk as
    webview.util.inject_pywebview's get_functions: every public name, methods as
    callables, and any non-callable object or class recursed into."""
    seen = set() if seen is None else seen
    if id(obj) in seen:
        return set()
    seen.add(id(obj))
    out: set[str] = set()
    for name in dir(obj):
        if name.startswith("_"):
            continue
        full = f"{base}.{name}" if base else name
        attr = getattr(obj, name)
        if inspect.ismethod(attr) or inspect.isfunction(attr):
            out.add(full)
        elif inspect.isclass(attr) or (not callable(attr) and hasattr(attr, "__module__")):
            out |= exposed(attr, full, seen)
    return out


try:
    print("1. the js_api surface")
    controls = keys.WindowControls()
    surface = exposed(controls)
    ok(surface == ALLOWED, f"exactly {sorted(ALLOWED)} (got {sorted(surface)})")
    controls._hwnd = 12345   # state set after construction stays private
    ok(exposed(controls) == ALLOWED, "still exactly three with a window attached")
    ok(all(not inspect.signature(getattr(controls, n)).parameters for n in ALLOWED),
       "none of the three takes an argument from the page")

    print("2. with no window yet, every call is a harmless no-op")
    idle = keys.WindowControls()
    ok(idle.minimize() is None, "minimize")
    ok(idle.toggle_maximize() is False, "toggle_maximize reports not maximised")
    ok(idle.close() is None, "close")

    print("3. keys.py wiring")
    src = (ROOT / "keys.py").read_text("utf-8")
    ok(src.count("js_api=") == 1 and "js_api=controls" in src,
       "create_window is given the controls object and nothing else")
    ok(".expose(" not in src, "no window.expose() adds functions behind the class's back")
    ok(inspect.getsource(keys._user32).count('ctypes.WinDLL("user32"') == 1,
       "prototypes are set on a private user32, not the one pywebview shares")
    call = src[src.index("webview.create_window("):]
    call = call[:call.index("\n        )\n")]
    ok("frameless" not in call, "not pywebview's frameless (it drops resize and Snap)")
    ok(keys.title_bar_in_app() is True, "the app title bar is the default")

    print("4. the page side")
    tb = (ROOT / "frontend" / "titlebar.js").read_text("utf-8")
    called = set(re.findall(r"\bapi\.(\w+)\(", tb))
    ok(called == ALLOWED, f"titlebar.js calls only the three (got {sorted(called)})")
    css = (ROOT / "frontend" / "style.css").read_text("utf-8")
    ok("html.is-titlebar .rail {" in css and "app-region: drag" in css,
       "the rail is a drag region only in title-bar mode")
    ok(re.search(r"html\.is-titlebar \.rail button,\s*html\.is-titlebar \.rail input", css) is not None
       and "app-region: no-drag" in css, "buttons and inputs in the rail are carved out of the drag")
    app = (ROOT / "frontend" / "app.js").read_text("utf-8")
    ok("initTitlebar()" in app, "app.js mounts the title bar")
finally:
    shutil.rmtree(SANDBOX, ignore_errors=True)

print()
print(f"{'FAILED: ' + str(len(fails)) if fails else 'all passed'}")
sys.exit(1 if fails else 0)
