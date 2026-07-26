"""Syntax-check every frontend module, and sanity-check the HTML it depends on.

There is no build step, which is a feature -- but it means nothing catches a stray
bracket until the browser silently refuses to run the module and the page comes up
empty. One missing parenthesis in a view took the entire app down exactly once; this
is the gate that stops it happening twice.

    .venv\\Scripts\\python.exe tools\\frontend_check.py

Needs Node only for the syntax pass. Without Node it still runs the structural checks
and says so rather than pretending it verified something.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"

ok = True


def step(label: str, passed: bool, detail: str = "") -> None:
    global ok
    ok = ok and passed
    print(f"  [{'PASS' if passed else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))


modules = sorted(FRONTEND.rglob("*.js"))
print(f"1. ES module syntax ({len(modules)} files)")
node = shutil.which("node")
if not node:
    print("       node not found -- skipping the syntax pass (install Node to enable it)")
else:
    for path in modules:
        rel = path.relative_to(ROOT).as_posix()
        proc = subprocess.run(
            [node, "--input-type=module", "--check"],
            stdin=path.open("rb"), capture_output=True, text=True,
        )
        if proc.returncode == 0:
            step(rel, True)
        else:
            first = next((ln for ln in proc.stderr.splitlines() if "Error" in ln), "")
            step(rel, False, first.strip())

print("2. every module the HTML and app.js reference actually exists")
index = (FRONTEND / "index.html").read_text("utf-8")
for src in re.findall(r'(?:src|href)="/([^"]+)"', index):
    step(f"index.html -> {src}", (FRONTEND / src).exists())

for path in modules:
    text = path.read_text("utf-8")
    for spec in re.findall(r"^\s*import\s+.*?from\s+'([^']+)'", text, re.M):
        if not spec.startswith("."):
            continue
        target = (path.parent / spec).resolve()
        step(f"{path.name} -> {spec}", target.exists(), "" if target.exists() else str(target))

print("3. views expose the shape app.js expects")
for path in sorted((FRONTEND / "views").glob("*.js")):
    text = path.read_text("utf-8")
    has_default = "export default" in text
    has_mount = re.search(r"\bmount\s*\(", text) is not None
    step(f"{path.name} default export with mount()", has_default and has_mount)

print("4. CSS custom properties the keyboard reads are all defined")
css = (FRONTEND / "style.css").read_text("utf-8")
kb = (FRONTEND / "keyboard.js").read_text("utf-8")
needed = set(re.findall(r"var\((--key-[a-z-]+)", kb))
defined = set(re.findall(r"^\s*(--key-[a-z-]+)\s*:", css, re.M))
missing = sorted(needed - defined)
step("no keyboard variable falls back to its built-in default",
     not missing, f"{len(needed)} used, {len(defined)} defined"
     + (f", missing {missing}" if missing else ""))

print("5. no absolute developer paths leaked into shipped files")
leaked = []
for path in list(modules) + [FRONTEND / "index.html", FRONTEND / "style.css"]:
    if re.search(r"[A-Za-z]:\\\\Users\\\\|/Users/|/home/", path.read_text("utf-8")):
        leaked.append(path.name)
step("frontend is path-clean", not leaked, ", ".join(leaked) if leaked else "")

print()
print("ALL CHECKS PASSED" if ok else "SOMETHING FAILED")
sys.exit(0 if ok else 1)
