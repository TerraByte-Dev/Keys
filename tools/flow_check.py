"""Checks for Chord Flow: the composer (frontend/harmony.js) and the pad (backend/flow.py).

    .venv\\Scripts\\python.exe tools\\flow_check.py

**The promise is "every lit key sounds good right now", so the load-bearing test is
that no chord ever leaves the key.** harmony.js builds chords by stacking scale steps;
backend/theory.py builds them a second, separate way for the Chords-in-this-key panel.
Every chord the composer plays, in every key Chord Flow offers, over thousands of
chords and several seeds, is checked against the server's chord on that degree. The
one allowed exception is minor V, whose raised third is the point of it.

The musical rules are checked as behaviour, not as a table: phrases do not come
straight back, a session opens on the key's own chord, the right hand moves a few
semitones per change rather than jumping, and both hands stay where a beginner's
hands are.

The pad is checked with a recording synth: it holds a chord until the next one, never
re-strikes the same chord, keeps off the zones' channels, and lets go by itself when
nothing renews it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
sys.path.insert(0, str(ROOT))

from backend import flow, theory   # noqa: E402

# Must match KEYS in frontend/flow.js -- checked below by reading that file.
KEYS = [("C", "major"), ("A", "natural_minor"), ("G", "major"), ("E", "natural_minor"),
        ("F", "major"), ("D", "natural_minor"), ("D", "major"), ("B", "natural_minor"),
        ("Bb", "major"), ("G", "natural_minor"), ("F#", "major"), ("Eb", "natural_minor")]

results: list[tuple[str, bool, str]] = []


def ok(label: str, passed: bool, detail: str = "") -> None:
    results.append((label, bool(passed), detail))


DRIVER = r"""
import { createProgression, leftKeys, voiceLeft, voiceRight, PALETTE, PHRASES, chordPcs } from './harmony.js';

const keys = JSON.parse(process.env.FLOW_KEYS);
const out = [];
const ok = (label, passed, detail = '') => out.push({ label, passed: !!passed, detail });

function seeded(seed) {
  let s = seed % 2147483647 || 1;
  return () => (s = (s * 16807) % 2147483647) / 2147483647;
}

/* 1. Every chord stays in the key -- against the server's own chords. */
{
  let chords = 0, bad = [];
  for (const k of keys) {
    const mode = k.mode === 'major' ? 'major' : 'minor';
    for (const seed of [1, 7, 42, 999, 31337]) {
      const p = createProgression({ mode, scale: k.pcs, rng: seeded(seed) });
      for (let i = 0; i < 300; i++) {
        const c = p.next();
        chords++;
        const deg = { I: 0, ii: 1, iii: 2, IV: 3, V: 4, vi: 5, i: 0, III: 2, iv: 3, v: 4, VI: 5, VII: 6 }[c.roman];
        const want = k.chords[deg].slice();
        if (mode === 'minor' && c.roman === 'V') want[1] = (want[1] + 1) % 12;   // the raised third
        if (JSON.stringify(c.pcs) !== JSON.stringify(want)) bad.push(`${k.name} ${c.roman}: ${c.pcs} vs ${want}`);
      }
    }
  }
  ok('every chord matches the server\'s chord on that degree', !bad.length,
     bad.length ? bad.slice(0, 3).join('; ') : `${chords} chords, ${keys.length} keys`);
}

/* 2. Nothing but the palette: no vii° in major, no ii° in minor. */
{
  const seen = { major: new Set(), minor: new Set() };
  for (const mode of ['major', 'minor']) {
    const p = createProgression({ mode, scale: keys[mode === 'major' ? 0 : 1].pcs, rng: seeded(5) });
    for (let i = 0; i < 2000; i++) seen[mode].add(p.next().roman);
  }
  const extra = [...seen.major].filter((r) => !PALETTE.major.includes(r))
    .concat([...seen.minor].filter((r) => !PALETTE.minor.includes(r)));
  ok('only palette chords are ever played (no diminished chord)', !extra.length, extra.join(', '));
  ok('the whole major palette gets used', PALETTE.major.every((r) => seen.major.has(r)),
     [...seen.major].join(' '));
  ok('the whole minor palette gets used', PALETTE.minor.every((r) => seen.minor.has(r)),
     [...seen.minor].join(' '));
}

