"""Checks for Jam: the composer and the note sets (frontend/harmony.js) and the band (backend/band.py).

    .venv\\Scripts\\python.exe tools\\jam_check.py

**The promise is "every lit key sounds good right now", so the load-bearing test is
that no chord ever leaves the key.** harmony.js builds chords by stacking scale steps;
backend/theory.py builds them a second, separate way for the Chords-in-this-key panel.
Every chord the composer plays, in every key Jam offers, over thousands of
chords and several seeds, is checked against the server's chord on that degree. The
one allowed exception is minor V, whose raised third is the point of it.

The musical rules are checked as behaviour, not as a table: phrases do not come
straight back, a session opens on the key's own chord, the lit notes over every chord
are the key minus the ones that rub (a half step above a chord note), and the way across
a change always lands on the next chord, close to your hand, holding a shared note when
one is near.

The band is checked against a recording sequencer: it keeps off the zones' channels,
plays the chart's chords in the right registers, never rewrites a queued bar, keeps its
place through a tempo change, and stops by itself when nothing tops up its chart.
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

from backend import band as band_mod, theory   # noqa: E402

# Must match KEYS in frontend/jam.js -- checked below by reading that file.
KEYS = [("C", "major"), ("A", "natural_minor"), ("G", "major"), ("E", "natural_minor"),
        ("F", "major"), ("D", "natural_minor"), ("D", "major"), ("B", "natural_minor"),
        ("Bb", "major"), ("G", "natural_minor"), ("F#", "major"), ("Eb", "natural_minor")]

results: list[tuple[str, bool, str]] = []


def ok(label: str, passed: bool, detail: str = "") -> None:
    results.append((label, bool(passed), detail))


DRIVER = r"""
import { createProgression, crossing, palette, PALETTE, PHRASES, chordPcs } from './harmony.js';

const keys = JSON.parse(process.env.JAM_KEYS);
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

/* 5. What sounds good: over every chord the composer plays, the lit notes are the chord
      plus the key minus any note a half step above a chord note -- and nothing else. */
{
  let n = 0, bad = [];
  for (const k of keys) {
    const mode = k.mode === 'major' ? 'major' : 'minor';
    const p = createProgression({ mode, scale: k.pcs, rng: seeded(9) });
    for (let i = 0; i < 150; i++) {
      const c = p.next();
      const scale = k.pcs.slice();
      if (c.roman === 'V' && mode === 'minor') scale[6] = c.pcs[1];
      const pal = palette(c.pcs, scale);
      n++;
      const all = [...pal.chord, ...pal.color, ...pal.rub];
      const want = new Set([...scale, ...c.pcs]);
      if (all.length !== want.size || !all.every((pc) => want.has(pc))) bad.push(`${k.name} ${c.roman}: tiers do not split the key`);
      if (JSON.stringify(pal.chord) !== JSON.stringify(c.pcs)) bad.push(`${k.name} ${c.roman}: chord tier is not the chord`);
      for (const pc of pal.color) if (c.pcs.includes((pc + 11) % 12)) bad.push(`${k.name} ${c.roman}: ${pc} rubs but is lit`);
      for (const pc of pal.rub) if (!c.pcs.includes((pc + 11) % 12)) bad.push(`${k.name} ${c.roman}: ${pc} unlit for no reason`);
      if (!pal.color.length) bad.push(`${k.name} ${c.roman}: nothing but the chord`);
    }
  }
  ok('over every chord, lit = chord + key, minus the notes a half step above the chord', !bad.length,
     bad.length ? bad.slice(0, 3).join('; ') : `${n} chords, ${keys.length} keys`);
}
{
  const CM = [0, 2, 4, 5, 7, 9, 11];
  const I = palette([0, 4, 7], CM), V = palette([7, 11, 2], CM), vi = palette([9, 0, 4], CM);
  ok('over C in C major: D, A and B add colour, F rubs', JSON.stringify(I.color) === '[2,9,11]'
     && JSON.stringify(I.rub) === '[5]', `${I.color} / ${I.rub}`);
  ok('over G in C major, C rubs (it sits on B); over Am, F rubs (it sits on E)',
     JSON.stringify(V.rub) === '[0]' && JSON.stringify(vi.rub) === '[5]', `${V.rub} / ${vi.rub}`);
  const am = keys.find((k) => k.name === 'A natural_minor');
  const sc = am.pcs.slice(); sc[6] = 8;
  const E = palette([4, 8, 11], sc);
  ok('over E major in A minor, only D adds colour (E7) -- A, C and F all rub',
     JSON.stringify(E.color) === '[2]', `${E.color} / ${E.rub}`);
}

