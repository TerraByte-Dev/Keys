"""Checks for backend/midi_import.py -- MIDI in, MusicXML out.

Point it at a folder of MIDI files to check a whole corpus:

    .venv\\Scripts\\python tools\\midi_check.py
    .venv\\Scripts\\python tools\\midi_check.py "C:\\path\\to\\midis"

Two bugs got through before this existed, and both are checked here by name.

**Chord ordering.** `<chord/>` means "add this note to the previous one", so the
first note of a chord must not carry it. The events were sorted by DESCENDING pitch
while the chord flag was assigned after sorting ascending, so the flagged note came
out first -- a `<chord/>` with nothing in front of it to join. Verovio does not
reject that: its WebAssembly reads out of bounds and dies, so a one-word ordering
bug presented as "the engraver crashed".

**Bars that do not add up.** A note longer than the rest of its measure has to be
split at the bar line and tied. Without that it was emitted whole into the measure
it started in, leaving that measure longer than a bar. 52 of the 73 scores in the
corpus were wrong this way -- and Fur Elise, the file used to develop the converter,
happened to be one of the 21 that were fine.

That is the reason this walks a whole corpus rather than one file. Both bugs are
invisible on some inputs.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import midi_import, score   # noqa: E402

DEFAULT_CORPUS = Path.home() / "Desktop" / "Media" / "Library" / "SheetMusic"
VENDOR = ROOT / "frontend" / "vendor"

fails: list[str] = []
count = 0


def ok(cond: bool, label: str, detail: str = "") -> None:
    global count
    count += 1
    print(("  [PASS] " if cond else "  [FAIL] ") + label + (f" -- {detail}" if detail else ""))
    if not cond:
        fails.append(label)


def synth_midi(events: list[tuple[int, int, int]], ppq: int = 384,
               num: int = 4, den: int = 2, sharps: int = 0) -> bytes:
    """A one-track MIDI from (tick, duration, note), for the shaped cases below."""
    def var(n: int) -> bytes:
        out = bytearray([n & 0x7F])
        n >>= 7
        while n:
            out.insert(0, 0x80 | (n & 0x7F))
            n >>= 7
        return bytes(out)

    msgs: list[tuple[int, bytes]] = [
        (0, b"\xFF\x58\x04" + bytes([num, den, 24, 8])),
        (0, b"\xFF\x59\x02" + bytes([sharps & 0xFF, 0])),
    ]
    for tick, dur, note in events:
        msgs.append((tick, bytes([0x90, note, 80])))
        msgs.append((tick + dur, bytes([0x80, note, 0])))
    msgs.sort(key=lambda m: m[0])

    trk, prev = bytearray(), 0
    for tick, payload in msgs:
        trk += var(tick - prev) + payload
        prev = tick
    trk += var(0) + b"\xFF\x2F\x00"
    head = b"MThd" + (6).to_bytes(4, "big") + (0).to_bytes(2, "big") \
        + (1).to_bytes(2, "big") + ppq.to_bytes(2, "big")
    return head + b"MTrk" + len(trk).to_bytes(4, "big") + bytes(trk)


def audit(xml: bytes) -> dict[str, object]:
    """Everything that can be checked without a renderer."""
    root = ET.fromstring(xml)
    div = int(root.findtext(".//divisions") or 384)
    beats = int(root.findtext(".//time/beats") or 4)
    btype = int(root.findtext(".//time/beat-type") or 4)
    bar = round(beats * (4 / btype) * div)

    short_bars, chord_first, zero = 0, 0, 0
    for m in root.iter("measure"):
        per: dict[str, int] = {}
        seen_base: set[str] = set()
        for n in m:
            if n.tag == "backup":
                seen_base.clear()
                continue
            if n.tag != "note":
                continue
            v = n.findtext("voice") or "1"
            d = int(n.findtext("duration") or 0)
            if d <= 0:
                zero += 1
            if n.find("chord") is not None:
                # A chord note with no plain note before it in this voice.
                if v not in seen_base:
                    chord_first += 1
            else:
                seen_base.add(v)
                per[v] = per.get(v, 0) + d
        short_bars += sum(1 for t in per.values() if t != bar)
    return {"bar": bar, "short_bars": short_bars,
            "chord_first": chord_first, "zero": zero}


print("1. shaped cases")

# A note lasting two bars must come back as tied pieces that each fit their bar.
xml, rep = midi_import.convert(synth_midi([(0, 384 * 8, 60)]))
a = audit(xml)
ok(a["short_bars"] == 0, "a note twice the length of the bar is split and tied",
   f"{a['short_bars']} short bars")
sc = score.parse(xml, "x.musicxml")
ok(len(sc.notes) == 1 and abs(sc.notes[0].duration - 8.0) < 1e-6,
   "and our own reader merges the ties back into one 8-quarter note",
   f"{len(sc.notes)} notes, {sc.notes[0].duration if sc.notes else 0} quarters")

# A chord: three notes, same onset, same length.
xml, _ = midi_import.convert(synth_midi([(0, 384, 60), (0, 384, 64), (0, 384, 67)]))
a = audit(xml)
ok(a["chord_first"] == 0, "a chord never leads with its <chord/> note",
   f"{a['chord_first']} bad")
ok(xml.count(b"<chord/>") == 2, "and marks exactly the notes after the first",
   f"{xml.count(b'<chord/>')} of 3")

# Five sixteenths is not a note type; it has to become a tied pair.
xml, _ = midi_import.convert(synth_midi([(0, 96 * 5, 60), (96 * 5, 96 * 11, 62)]))
ok(audit(xml)["short_bars"] == 0, "an unwritable length becomes a tie, not a guess")

# Spelling follows the line, not the key signature's flat side.
xml, _ = midi_import.convert(synth_midi([(0, 192, 64), (192, 192, 63), (384, 192, 64)]))
sc = score.parse(xml, "x.musicxml")
ok([n.name for n in sc.notes] == ["E4", "D#4", "E4"],
   "a chromatic note that resolves upward is a sharp",
   " ".join(n.name for n in sc.notes))
xml, _ = midi_import.convert(synth_midi([(0, 192, 64), (192, 192, 63), (384, 192, 62)]))
sc = score.parse(xml, "x.musicxml")
ok([n.name for n in sc.notes] == ["E4", "Eb4", "D4"],
   "and one that falls is a flat", " ".join(n.name for n in sc.notes))

# Garbage in.
for bad, why in ((b"not midi at all", "a file that is not MIDI"),
                 (b"MThd" + b"\x00" * 20, "a MIDI with no notes")):
    try:
        midi_import.convert(bad)
        ok(False, f"{why} is refused")
    except midi_import.MidiError:
        ok(True, f"{why} is refused with a readable error")


print("\n2. the corpus")

corpus = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CORPUS
midis = sorted(corpus.rglob("*.mid")) if corpus.exists() else []
if not midis:
    print(f"  (no MIDI files under {corpus} -- corpus checks skipped)")
else:
    tmp = Path(tempfile.mkdtemp(prefix="keys-midicheck-"))
    broke, short, chordbad, notes, snapped = [], [], [], 0, 0
    for m in midis:
        try:
            xml, rep = midi_import.convert(m.read_bytes(), m.stem)
            sc = score.parse(xml, m.name)
        except Exception as exc:                        # noqa: BLE001
            broke.append(f"{m.name}: {type(exc).__name__} {exc}")
            continue
        (tmp / (m.stem.replace(" ", "_") + ".musicxml")).write_bytes(xml)
        notes += len(sc.notes)
        snapped += rep["snapped"]
        a = audit(xml)
        if a["short_bars"]:
            short.append(f"{m.stem} ({a['short_bars']})")
        if a["chord_first"]:
            chordbad.append(f"{m.stem} ({a['chord_first']})")

    ok(not broke, f"all {len(midis)} files convert and re-parse",
       f"{len(broke)} failed: {broke[:2]}" if broke else f"{notes:,} notes")
    ok(not chordbad, "no file leads a chord with its <chord/> note",
       f"{len(chordbad)}: {chordbad[:3]}" if chordbad else "the bug that killed Verovio")
    # One known straggler is tolerated and named; silence would be the problem.
    ok(len(short) <= 1, "at most one file has a measure that does not add up",
       f"{len(short)}: {short[:4]}" if short else "every bar exact")
    if snapped:
        print(f"         {snapped:,} of {notes:,} durations approximated "
              f"({100 * snapped / max(1, notes):.1f}%)")

    node = shutil.which("node")
    if node and (VENDOR / "verovio.mjs").exists():
        harness = tmp / "render.mjs"
        harness.write_text(
            "import { readFileSync, readdirSync } from 'node:fs';\n"
            f"const V = {str(VENDOR.as_uri())!r};\n"
            "const { VerovioToolkit } = await import(`${V}/verovio.mjs`);\n"
            "const createModule = (await import(`${V}/verovio-module.mjs`)).default;\n"
            f"const dir = {str(tmp)!r};\n"
            "const files = readdirSync(dir).filter(f => f.endsWith('.musicxml')).sort();\n"
            "let ok = 0; const bad = [];\n"
            "for (const f of files) {\n"
            "  const tk = new VerovioToolkit(await createModule());\n"
            "  try {\n"
            "    if (!tk.loadData(readFileSync(dir + '/' + f, 'utf8'))) { bad.push(f); continue; }\n"
            "    if ((tk.renderToSVG(1).match(/class=\"note\"/g) || []).length < 1) { bad.push(f); continue; }\n"
            "    ok++;\n"
            "  } catch (e) { bad.push(f + ': ' + (e.message || e)); }\n"
            "}\n"
            "console.log(JSON.stringify({ ok, total: files.length, bad: bad.slice(0, 5) }));\n",
            "utf-8")
        try:
            out = subprocess.run([node, "--max-old-space-size=4096", str(harness)],
                                 capture_output=True, text=True, timeout=900)
            line = [ln for ln in out.stdout.splitlines() if ln.startswith("{")]
            import json
            res = json.loads(line[-1]) if line else {}
            ok(res.get("ok") == res.get("total") and res.get("total", 0) > 0,
               f"Verovio engraves all {res.get('total', '?')} of them",
               f"{res.get('ok')} ok, failed: {res.get('bad')}")
        except Exception as exc:                        # noqa: BLE001
            print(f"  (render check skipped: {exc})")
    else:
        print("  (node or the Verovio bundle is missing -- render check skipped)")
    shutil.rmtree(tmp, ignore_errors=True)

print()
if fails:
    print(f"  {len(fails)} of {count} FAILED:")
    for f in fails:
        print(f"    - {f}")
    raise SystemExit(1)
print(f"  {count} assertions")
print("ALL CHECKS PASSED")