/* 3. It opens on the key's own chord, and phrases do not come straight back. */
{
  let firsts = new Set(), repeats = 0, phrases = 0, ids = new Set();
  for (let seed = 1; seed <= 60; seed++) {
    for (const mode of ['major', 'minor']) {
      const p = createProgression({ mode, scale: keys[mode === 'major' ? 0 : 1].pcs, rng: seeded(seed) });
      const first = p.next();
      firsts.add(first.roman);
      const order = [first.phrase.id];
      for (let i = 0; i < 200; i++) {
        const c = p.next();
        if (c.step === 0 && c.pass === 1) order.push(c.phrase.id);
      }
      phrases += order.length;
      order.forEach((id) => ids.add(id));
      for (let i = 1; i < order.length; i++) {
        if (order.slice(Math.max(0, i - 3), i).includes(order[i])) repeats++;
      }
    }
  }
  ok('a session opens on the key\'s own chord (I or i)', [...firsts].every((r) => r === 'I' || r === 'i'),
     [...firsts].join(' '));
  ok('no phrase returns within three phrases of itself', repeats === 0, `${repeats} of ${phrases}`);
  ok('every phrase gets played', ids.size === PHRASES.major.length + PHRASES.minor.length,
     `${ids.size} of ${PHRASES.major.length + PHRASES.minor.length}`);
}

/* 4. Second passes vary sometimes, never the first chord, never into a duplicate. */
{
  let varied = 0, firstVaried = 0, dup = 0, total = 0;
  const p = createProgression({ mode: 'major', scale: keys[0].pcs, rng: seeded(11) });
  let prev = null;
  for (let i = 0; i < 4000; i++) {
    const c = p.next();
    total++;
    if (c.varied) {
      varied++;
      if (c.step === 0) firstVaried++;
      if (prev && prev.roman === c.roman) dup++;
    }
    prev = c;
  }
  ok('second passes are sometimes varied', varied > 50 && varied < total / 4, `${varied} of ${total}`);
  ok('a swap never touches the first chord or doubles its neighbour', !firstVaried && !dup,
     `first=${firstVaried} dup=${dup}`);
}

/* 5. Hands: where a beginner's are, and moving little. */
{
  let rh = null, lh = null, worst = 0, sum = 0, n = 0, outside = [];
  for (const k of keys) {
    const mode = k.mode === 'major' ? 'major' : 'minor';
    const p = createProgression({ mode, scale: k.pcs, rng: seeded(3) });
    rh = null; lh = null;
    for (let i = 0; i < 400; i++) {
      const c = p.next();
      const v = voiceRight(c.pcs, rh);
      const l = voiceLeft(c.pcs[0], lh);
      if (v[0] < 55 || v[2] > 79 || v[2] - v[0] > 9) outside.push(`${k.name} RH ${v}`);
      if (l < 40 || l > 51 || l % 12 !== c.pcs[0]) outside.push(`${k.name} LH ${l}`);
      if (v.map((m) => m % 12).sort().join() !== [...c.pcs].sort().join()) outside.push(`${k.name} RH notes ${v}`);
      for (const style of ['root', 'fifth']) {
        const keysL = leftKeys(l, style, v[0]);
        if (Math.max(...keysL) >= v[0]) outside.push(`${k.name} LH ${style} ${keysL} hits RH ${v}`);
        if (keysL[0] % 12 !== c.pcs[0]) outside.push(`${k.name} LH ${style} ${keysL} not on the root`);
      }
      if (rh) {
        const move = v.reduce((a, m, j) => a + Math.abs(m - rh[j]), 0);
        worst = Math.max(worst, move);
        sum += move; n++;
      }
      rh = v; lh = l;
    }
  }
  ok('both hands stay in range, play the chord, and never share a key',!outside.length, outside.slice(0, 3).join('; '));
  ok('the right hand moves a few semitones per change, not leaps', sum / n <= 5 && worst <= 14,
     `mean ${(sum / n).toFixed(2)}, worst ${worst}`);
}

/* 5b. A one-finger move is found when there is one -- including above F#4. */
{
  const g = voiceRight([7, 11, 2], [66, 71, 74]);       // Bm F#4 B4 D5 -> G
  ok('Bm to G keeps B and D and moves one finger', JSON.stringify(g) === '[67,71,74]', `${g}`);
}

/* 6. The first chord is the textbook shape. */
{
  const c = voiceRight([0, 4, 7], null);
  ok('the first right-hand chord is in root position', c[0] % 12 === 0, `${c}`);
}

/* 7. Minor V really is major (leading tone), and v really is minor. */
{
  const am = keys.find((k) => k.name === 'A natural_minor');
  ok('minor V is E G# B and v is E G B', JSON.stringify(chordPcs(am.pcs, 'V')) === '[4,8,11]'
     && JSON.stringify(chordPcs(am.pcs, 'v')) === '[4,7,11]', `${chordPcs(am.pcs, 'V')}`);
}