/* 6. The way across a change: decided by where your hand is, always onto the next chord,
      always close, a shared note held when there is one nearby. */
{
  let n = 0, bad = [];
  const rnd = seeded(77);
  for (const k of keys) {
    const mode = k.mode === 'major' ? 'major' : 'minor';
    const p = createProgression({ mode, scale: k.pcs, rng: seeded(3) });
    let c = p.next();
    for (let i = 0; i < 150; i++) {
      const nx = p.next();
      const from = rnd() < 0.15 ? null : 40 + Math.floor(rnd() * 50);
      const x = crossing(c.pcs, nx.pcs, from, { low: 36, high: 96 });
      n++;
      if (!nx.pcs.includes(x.land % 12)) bad.push(`${k.name}: lands off the next chord`);
      if (x.land < 36 || x.land > 96) bad.push(`${k.name}: ${x.land} off the keyboard`);
      if (from !== null && Math.abs(x.land - from) > 4) bad.push(`${k.name}: ${from} -> ${x.land} is a reach`);
      if (JSON.stringify(x.keep) !== JSON.stringify(c.pcs.filter((pc) => nx.pcs.includes(pc)))) bad.push('keep is not the shared notes');
      if (!x.why) bad.push('no reason');
      const again = crossing(c.pcs, nx.pcs, from, { low: 36, high: 96 });
      if (again.land !== x.land) bad.push('not the same answer twice');
      c = nx;
    }
  }
  ok('every change lands on the next chord, within 4 keys of your hand, the same every time', !bad.length,
     bad.length ? bad.slice(0, 3).join('; ') : `${n} changes, ${keys.length} keys`);
  const C = [0, 4, 7], F = [5, 9, 0], G = [7, 11, 2], Dm = [2, 5, 9];
  const cf = crossing(C, F, 64);
  ok('C to F from E: step up to F, not a reach down to the shared C', cf.land === 65, `${cf.land} ${cf.why}`);
  const cg = crossing(C, G, 67);
  ok('C to G from G: hold the G, it is in both', cg.land === 67 && /both/.test(cg.why), `${cg.land} ${cg.why}`);
  const cd = crossing(C, Dm, 60);
  ok('C to Dm (nothing shared) from C: one step to D', cd.land === 62 && !cd.keep.length, `${cd.land}`);
  const first = crossing(C, G, null);
  ok('before you have played, it lands near the middle of the keyboard', first.land >= 62 && first.land <= 70, `${first.land}`);
}

/* 7. Minor V really is major (leading tone), and v really is minor. */
{
  const am = keys.find((k) => k.name === 'A natural_minor');
  ok('minor V is E G# B and v is E G B', JSON.stringify(chordPcs(am.pcs, 'V')) === '[4,8,11]'
     && JSON.stringify(chordPcs(am.pcs, 'v')) === '[4,7,11]', `${chordPcs(am.pcs, 'V')}`);
}

