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

# This regex has to cross newlines, and that is the whole point of it. The single-line
# version silently skipped every WRAPPED import statement -- prefs.js -> ./app.js and
# settings-overlay.js -> ./prefs.js were both on a continuation line and neither was ever
# checked, while the same files' unwrapped imports printed PASS and made it look covered.
# Double quotes and dynamic `import()` are here for the same reason: app.js reaches the
# engraver only through `await import('./engrave.js')`, so it was invisible too.
for path in modules:
    text = path.read_text("utf-8")
    specs = re.findall(r"^\s*(?:import|export)\s[\s\S]*?from\s+['\"]([^'\"]+)['\"]", text, re.M)
    specs += re.findall(r"import\(\s*['\"](\.[^'\"]+)['\"]\s*\)", text)
    for spec in dict.fromkeys(specs):
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

print("5. the dock constrains the keyboard")
# keyboard.js injects `.keys-kb{width:100%;height:auto}` into <head> at RUNTIME, i.e.
# after style.css. With height:auto the SVG sizes itself from its 8.43:1 viewBox and
# overflows the fixed-height dock, and body{overflow:hidden} crops it -- which looked
# like "all the keys are the same length" rather than like a layout bug. Two things have
# to hold, and neither is visible in a diff:
#   * the override must be TWO classes, or it loses the equal-specificity tie-break
#   * the dock's grid row must be explicit, or it is sized BY the keyboard instead
dock_rule = re.search(r"\.dock\s+\.keys-kb\s*\{([^}]*)\}", css)
step("style.css overrides .keys-kb height", dock_rule is not None and "height" in dock_rule.group(1),
     dock_rule.group(1).strip() if dock_rule else "no `.dock .keys-kb` rule at all")
step("the override is two-class, so it beats the injected sheet",
     dock_rule is not None, ".dock .keys-kb, not a bare .keys-kb")
dock_block = re.search(r"\.dock\s*\{([^}]*)\}", css)
step("the dock row is explicit, not auto-sized by its content",
     dock_block is not None and "grid-template-rows" in dock_block.group(1),
     "grid-template-rows: minmax(0, 1fr)")

print("6. no absolute developer paths leaked into shipped files")
leaked = []
for path in list(modules) + [FRONTEND / "index.html", FRONTEND / "style.css"]:
    if re.search(r"[A-Za-z]:\\\\Users\\\\|/Users/|/home/", path.read_text("utf-8")):
        leaked.append(path.name)
step("frontend is path-clean", not leaked, ", ".join(leaked) if leaked else "")

print("7. every id the JS looks up is spelled like one something declares -- a rename "
      "check, not proof the element is ever in the DOM")
# Checking selectors against index.html alone reports 137 false positives, because every
# view builds its own DOM in mount() and the shell only declares the ~55 static ids. So a
# declaration is any of three things. The third -- `.id = 'name'` assigned onto a node
# found by walking the DOM -- matches nothing in frontend/ today and is kept only so the
# check keeps working if anyone reaches for that shape again. It used to be load-bearing:
# slider() took no id, so views/tools.js labelled the tempo range by walking to it from
# #bpm-display. slider() now forwards an optional `id`, the walk is gone, and the range
# declares itself where it is built.
# Template-literal selectors (`$(`#zlo-${i}`)`) are deliberately not counted as uses: the
# id is assembled at runtime, so there is no static name to check against anything.
declared = set(re.findall(r'id="([^"]+)"', index))
used = set()
for path in modules:
    text = path.read_text("utf-8")
    declared |= set(re.findall(r"""\bid[:=]\s*['"]([A-Za-z0-9_-]+)['"]""", text))
    declared |= set(re.findall(r"""\.id\s*=\s*['"]([A-Za-z0-9_-]+)['"]""", text))
    used |= set(re.findall(r"""(?:\$|querySelector)\(\s*['"]#([A-Za-z0-9_-]+)['"]""", text))
    # getElementById is the other half of the app's lookups and was invisible here, so
    # #stage, #toasts, #tour-card and #loop-head went unchecked by a section that said it
    # covered every one. The closing paren is required for the same reason template
    # literals are skipped: `getElementById('lay-' + l.id)` names no id, and without the
    # guard the prefix `lay-` is reported as an id nothing declares.
    used |= set(re.findall(r"""getElementById\(\s*['"]([A-Za-z0-9_-]+)['"]\s*\)""", text))
unresolved = sorted(used - declared)
step("no selector reaches for an id nothing declares", not unresolved,
     f"{len(used)} used, {len(declared)} declared"
     + (f", unresolved {unresolved}" if unresolved else ""))

print("8. every file picker takes more than one file, and one module owns the upload")
# Bulk import was added to the songs drawer and shipped -- while #lib-file, the picker
# people actually use, and #sheet-file were two more copies of the same upload loop and
# still took one file each. Nothing threw: the picker just quietly refused a second
# selection. Both halves of that are checked here, because either one alone lets it
# happen again -- `multiple` on every input, and exactly one module allowed to POST.
IMPORTER = "frontend/import-scores.js"
FILE_INPUT = re.compile(r"""type\s*[:=]\s*['"]file['"]""")
POSTS_SCORES = re.compile(r"""(?:fetch|api\.post)\(\s*['"]/api/scores['"]""")


def enclosing_object(text: str, at: int) -> str | None:
    """The `{...}` props literal containing offset `at`, brace-matched both ways."""
    depth, i = 0, at
    while i >= 0:
        if text[i] == "}":
            depth += 1
        elif text[i] == "{":
            if depth == 0:
                break
            depth -= 1
        i -= 1
    if i < 0:
        return None
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[i:j + 1]
    return None


# (label, the text that declares the input). A picker built from a variable rather than a
# literal `type: 'file'` is invisible to this, which is why the count is asserted too --
# a scan that finds nothing must fail rather than pass quietly.
pickers: list[tuple[str, str]] = []
for path in sorted(FRONTEND.rglob("*.html")):
    text = path.read_text("utf-8")
    for tag in re.findall(r"<input\b[^>]*>", text):
        if FILE_INPUT.search(tag):
            pickers.append((path.relative_to(ROOT).as_posix(), tag))
for path in modules:
    text = path.read_text("utf-8")
    for m in FILE_INPUT.finditer(text):
        blob = enclosing_object(text, m.start())
        pickers.append((path.relative_to(ROOT).as_posix(), blob or ""))

step("the scan found file pickers at all", bool(pickers), f"{len(pickers)} found")
for rel, blob in pickers:
    ident = re.search(r"""id\s*[:=]\s*['"]([A-Za-z0-9_-]+)['"]""", blob)
    where = f"{rel} #{ident.group(1)}" if ident else rel
    step(f"{where} accepts more than one file", bool(re.search(r"\bmultiple\b", blob)),
         "" if blob else "could not read the input's declaration -- check it by hand")

owners = sorted(p.relative_to(ROOT).as_posix() for p in modules
                if POSTS_SCORES.search(p.read_text("utf-8")))
step(f"only {IMPORTER} POSTs to /api/scores", owners == [IMPORTER],
     "uploaders: " + (", ".join(owners) or "none -- the importer itself is gone"))

print()
print("ALL CHECKS PASSED" if ok else "SOMETHING FAILED")
sys.exit(0 if ok else 1)
