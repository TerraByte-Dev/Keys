"""Render every tab in a real browser and assert the page is not broken.

    .venv\\Scripts\\python tools\\ui_check.py            # starts its own silent app
    .venv\\Scripts\\python tools\\ui_check.py --port 8770  # use one already running

The other suites read the source. This one looks at the pixels, because the two
things it checks cannot be seen any other way and have both shipped:

**Panels must not overlap, and must not be hollow.** The grid gives every panel an
explicit row span computed from its own height. Miss one and `grid-auto-rows: 8px`
makes it eight pixels tall while its content spills over whatever is beneath -- the
stacking on Practice that had to be dragged apart by hand. Get it too generous and
you get a short panel in a tall empty box.

**No element may print the word "null".** `replaceChildren()` accepts Nodes or
strings, so a conditional child that resolves to null becomes the TEXT "null".
Practice printed one above its exercise shelf for anyone with no exercise history,
which is to say for every new user.

Checked at three widths, because both failures are layout-dependent and a 1600px
window is not the only one anybody uses.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

VIEWS = ("play", "practice", "layers", "tools", "stats", "settings")
WIDTHS = ((1600, 1000), (1280, 860), (1920, 1080))

# A panel taller than its content by more than this is a panel in an empty box.
HOLLOW_PX = 28

AUDIT = """() => {
  const grid = document.querySelector('#stage .grid');
  if (!grid) return { error: 'no grid in #stage' };
  const gr = grid.getBoundingClientRect();
  const kids = [...grid.children].filter(e => e.getBoundingClientRect().height > 0);
  const boxes = kids.map(e => {
    const r = e.getBoundingClientRect();
    const c = e.dataset.panel ? e.firstElementChild : e;
    return {
      t: (e.querySelector('.mod__title')?.textContent || e.className).trim().slice(0, 28),
      x: Math.round(r.left - gr.left), y: Math.round(r.top - gr.top),
      w: Math.round(r.width), h: Math.round(r.height),
      inner: Math.round(c ? c.getBoundingClientRect().height : 0),
      span: e.style.gridRowEnd || '',
    };
  });

  const over = [];
  for (let i = 0; i < boxes.length; i++) {
    for (let j = i + 1; j < boxes.length; j++) {
      const a = boxes[i], b = boxes[j];
      const ox = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
      const oy = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
      if (ox > 2 && oy > 2) over.push(`${a.t} over ${b.t} by ${ox}x${oy}px`);
    }
  }

  // Every text node in the stage, hunting for a stringified nothing.
  const junk = [];
  const walk = (n) => {
    for (const c of n.childNodes) {
      if (c.nodeType === 3) {
        const t = c.textContent.trim();
        if (t === 'null' || t === 'undefined' || t === 'NaN' || t === '[object Object]') {
          junk.push(`"${t}" inside .${(n.className || n.tagName).toString().slice(0, 30)}`);
        }
      } else if (c.nodeType === 1) walk(c);
    }
  };
  walk(document.getElementById('stage'));

  return {
    n: boxes.length,
    over,
    junk,
    unspanned: boxes.filter(b => !b.span).map(b => b.t),
    hollow: boxes.filter(b => b.inner && b.h - b.inner > HOLLOW).map(
      b => `${b.t} is ${b.h - b.inner}px taller than its content`),
  };
}""".replace("HOLLOW", str(HOLLOW_PX))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0,
                    help="an app already running; omit to start a silent one")
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  playwright is needed for this check:")
        print("    .venv\\Scripts\\pip install playwright && playwright install chromium")
        return 1

    proc = sandbox = None
    port = args.port
    if not port:
        import json
        import os
        import subprocess
        import tempfile
        import ui_sandbox
        port = 8809
        sandbox = Path(tempfile.mkdtemp(prefix="keys-uicheck-"))
        (sandbox / "config.local.json").write_text(
            json.dumps(ui_sandbox.SILENT_CONFIG), "utf-8")
        log = open(sandbox / "server.log", "w", encoding="utf-8")
        proc = subprocess.Popen(
            [sys.executable, str(ROOT / "keys.py"), "--no-browser", "--port", str(port)],
            cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT,
            env={**os.environ, "KEYS_DATA_DIR": str(sandbox)})
        if not ui_sandbox.wait_for(port):
            print("  the app never answered")
            proc.terminate()
            return 1
        print(f"  silent app on {port}, data in {sandbox.name}")

    fails: list[str] = []
    count = 0
    errs: list[str] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            for width, height in WIDTHS:
                page = browser.new_page(viewport={"width": width, "height": height})
                page.on("pageerror", lambda e: errs.append(str(e)))
                page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
                page.wait_for_timeout(1600)
                print(f"\n{width}x{height}")
                for view in VIEWS:
                    page.evaluate(f"location.hash = '{view}'")
                    # Views load asynchronously; the packing happens after the fill.
                    page.wait_for_timeout(2200)
                    a = page.evaluate(AUDIT)
                    count += 1
                    if a.get("error"):
                        fails.append(f"{view} @{width}: {a['error']}")
                        print(f"  [FAIL] {view:<9} {a['error']}")
                        continue
                    bad = a["over"] + a["junk"] + a["hollow"] \
                        + [f"{t} has no row span" for t in a["unspanned"]]
                    if bad:
                        fails.extend(f"{view} @{width}: {b}" for b in bad)
                        print(f"  [FAIL] {view:<9} {a['n']:>2} panels")
                        for b in bad:
                            print(f"           {b}")
                    else:
                        print(f"  [PASS] {view:<9} {a['n']:>2} panels, no overlap, "
                              f"no gaps, nothing stringified")
                page.close()
            browser.close()
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:  # noqa: BLE001
                proc.kill()
        if sandbox is not None:
            import shutil
            shutil.rmtree(sandbox, ignore_errors=True)

    if errs:
        print(f"\n  {len(errs)} page error(s):")
        for e in dict.fromkeys(errs):
            print(f"    {e}")
        fails.extend(errs)

    print()
    if fails:
        print(f"  {len(fails)} problem(s) across {count} renders:")
        for f in fails:
            print(f"    - {f}")
        return 1
    print(f"  {count} renders across {len(WIDTHS)} widths")
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