console.log(JSON.stringify(out));
"""


# ── the band, against a recording sequencer ────────────────────────────────
class FakeFS:
    def __init__(self) -> None:
        self.log: list[tuple] = []

    def program_select(self, ch, sfid, bank, prog):
        self.log.append(("prog", ch, bank, prog))
        return 0

    def cc(self, ch, c, v):
        self.log.append(("cc", ch, c, v))


class FakeSeq:
    def __init__(self) -> None:
        self.tick = 1000
        self.notes: list[tuple] = []
        self.sequencer = None

    def get_tick(self):
        return self.tick

    def register_client(self, name, cb):
        return 7

    def note(self, at, ch, key, vel, dur, source=-1, dest=-1):
        self.notes.append((at, ch, key, vel, dur, source))

    def timer(self, at, data=None, source=-1, dest=-1):
        pass


def band_checks() -> None:
    removed: list[tuple] = []
    band_mod._remove_events = lambda *a: removed.append(a)   # no real sequencer here

    fs, seq = FakeFS(), FakeSeq()
    eng = SimpleNamespace(fs=fs, sequencer=seq, seq_dest=1, active_channels=(0, 1),
                          load_soundfont=lambda name: 1)
    C = {"root": 0, "pcs": [0, 4, 7]}
    G = {"root": 7, "pcs": [7, 11, 2]}
    Am = {"root": 9, "pcs": [9, 0, 4]}
    F = {"root": 5, "pcs": [5, 9, 0]}

    def fresh(style="pop", chart=None, bpm=120):
        b = band_mod.Band(eng)
        b._thread = SimpleNamespace(is_alive=lambda: True)    # we drive _fill() ourselves
        seq.notes.clear()
        fs.log.clear()
        seq.tick = 1000
        b.start(chart or [C, G, Am, F] * 2, style=style, bpm=bpm)
        return b

    b = fresh()
    b._fill()
    ch = b._channels
    ok("the band takes three channels no zone is using",
       len(set(ch.values())) == 3 and not set(ch.values()) & {0, 1} and all(1 <= c <= 8 for c in ch.values()),
       str(ch))
    ok("the drums are on a drum kit", ("prog", ch["drums"], 128, 0) in fs.log)
    bar_ms = b.bar_ms()
    t0 = b._anchor_tick
    bar0 = [n for n in seq.notes if t0 <= n[0] < t0 + bar_ms]
    ok("bar 0 has drums, bass and chords", {n[1] for n in bar0} == set(ch.values()), f"{len(bar0)} notes")
    bass = [n for n in bar0 if n[1] == ch["bass"]]
    lo, hi = band_mod.BASS_RANGE
    ok("the bass plays C's root and fifth, low", bass and all(n[2] % 12 in (0, 7, 11, 6) for n in bass)
       and all(lo - 12 <= n[2] <= hi + 12 for n in bass), str([n[2] for n in bass]))
    ok("the bass lands on the root on beat one", any(n[0] == int(round(t0)) and n[2] % 12 == 0 for n in bass))
    comp = [n for n in bar0 if n[1] == ch["comp"]]
    ok("the chords are C E G, under the melody", comp and all(n[2] % 12 in (0, 4, 7) for n in comp)
       and all(52 <= n[2] <= 63 for n in comp), str(sorted({n[2] for n in comp})))
    ok("every note is a real MIDI note at a real velocity",
       all(0 <= n[2] <= 127 and 1 <= n[3] <= 127 and n[4] > 0 for n in seq.notes))
    ok("everything it schedules is tagged as the band's, so a flush removes only the band",
       all(n[5] == 7 for n in seq.notes))
    ok("only the lookahead is queued, not the whole song", b._next_bar <= 1, f"queued to bar {b._next_bar}")

    # Where we are.
    seq.tick = int(t0 + bar_ms * 1.5)
    ok("position() is the bar you hear, with its fraction", abs(b.position() - 1.5) < 1e-6, f"{b.position()}")
    seq.tick = int(t0 - 50)
    ok("before the first downbeat there is no position", b.position() is None)

    # Second bar is G.
    seq.tick = int(t0 + bar_ms * 0.9)
    b._fill()
    bar1 = [n for n in seq.notes if t0 + bar_ms <= n[0] < t0 + 2 * bar_ms and n[1] == ch["comp"]]
    ok("bar 1 plays the chart's second chord (G)", bar1 and all(n[2] % 12 in (7, 11, 2) for n in bar1))

    # Replacing the chart never rewrites a queued bar.
    at = b.replace(0, [F, F, F, F])
    ok("a change lands on the first bar not yet queued", at == b._next_bar and at >= 1, f"{at}")
    ok("...and the chart from there is the new one", b._chart[at]["pcs"] == [5, 9, 0])

    # A tempo change re-anchors without a jump.
    seq.tick = int(t0 + bar_ms * 1.2)
    before = b.position()
    b.configure(bpm=60)
    ok("a tempo change does not make the position jump", abs(b.position() - before) < 1e-6,
       f"{before} -> {b.position()}")
    ok("...and takes the new bar length", abs(b.bar_ms() - 4000) < 1e-6)
    seq.tick = int(b._anchor_tick + 2000)
    ok("...from the bar it lands on: half a 4-second bar later is half a bar on",
       abs(b.position() - (b._anchor_bar + 0.5)) < 1e-3, f"{b.position()}")

    # Stop.
    removed.clear()
    ok("stop says it was playing and flushes the band's queue", b.stop() and removed and removed[0][1] == 7)
    ok("stopping again says nothing was playing", b.stop() is False)

    # Dead man's switch.
    b = fresh(chart=[C, G])
    for i in range(40):
        seq.tick = int(b._anchor_tick + i * b.bar_ms())
        b._fill()
    ok("with no one topping up the chart, the band stops by itself",
       not b._running and b._next_bar == 2 + band_mod.DRY_BARS, f"stopped at bar {b._next_bar}")

    # Every style plays and stays sane; lo-fi swings.
    for style in band_mod.STYLES:
        b = fresh(style=style, bpm=band_mod.STYLES[style]["bpm"])
        for i in range(4):
            seq.tick = int(b._anchor_tick + b.bar_ms() * i - 100)   # just before each bar
            b._fill()
        notes = list(seq.notes)
        ok(f"{style}: four bars, every part, sane notes",
           b._next_bar >= 4 and {n[1] for n in notes} == set(b._channels.values())
           and all(0 <= n[2] <= 127 and 1 <= n[3] <= 127 for n in notes), f"{len(notes)} notes")
    b = fresh(style="lofi", bpm=80)
    b._fill()
    six = b.bar_ms() / 16
    hats = sorted(n[0] - b._anchor_tick for n in seq.notes if n[1] == b._channels["drums"] and n[2] == band_mod.HAT)
    ok("lo-fi swings its off-beat eighths late", any(abs(h - 2 * six) > 5 and h < 4 * six for h in hats),
       str([round(h) for h in hats[:3]]))

    eng.fs = None
    try:
        band_mod.Band(eng).start([C])
        ok("no engine is an error, not a crash", False)
    except RuntimeError:
        ok("no engine is an error, not a crash", True)
    eng.fs = fs


def source_checks() -> None:
    src = (FRONTEND / "jam.js").read_text(encoding="utf-8")
    for key, mode in KEYS:
        if f"key: '{key}', mode: '{mode}'" not in src:
            ok("jam.js offers the keys this check covers", False, f"{key} {mode} missing")
            return
    ok("jam.js offers the keys this check covers", True, f"{len(KEYS)} keys")
    styles = [line.split("'")[1] for line in src.splitlines() if line.strip().startswith("{ id: '") and "bpm:" in line]
    ok("jam.js offers exactly the band's styles", sorted(styles) == sorted(band_mod.STYLES), str(styles))
    server = (ROOT / "backend" / "server.py").read_text(encoding="utf-8")
    ok("panic stops the band", "self.band.stop()" in server)
    ok("shutdown stops the band", "self.band.shutdown()" in server)


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

    driver = FRONTEND / "_jam_check.mjs"
    driver.write_text(DRIVER, encoding="utf-8")
    try:
        proc = subprocess.run([node, str(driver)], capture_output=True, text=True,
                              env={**__import__("os").environ, "JAM_KEYS": json.dumps(keys)})
    finally:
        driver.unlink(missing_ok=True)
    if proc.returncode != 0:
        print("the driver did not run:")
        print(proc.stderr.strip() or proc.stdout.strip())
        return 1
    for r in json.loads(proc.stdout.strip().splitlines()[-1]):
        ok(r["label"], r["passed"], r.get("detail", ""))

    band_checks()
    source_checks()

    passed = all(p for _, p, _ in results)
    for label, p, detail in results:
        print(f"  [{'PASS' if p else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))
    print()
    print(f"{'ALL CHECKS PASSED' if passed else 'FAILED'}  ({len(results)} assertions)")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