console.log(JSON.stringify(out));
"""


# ── the pad, with a recording synth ─────────────────────────────────────────
class FakeFS:
    def __init__(self) -> None:
        self.log: list[tuple] = []

    def noteon(self, ch, n, v):
        self.log.append(("on", ch, n))

    def noteoff(self, ch, n):
        self.log.append(("off", ch, n))

    def program_select(self, ch, sfid, bank, prog):
        self.log.append(("prog", ch, prog))
        return 0

    def cc(self, ch, c, v):
        self.log.append(("cc", ch, c, v))


def pad_checks() -> None:
    fs = FakeFS()
    eng = SimpleNamespace(fs=fs, active_channels=(0, 1, 2), load_soundfont=lambda name: 1)
    pad = flow.FlowBacking(eng)

    got = pad.play([48, 60, 64, 67], sound="strings")
    ons = [e for e in fs.log if e[0] == "on"]
    ok("the pad takes a channel no zone is using", got["channel"] == 3 and all(e[1] == 3 for e in ons),
       f"channel {got['channel']}")
    ok("it selects the sound asked for", ("prog", 3, flow.SOUNDS["strings"]) in fs.log)
    ok("it sounds every note of the chord", sorted(e[2] for e in ons) == [48, 60, 64, 67])

    fs.log.clear()
    pad.play([48, 60, 64, 67], sound="strings")
    ok("the same chord again does not re-strike", not [e for e in fs.log if e[0] in ("on", "off")],
       str(fs.log))

    fs.log.clear()
    pad.play([48, 60, 64, 67], sound="pad")
    ok("a new sound re-strikes the same chord on that sound",
       ("prog", 3, flow.SOUNDS["pad"]) in fs.log and len([e for e in fs.log if e[0] == "on"]) == 4)

    fs.log.clear()
    pad.play([45, 60, 64, 69])          # C to Am: C and E stay
    offs = [e[2] for e in fs.log if e[0] == "off"]
    ons = [e[2] for e in fs.log if e[0] == "on"]
    first_on = next(i for i, e in enumerate(fs.log) if e[0] == "on")
    last_off = max(i for i, e in enumerate(fs.log) if e[0] == "off")
    ok("a new chord releases the old one first, then plays", sorted(offs) == [48, 60, 64, 67]
       and sorted(ons) == [45, 60, 64, 69] and last_off < first_on)

    fs.log.clear()
    ok("stop silences it and says so", pad.stop() and sorted(e[2] for e in fs.log if e[0] == "off") == [45, 60, 64, 69])
    ok("stopping a silent pad says nothing was sounding", pad.stop() is False)

    fs.log.clear()
    pad.play([50, 62], hold_s=1.0)
    time.sleep(1.4)
    ok("with nothing renewing it, the pad lets go by itself",
       sorted(e[2] for e in fs.log if e[0] == "off") == [50, 62])

    fs.log.clear()
    pad.play([50, 62], hold_s=1.0)
    time.sleep(0.6)
    pad.play([50, 62], hold_s=1.0)      # renewed
    time.sleep(0.6)
    ok("a renewal keeps it sounding past the first deadline", not [e for e in fs.log if e[0] == "off"])
    pad.stop()

    eng.active_channels = tuple(range(0, 9))
    ok("with every spare channel taken it still picks one", flow.FlowBacking(eng)._pick_channel() == 8)

    eng.fs = None
    try:
        flow.FlowBacking(eng).play([60])
        ok("no engine is an error, not a crash", False)
    except RuntimeError:
        ok("no engine is an error, not a crash", True)


def source_checks() -> None:
    src = (FRONTEND / "flow.js").read_text(encoding="utf-8")
    for key, mode in KEYS:
        if f"key: '{key}', mode: '{mode}'" not in src:
            ok("flow.js offers the keys this check covers", False, f"{key} {mode} missing")
            return
    ok("flow.js offers the keys this check covers", True, f"{len(KEYS)} keys")
    server = (ROOT / "backend" / "server.py").read_text(encoding="utf-8")
    ok("panic stops the pad", "self.flow.stop()" in server)


def main() -> int:
    node = shutil.which("node")
    if not node:
        print("node not found -- this check needs it to run the real composer")
        return 1

    keys = []
    for key, mode in KEYS:
        plan = theory.scale_plan(key, mode)
        keys.append({"name": f"{key} {mode}", "mode": mode, "pcs": plan["pcs"],
                     "chords": [c["pcs"] for c in plan["chords"]]})

    driver = FRONTEND / "_flow_check.mjs"
    driver.write_text(DRIVER, encoding="utf-8")
    try:
        proc = subprocess.run([node, str(driver)], capture_output=True, text=True,
                              env={**__import__("os").environ, "FLOW_KEYS": json.dumps(keys)})
    finally:
        driver.unlink(missing_ok=True)
    if proc.returncode != 0:
        print("the driver did not run:")
        print(proc.stderr.strip() or proc.stdout.strip())
        return 1
    for r in json.loads(proc.stdout.strip().splitlines()[-1]):
        ok(r["label"], r["passed"], r.get("detail", ""))

    pad_checks()
    source_checks()

    passed = all(p for _, p, _ in results)
    for label, p, detail in results:
        print(f"  [{'PASS' if p else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))
    print()
    print(f"{'ALL CHECKS PASSED' if passed else 'FAILED'}  ({len(results)} assertions)")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
