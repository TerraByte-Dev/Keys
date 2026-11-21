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

Lights Out (the Guide setting) is checked the same way: the level over every chord
the composer plays falls one step per time through a progression -- the Canon's single
pass included -- and comes back to full on a new key or a new progression.

The seats (sitting in as the bass or the chords player) light the right keys on every
keyboard size: the chords seat the chord's whole set in every octave of its window --
never one shape to copy, which Tate turned down in 0.10.0 -- and the bass seat the root
and its fifth, low, crossing root to root.

The band is checked against a recording sequencer: it keeps off the zones' channels,
plays the chart's chords in the right registers, never rewrites a queued bar, keeps its
place through a tempo change, and stops by itself when nothing tops up its chart. A
part switched off for a seat plays nothing at all, leaves the others exactly as they
were, and goes quiet from the first bar not already queued.
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
sys.path.insert(0, str(ROOT))
# The band endpoints' sanitiser is checked by importing the server, which opens the
# practice database: point the data directory at a throwaway one first, so this check
# never writes a keys.db of its own -- or touches yours.
SANDBOX = tempfile.mkdtemp(prefix="keys-jamcheck-")
os.environ["KEYS_DATA_DIR"] = SANDBOX

from backend import band as band_mod, theory   # noqa: E402

# Must match KEYS in frontend/jam.js -- checked below by reading that file.
KEYS = [("C", "major"), ("A", "natural_minor"), ("G", "major"), ("E", "natural_minor"),
        ("F", "major"), ("D", "natural_minor"), ("D", "major"), ("B", "natural_minor"),
        ("Bb", "major"), ("G", "natural_minor"), ("F#", "major"), ("Eb", "natural_minor"),
        ("Db", "major"), ("Bb", "natural_minor")]

results: list[tuple[str, bool, str]] = []


def ok(label: str, passed: bool, detail: str = "") -> None:
    results.append((label, bool(passed), detail))


DRIVER = r"""
import { createProgression, crossing, guideLevel, keysIn, palette, seatTarget, seatZone, PALETTE, PHRASES, chordPcs,
  ROOMS, roomScale, roomSig, createVamp, BLUES_FORMS, bluesChord, bluesNote, bluesScale, createBlues,
  lick, question, createAsks, echoScore, pinSafe, wanderDue, BLUES_LEVELS, SAFE_PHRASES,
  createLoop, loopRole, loopRoman, loopScale, spellRoot, spellUp, keyMoves, fitOf } from './harmony.js';

const keys = JSON.parse(process.env.JAM_KEYS);
const served = JSON.parse(process.env.JAM_SERVED);
const allKeys = JSON.parse(process.env.JAM_ALL_KEYS);
const rooms = JSON.parse(process.env.JAM_ROOMS);
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

/* 8. Lights Out. The level, by times through a progression since the lights came back. */
{
  const row = (g) => [0, 1, 2, 3, 4, 9].map((t) => guideLevel(t, g)).join('');
  ok('Full is always level 0, Dark always 3, Fading 0 1 2 3 then stays dark',
     row('full') === '000000' && row('dark') === '333333' && row('fading') === '012333',
     `${row('full')} ${row('dark')} ${row('fading')}`);

  // jam.js's counter, driven over real progressions: +1 at each phrase start (step 0),
  // back to -1 on a new key or a new progression. The level may only ever go down one
  // step at a time, and a reset must bring the very next chord back to full.
  let skips = 0, chords = 0, canons = 0, resets = 0, resetOk = 0;
  for (const k of keys.slice(0, 4)) {
    const mode = k.mode === 'major' ? 'major' : 'minor';
    for (const seed of [2, 8, 77]) {
      const rng = seeded(seed);
      let p = createProgression({ mode, scale: k.pcs, rng });
      let times = -1, prev = 0, prevChord = null;
      for (let i = 0; i < 400; i++) {
        if (i % 97 === 96) {                      // a wander, a key change or New progression
          if (rng() < 0.5) p = createProgression({ mode, scale: k.pcs, rng });
          else p.skipPhrase();
          times = -1; prev = 0; resets++;
          const c = p.next();
          if (c.step === 0) times++;
          if (guideLevel(Math.max(0, times), 'fading') === 0) resetOk++;
          prevChord = c;
          continue;
        }
        const c = p.next();
        if (c.step === 0) times++;
        const lv = guideLevel(Math.max(0, times), 'fading');
        chords++;
        if (lv - prev > 1 || lv < prev) skips++;
        if (c.phrase.id === 'canon' && c.step === 0) canons++;
        prev = lv;
        prevChord = c;
      }
    }
  }
  // The Canon is in there: played once, keyed on pass it would skip a level.
  ok('Fading goes down one level per time through, never skipping one (the Canon included)',
     !skips && canons > 0, `${skips} jumps in ${chords} chords, ${canons} Canons`);
  ok('a new key or a new progression brings the lights back on its first chord',
     resets > 0 && resetOk === resets, `${resetOk} of ${resets}`);
}

/* 9. Sitting in, on keyboards from 88 keys down to 25 (and one that starts at middle C). */
{
  const boards = [[21, 108], [28, 103], [36, 96], [36, 84], [48, 72], [60, 84]];
  let n = 0, chordBad = [], bassBad = [];
  const rnd = seeded(13);
  for (const k of keys) {
    const mode = k.mode === 'major' ? 'major' : 'minor';
    const p = createProgression({ mode, scale: k.pcs, rng: seeded(4) });
    let c = p.next();
    for (let i = 0; i < 100; i++) {
      const nx = p.next();
      const pal = palette(c.pcs, k.pcs), npal = palette(nx.pcs, k.pcs);
      for (const [lo, hi] of boards) {
        n++;
        const z = seatZone('chords', lo, hi);
        const lit = keysIn(seatTarget('chords', pal).tone, z);
        if (z.low < lo || z.high > hi || z.high - z.low < 11) chordBad.push(`${lo}-${hi}: window ${z.low}-${z.high}`);
        for (let m = z.low; m <= z.high; m++) {
          if (c.pcs.includes(m % 12) !== lit.includes(m)) chordBad.push(`${k.name} ${c.roman} on ${lo}-${hi}: key ${m}`);
        }
        // Two octaves of window means every chord note twice: a set to choose from.
        if (z.high - z.low >= 23 && !c.pcs.every((pc) => lit.filter((m) => m % 12 === pc).length >= 2)) {
          chordBad.push(`${k.name} ${c.roman}: a note lit only once`);
        }
        const bz = seatZone('bass', lo, hi);
        const bt = seatTarget('bass', pal);
        if (bz.low < lo || bz.high > hi || bz.high - bz.low < 11) bassBad.push(`${lo}-${hi}: register ${bz.low}-${bz.high}`);
        if (JSON.stringify(bt.tone) !== JSON.stringify([c.pcs[0]])
            || JSON.stringify(bt.color) !== JSON.stringify([(c.pcs[0] + 7) % 12])) bassBad.push(`${k.name} ${c.roman}: not root and fifth`);
        const from = rnd() < 0.2 ? null : bz.low + Math.floor(rnd() * (bz.high - bz.low + 1));
        const x = crossing(bt.tone, seatTarget('bass', npal).tone, from, bz);
        if (x.land % 12 !== nx.pcs[0] || x.land < bz.low || x.land > bz.high) bassBad.push(`${k.name}: lands ${x.land} for ${nx.roman}`);
      }
      c = nx;
    }
  }
  ok('the chords seat lights every key of the chord in its window, every octave, never one shape',
     !chordBad.length, chordBad.length ? chordBad.slice(0, 3).join('; ') : `${n} chords x keyboards, ${keys.length} keys`);
  ok('the bass seat lights the root and its fifth, low, and crosses root to root in its register',
     !bassBad.length, bassBad.length ? bassBad.slice(0, 3).join('; ') : `${n} changes`);
}

/* 7. The modal rooms: on the white keys, true to the server's modes, one ringed note. */
{
  const WHITE = [0, 2, 4, 5, 7, 9, 11];
  const SCALE = { major: [0, 2, 4, 5, 7, 9, 11], minor: [0, 2, 3, 5, 7, 8, 10], dorian: [0, 2, 3, 5, 7, 9, 10] };
  const AGAINST = { dorian: 'minor', phrygian: 'minor', aeolian: 'dorian', lydian: 'major', mixolydian: 'major' };
  const bad = [];
  const sorted = (xs) => JSON.stringify([...xs].sort((a, b) => a - b));
  for (const r of ROOMS) {
    const sc = roomScale(r);
    if (sorted(sc) !== sorted(rooms[r.id])) bad.push(`${r.id}: not the server's scale`);
    if (!sc.every((pc) => WHITE.includes(pc))) bad.push(`${r.id}: leaves the white keys`);
    const plain = SCALE[AGAINST[r.id]].map((s) => (r.tonic + s) % 12);
    const odd = sc.filter((pc) => !plain.includes(pc));
    if (odd.length !== 1 || odd[0] !== roomSig(r)) bad.push(`${r.id}: ringed ${roomSig(r)}, differs on ${odd}`);
    const v = createVamp(r);
    const a = v.next(), b = v.next(), c = v.next();
    if (a.pcs[0] !== r.tonic || a.role !== 'home') bad.push(`${r.id}: the vamp does not start at home`);
    if (sorted(c.pcs) !== sorted(a.pcs) || sorted(a.pcs) === sorted(b.pcs)) bad.push(`${r.id}: not two chords back and forth`);
    for (const x of [a, b]) {
      if (!x.pcs.every((pc) => sc.includes(pc)) || !sc.includes(x.bass)) bad.push(`${r.id}: ${x.roman} leaves the mode`);
    }
  }
  ok("every modal room is on the white keys, matches the server's mode, and rings the one note that makes it",
     !bad.length, bad.length ? bad.slice(0, 3).join('; ') : `${ROOMS.length} rooms`);
}

/* 8. The blues: twelve bars of seventh chords, judged so the blue note is colour. */
{
  const bad = [];
  let n = 0;
  for (const k of keys) {
    const minor = k.mode !== 'major';
    const tonic = k.pcs[0];
    const blue = bluesNote(tonic, minor);
    for (const f of BLUES_FORMS[minor ? 'minor' : 'major']) {
      if (f.seq.length !== 12 || !f.seq[11].startsWith('V') || !/^(I|i)7$/.test(f.seq[0])) bad.push(`${f.id}: not a 12-bar form`);
    }
    const g = createBlues({ tonic, minor, rng: seeded(tonic + 1) });
    for (let i = 0; i < 48; i++) {
      const c = g.next();
      n++;
      const sc = bluesScale(tonic, c.roman);
      const pal = palette(c.pcs, sc);
      if (c.pcs.length !== 4 || !sc.includes(c.pcs[0])) bad.push(`${k.name} ${c.roman}: not a seventh chord`);
      const all = [...pal.chord, ...pal.color, ...pal.rub].sort((a, b) => a - b);
      if (JSON.stringify(all) !== JSON.stringify(sc)) bad.push(`${k.name} ${c.roman}: palette is not the scale`);
      const home = /^(I|i)7$/.test(c.roman);
      if ((home || (!minor && c.roman === 'IV7')) && !pal.chord.concat(pal.color).includes(blue)) {
        bad.push(`${k.name} ${c.roman}: the blue note is not lit`);
      }
      if (!minor && c.roman === 'I7' && !pal.rub.includes((tonic + 5) % 12)) bad.push(`${k.name}: the 4th over I7 is not a rub`);
    }
  }
  ok('every blues chorus is twelve bars of 7th chords, and the blue note is lit over home (and IV)',
     !bad.length, bad.length ? bad.slice(0, 3).join('; ') : `${n} chords, ${keys.length} keys`);
}

/* 9. The band's melody: licks and questions are singable and say what they should. */
{
  const lickBad = [], qBad = [];
  let licks = 0, qs = 0;
  for (const k of keys) {
    const mode = k.mode === 'major' ? 'major' : 'minor';
    const rnd = seeded(k.pcs[0] * 31 + 7);
    const prog = createProgression({ mode, scale: k.pcs, rng: rnd });
    for (let i = 0; i < 60; i++) {
      const c = prog.next();
      const pal = palette(c.pcs, k.pcs);
      const size = 2 + (i % 5);
      const l = lick({ pal, size, rng: rnd });
      licks++;
      const fits = pal.chord.concat(pal.color);
      const where = `${k.name} ${c.roman} x${size}`;
      if (l.length !== size) lickBad.push(`${where}: ${l.length} notes`);
      if (!l.every((x, j) => x.step >= 0 && x.len >= 1 && (j === 0 || x.step > l[j - 1].step))) lickBad.push(`${where}: steps out of order`);
      if (l[l.length - 1].step + l[l.length - 1].len > 16) lickBad.push(`${where}: runs past the bar`);
      if (!l.every((x) => x.key >= 64 && x.key <= 81)) lickBad.push(`${where}: out of range`);
      if (!l.every((x) => fits.includes(x.key % 12))) lickBad.push(`${where}: a note that does not sound good`);
      if (!pal.chord.includes(l[0].key % 12) || !pal.chord.includes(l[l.length - 1].key % 12)) lickBad.push(`${where}: does not start and end on the chord`);
      if (l.some((x, j) => j && (x.key === l[j - 1].key || Math.abs(x.key - l[j - 1].key) > (j === l.length - 1 ? 7 : 5)))) lickBad.push(`${where}: a repeat or a leap`);
    }
    const asks = createAsks({ mode, scale: k.pcs, rng: rnd });
    const home = [k.pcs[0], k.pcs[2], k.pcs[4]];
    for (let i = 0; i < 20; i++) {
      const four = [asks.next(), asks.next(), asks.next(), asks.next()];
      qs++;
      const where = `${k.name} ${four.map((c) => c.roman).join('-')}`;
      if (four.map((c) => c.turn).join() !== 'call,call,you,you' || new Set(four.map((c) => c.ask)).size !== 1) qBad.push(`${where}: turns`);
      if (four[3].roman !== PALETTE[mode][0]) qBad.push(`${where}: the answer does not end at home`);
      const sc = (c) => { const s2 = k.pcs.slice(); if (c.roman === 'V' && mode === 'minor') s2[6] = c.pcs[1]; return s2; };
      const p1 = palette(four[0].pcs, sc(four[0])), p2 = palette(four[1].pcs, sc(four[1]));
      const q = question({ pals: [p1, p2], home, rng: rnd });
      const one = q.filter((x) => x.step < 16), two = q.filter((x) => x.step >= 16);
      const last = q[q.length - 1];
      if (!one.length || !two.length) qBad.push(`${where}: a bar with no notes`);
      if (last.step + last.len > 30) qBad.push(`${where}: no breath before your turn`);
      if (!one.every((x) => p1.chord.concat(p1.color).includes(x.key % 12))) qBad.push(`${where}: bar 1 note does not fit`);
      if (!two.slice(0, -1).every((x) => p2.chord.concat(p2.color).includes(x.key % 12))) qBad.push(`${where}: bar 2 note does not fit`);
      const open = p2.chord.filter((pc) => !home.includes(pc));
      if (!p2.chord.includes(last.key % 12) || (open.length && !open.includes(last.key % 12))) qBad.push(`${where}: ends on ${last.key % 12}, not left hanging`);
    }
  }
  ok('every lick is in the bar, in range, all notes that fit, starting and ending on the chord, no leaps',
     !lickBad.length, lickBad.length ? lickBad.slice(0, 3).join('; ') : `${licks} licks`);
  ok('every question is two bars that fit, left hanging off home, with the answer ending at home',
     !qBad.length, qBad.length ? qBad.slice(0, 3).join('; ') : `${qs} questions`);
  const e = (a, b) => echoScore(a, b);
  ok('an echo is scored by the notes in order, any octave', e([60, 62, 64], [72, 74, 76]).exact
     && e([60, 62, 64], [60, 64]).matched === 2 && !e([60, 62, 64], [60, 64]).exact
     && e([60, 62, 64], [60, 62, 64, 65]).matched === 3 && !e([60, 62, 64], [60, 62, 64, 65]).exact
     && e([60, 62], []).matched === 0 && e([64, 60], [60, 64]).matched === 1);
}

/* 10. Levels. The beginner loops are the spec's, written here, not read from harmony.js. */
const DEG = { I: 0, ii: 1, iii: 2, IV: 3, V: 4, vi: 5, i: 0, III: 2, iv: 3, v: 4, VI: 5, VII: 6 };
const BEGIN = { major: ['pop', 'sad-pop', 'fifties', 'three-chord'], minor: ['epic', 'andalusian', 'watchtower', 'minor-three'] };
{
  const bad = [];
  let skips = 0;
  for (const k of keys) {
    const mode = k.mode === 'major' ? 'major' : 'minor';
    for (const seed of [1, 5, 23]) {
      const p = createProgression({ mode, scale: k.pcs, rng: seeded(seed), level: 1 });
      const first = p.next();
      if (first.roman !== PALETTE[mode][0]) bad.push(`${k.name}: opens on ${first.roman}`);
      for (let round = 0; round < 4; round++) {
        const ids = new Set();
        const c0 = round ? p.next() : first;
        ids.add(c0.phrase.id);
        for (let i = 0; i < 59; i++) {
          const c = p.next();
          ids.add(c.phrase.id);
          if (c.varied) bad.push(`${k.name}: L1 varied a chord`);
          if (c.phrase.seq.length !== 4) bad.push(`${k.name}: L1 played ${c.phrase.id}`);
        }
        const [id] = [...ids];
        if (ids.size !== 1) bad.push(`${k.name}: L1 played ${[...ids]} without New progression`);
        if (!BEGIN[mode].includes(id)) bad.push(`${k.name}: ${id} is not a beginner loop`);
        p.skipPhrase();
        // The rest of the bar's loop is dropped: the next chord is a new loop's first.
        const nx = p.peek();
        if (nx.phrase.id === id || nx.step !== 0) bad.push(`${k.name}: New progression kept ${id}`);
        else skips++;
      }
    }
  }
  ok('level 1: one beginner loop, over and over, unvaried, opening on home, until New progression picks another',
     !bad.length, bad.length ? bad.slice(0, 3).join('; ') : `${skips} new progressions`);
}
{
  const bad = [];
  const seen = { major: new Set(), minor: new Set() };
  for (const k of keys) {
    const mode = k.mode === 'major' ? 'major' : 'minor';
    const p = createProgression({ mode, scale: k.pcs, rng: seeded(k.pcs[0] + 3), level: 2 });
    for (let i = 0; i < 800; i++) {
      const c = p.next();
      seen[mode].add(c.phrase.id);
      if (c.phrase.seq.length !== 4 || c.step > 3) bad.push(`${k.name}: L2 played ${c.phrase.id}`);
      if (c.varied) bad.push(`${k.name}: L2 varied a chord`);
    }
  }
  const fours = (m) => PHRASES[m].filter((ph) => ph.seq.length === 4).map((ph) => ph.id);
  const missing = ['major', 'minor'].flatMap((m) => fours(m).filter((id) => !seen[m].has(id)));
  ok('level 2: every four-chord loop and nothing longer, none varied', !bad.length && !missing.length
     && !seen.major.has('canon'), bad.length ? bad.slice(0, 3).join('; ') : `missing ${missing}`);
}
{
  // Level 4: a key change at every second phrase boundary, never on a coin. jam.js's
  // counter: +1 at each phrase start, back to 0 on a change.
  const boundary = (level, on, n, rng) => {
    const at = [];
    let count = 0;
    for (let b = 1; b <= n; b++) {
      count++;
      if (wanderDue(level, on, count, rng)) { at.push(b); count = 0; }
    }
    return at;
  };
  const four = boundary(4, false, 20, seeded(3));
  const want = [2, 4, 6, 8, 10, 12, 14, 16, 18, 20];
  const one = boundary(1, true, 200, seeded(3));
  const off = boundary(3, false, 200, seeded(3));
  const three = boundary(3, true, 200, seeded(3));
  ok('level 4 changes key at every second phrase boundary; level 1 and Wander off never do',
     JSON.stringify(four) === JSON.stringify(want) && !one.length && !off.length,
     `L4 ${four}; L1 ${one.length}; off ${off.length}`);
  ok('...and levels 2-3 with Wander on still wait three phrases and toss a coin',
     three.length > 5 && three.every((b, i) => b - (three[i - 1] || 0) >= 3)
     && three.some((b, i) => b - (three[i - 1] || 0) > 3), `${three.slice(0, 8)}`);
}

/* 11. The scale pin. Every key, both modes; the pin's notes and the rub test are written
       here as arithmetic, never taken from harmony.js. */
const SHAPES = { major_pentatonic: [0, 2, 4, 7, 9], minor_pentatonic: [0, 3, 5, 7, 10], blues: [0, 3, 5, 6, 7, 10] };
const at = (tonic, steps) => steps.map((s) => (tonic + s) % 12);
function pinOf(kind, mode, tonic) {
  if (kind === 'penta') return { pcs: at(tonic, SHAPES[mode === 'major' ? 'major_pentatonic' : 'minor_pentatonic']), blue: null };
  // Blues: on the tonic in minor; in major the relative minor's, whose b5 is the major's b3.
  return mode === 'major' ? { pcs: at(tonic + 9, SHAPES.blues), blue: (tonic + 3) % 12 }
    : { pcs: at(tonic, SHAPES.blues), blue: (tonic + 6) % 12 };
}
const rubs = (chord, pin, blue) => pin.filter((pc) => pc !== blue && !chord.includes(pc)
  && chord.includes((pc + 11) % 12));
{
  const bad = [];
  let n = 0;
  const loops = {};
  const safeSets = { major: new Set(), minor: new Set() };
  for (const k of allKeys) {
    const mode = k.mode === 'major' ? 'major' : 'minor';
    const tonic = k.pcs[0];
    for (const kind of ['penta', 'blues']) {
      const { pcs: pin, blue } = pinOf(kind, mode, tonic);
      const allowed = pinSafe(k.pcs, mode, pin, blue);
      safeSets[mode].add(allowed.map((r) => r).join(' '));
      for (const level of [1, 2, 3, 4]) {
        const p = createProgression({ mode, scale: k.pcs, rng: seeded(tonic * 13 + level), level, allowed });
        const ids = new Set();
        const count = level === 3 ? 2000 : 600;
        for (let i = 0; i < count; i++) {
          const c = p.next();
          n++;
          ids.add(c.phrase.id);
          if (i === 0 && c.roman !== PALETTE[mode][0]) bad.push(`${k.name} ${kind} L${level}: opens on ${c.roman}`);
          const r = rubs(c.pcs, pin, blue);
          if (r.length) bad.push(`${k.name} ${kind} L${level}: ${c.roman} rubs on ${r}`);
          const want = k.chords[DEG[c.roman]];
          if (JSON.stringify(c.pcs) !== JSON.stringify(want)) bad.push(`${k.name} ${c.roman}: ${c.pcs} is not the server's ${want}`);
          if (level === 1 && i % 50 === 49) p.skipPhrase();
        }
        const key = `${mode} L${level}`;
        loops[key] = Math.min(loops[key] ?? 99, ids.size);
      }
    }
  }
  ok('under a Pentatonic or Blues pin no chord rubs against the pin, in every key, both modes, every level',
     !bad.length, bad.length ? bad.slice(0, 3).join('; ') : `${n} chords, ${allKeys.length} keys`);
  ok('...and every chord is still the server\'s chord on its degree (the safe phrases included)', !bad.some((b) => b.includes('server')));
  ok('major pentatonic allows exactly I ii IV vi; minor pentatonic i III iv VI (computed, every key)',
     [...safeSets.major].join('|') === 'I ii IV vi' && [...safeSets.minor].join('|') === 'i III iv VI',
     `${[...safeSets.major]} / ${[...safeSets.minor]}`);
  const thin = Object.entries(loops).filter(([key, v]) => !key.endsWith('L4') && v < 3);
  ok('levels 1-3 have at least three loops to play under a pin, in both modes', !thin.length,
     JSON.stringify(loops));
  const safeIds = ['major', 'minor'].flatMap((m) => SAFE_PHRASES[m].map((ph) => `${m}:${ph.id}:${ph.seq[0]}`));
  ok('every pin-safe phrase starts at home', safeIds.every((s) => /:(I|i)$/.test(s)), safeIds.join(' '));
}
{
  // An unplayable filter still gives a phrase: nothing in the songbook fits these chords.
  let crashed = '';
  try {
    for (const level of [1, 2, 3]) {
      const p = createProgression({ mode: 'major', scale: keys[0].pcs, rng: seeded(1), level, allowed: ['ii', 'iii'] });
      for (let i = 0; i < 20; i++) if (!p.next()) throw new Error('no chord');
    }
  } catch (err) { crashed = String(err); }
  ok('a pool with nothing that fits still yields a phrase, never a crash', !crashed, crashed);
}

/* 12. Blues by level. Chords by arithmetic: a dominant 7th is 0 4 7 10 on its root. */
{
  const bad = [];
  const dom = (r) => at(r, [0, 4, 7, 10]), min7 = (r) => at(r, [0, 3, 7, 10]);
  for (const k of keys) {
    const minor = k.mode !== 'major';
    const tonic = k.pcs[0];
    const distinct = (level) => {
      const g = createBlues({ tonic, minor, rng: seeded(tonic + level), level });
      const seqs = new Set(), chords = new Set();
      for (let i = 0; i < 96; i++) {
        const c = g.next();
        chords.add(c.pcs.join(','));
        seqs.add(c.phrase.seq.join(' '));
        const sc = bluesScale(tonic, c.roman, minor);
        if (!c.pcs.every((pc) => sc.includes(pc))) bad.push(`${k.name} L${level} ${c.roman}: chord outside its scale`);
      }
      return { chords, seqs: [...seqs] };
    };
    const one = distinct(1);
    const home = (minor ? min7 : dom)(tonic).join(','), four = (minor ? min7 : dom)(tonic + 5).join(',');
    if (one.chords.size !== 2 || !one.chords.has(home) || !one.chords.has(four)) bad.push(`${k.name} L1: ${[...one.chords]}`);
    if (one.seqs.length !== 1 || one.seqs[0].split(' ').length !== 8) bad.push(`${k.name} L1: not four bars each`);
    const two = distinct(2);
    const s2 = two.seqs[0]?.split(' ') || [];
    if (two.seqs.length !== 1 || s2.length !== 12 || s2[1] !== s2[0] || two.chords.size !== 3) bad.push(`${k.name} L2: ${two.seqs}`);
    const four4 = distinct(4);
    const s4 = four4.seqs[0]?.split(' ') || [];
    if (s4.length !== 12 || s4[1] === s4[0] || s4[11][0] !== 'V') bad.push(`${k.name} L4: ${four4.seqs}`);
    // The turnaround moves: a chord the plain twelve bars never play.
    if (![...four4.chords].some((c) => !two.chords.has(c))) bad.push(`${k.name} L4: nothing new in the turnaround`);
    if (!minor && !four4.chords.has(dom(tonic + 9).join(','))) bad.push(`${k.name} L4: no VI7 (A7 in C)`);
  }
  ok('blues level 1 is two chords, 2 the plain 12-bar on three, 4 the quick change with a moving turnaround',
     !bad.length, bad.slice(0, 3).join('; '));
}

/* 13. My chords: the loop cycles, and each chord gets a role, a numeral and a scale. */
{
  const C = [0, 2, 4, 5, 7, 9, 11];
  const mine = [{ roman: 'I', pcs: [0, 4, 7], root: 0, role: 'home' }, { roman: 'II7', pcs: [2, 6, 9, 0], root: 2, role: 'pull' },
    { roman: 'IV', pcs: [5, 9, 0], root: 5, role: 'away' }];
  const g = createLoop(mine);
  const got = Array.from({ length: 7 }, () => g.next().roman).join(' ');
  ok('your loop plays your chords in order, round and round', got === 'I II7 IV I II7 IV I', got);
  // The roles the progressions give the same chords (ROLE, BLUES_ROLE), written out here:
  // C major then A minor, as [pcs, quality, role].
  const Am = [9, 11, 0, 2, 4, 5, 7];
  const roleCases = [
    [C, [0, 4, 7], '', 'home'], [C, [7, 11, 2], '', 'pull'], [C, [7, 11, 2, 5], '7', 'pull'],
    [C, [9, 0, 4], 'm', 'home'], [C, [4, 7, 11], 'm', 'home'], [C, [5, 9, 0], '', 'away'],
    [C, [5, 9, 0, 3], '7', 'away'], [C, [2, 6, 9, 0], '7', 'away'], [C, [2, 5, 9], 'm', 'away'],
    [C, [11, 2, 5], 'dim', 'pull'], [C, [10, 2, 5], '', 'away'],
    [Am, [9, 0, 4], 'm', 'home'], [Am, [7, 11, 2], '', 'pull'], [Am, [4, 8, 11], '', 'pull'],
    [Am, [4, 8, 11, 2], '7', 'pull'], [Am, [4, 7, 11], 'm', 'pull'], [Am, [0, 4, 7], '', 'away'],
    [Am, [5, 9, 0], '', 'away'], [Am, [2, 5, 9], 'm', 'away'], [Am, [8, 11, 2], 'dim', 'pull'],
  ];
  const roleBad = roleCases.filter(([sc, pcs, q, want]) => loopRole(pcs, q, sc) !== want)
    .map(([sc, pcs, q, want]) => `${pcs}${q} in ${sc[0]}: ${loopRole(pcs, q, sc)}, want ${want}`);
  ok('roles match the progressions\': iii and vi home in major, minor\'s VII pulls, IV7 and D7 in C are away',
     !roleBad.length, roleBad.join('; '));
  const romans = [[0, ''], [2, 'm7'], [7, '7'], [10, ''], [6, 'dim'], [3, 'maj7'], [1, 'dim'], [8, ''], [1, '']]
    .map(([r, q]) => loopRoman(r, q, C)).join(' ');
  ok('numerals by degree: I ii7 V7 ♭VII ♯iv° ♭IIImaj7 ♯i° ♭VI ♭II in C', romans === 'I ii7 V7 ♭VII ♯iv° ♭IIImaj7 ♯i° ♭VI ♭II', romans);
  const minorRomans = [[8, 'dim'], [8, ''], [6, 'm'], [10, ''], [4, '7'], [1, 'dim'], [7, '']]
    .map(([r, q]) => loopRoman(r, q, Am)).join(' ');
  ok('...and in A minor: ♯vii° ♯VII ♯vi ♭II V7 ♯iii° VII -- never ♭i°', minorRomans === '♯vii° ♯VII ♯vi ♭II V7 ♯iii° VII',
     minorRomans);
  const D7 = [2, 6, 9, 0], Dm = [2, 5, 9], Bb = [10, 2, 5];
  const choice = [loopScale(Dm, 'm', C), loopScale(D7, '7', C), loopScale(Bb, '', C), loopScale([3, 7, 10, 2], 'maj7', C),
    loopScale([6, 9, 0], 'dim', C), loopScale([4, 7, 11, 2], 'm7', [4, 6, 7, 9, 11, 0, 2]), loopScale([2, 7, 9], 'sus4', [1, 3, 5, 6, 8, 10, 0]),
    loopScale(D7, '7', C, true)].join(' ');
  ok('a chord in the key is judged by the key; out of it by its quality; under a pin by the pin',
     choice === 'key mixolydian major major locrian key mixolydian pin', choice);
  // Minor's V, V7 and leading-tone chord are its harmonic minor, as Minor music judges V.
  // Its V/V (B7 in A minor) is not: that one is still the chord's own Mixolydian.
  const harm = [loopScale([4, 8, 11], '', Am), loopScale([4, 8, 11, 2], '7', Am), loopScale([8, 11, 2], 'dim', Am),
    loopScale([11, 3, 6, 9], '7', Am), loopScale([7, 11, 2], '', Am)].join(' ');
  ok('in A minor, E and E7 and G♯° are judged by A harmonic minor; B7 by its own scale; G by the key',
     harm === 'harmonic harmonic harmonic mixolydian key', harm);
}

/* 14. Naming My chords' roots in a key, and spelling the scales they are judged by. */
const NAT = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };
const pcName = (n) => (NAT[n[0]] + (n.match(/#/g) || []).length - (n.slice(1).match(/b/g) || []).length + 120) % 12;
const rootNames = new Set();
{
  // Written out by hand: what a musician writes for these roots in these keys.
  const byName = Object.fromEntries(allKeys.map((k) => [k.name, k]));
  const name = (key, pc, q) => spellRoot(pc, q, byName[key].pcs, byName[key].roots);
  const cases = [
    ['E major', 6, '7', 'F#'], ['A natural_minor', 8, 'dim', 'G#'], ['A natural_minor', 6, 'm', 'F#'],
    ['A natural_minor', 10, '', 'Bb'], ['A natural_minor', 1, '', 'C#'], ['C major', 1, 'dim', 'C#'], ['C major', 1, '', 'Db'],
    ['C major', 8, '', 'Ab'], ['C major', 3, '', 'Eb'], ['C major', 10, '', 'Bb'], ['C major', 6, 'dim', 'F#'],
    ['C major', 6, '', 'F#'], ['G major', 5, '', 'F'], ['G major', 3, '', 'Eb'], ['F# major', 7, '', 'G'],
    ['Eb natural_minor', 2, 'dim', 'D'], ['Eb natural_minor', 0, 'm', 'C'], ['F major', 11, 'dim', 'B'],
    ['D natural_minor', 1, 'dim', 'C#'], ['E major', 8, 'm', 'G#'], ['B major', 10, '7', 'A#'],
  ];
  const bad = cases.map(([key, pc, q, want]) => [key, pc, q, want, name(key, pc, q)]).filter((c) => c[3] !== c[4]);
  ok('a root is named for the key: F♯7 in E, G♯° and F♯m in A minor, C♯° but D♭ in C, F in G',
     !bad.length, bad.map((c) => `${c[0]} pc ${c[1]}${c[2]}: ${c[4]}, want ${c[3]}`).join('; '));
  // Every root of every quality in every key: its own pitch, at most one accidental.
  const wrong = [];
  for (const k of allKeys) {
    for (let pc = 0; pc < 12; pc++) {
      for (const q of ['', 'm', '7', 'm7', 'maj7', 'sus4', 'dim']) {
        const n = spellRoot(pc, q, k.pcs, k.roots);
        rootNames.add(n);
        if (!/^[A-G](#|b)?$/.test(n) || pcName(n) !== pc) wrong.push(`${k.name} pc ${pc}${q}: ${n}`);
      }
    }
  }
  ok('...and every root in every key is its own pitch with at most one accidental', !wrong.length,
     wrong.slice(0, 4).join('; '));
  // The scale a chord outside the key is judged by, asked for by a name the server knows
  // (served, from Python) and spelled from the chord's own letter: D♯ dorian, G♯ mixolydian.
  const spellBad = [];
  let spelled = 0;
  for (const k of allKeys) {
    for (const root of k.roots) {
      for (const [mode, byPc] of Object.entries(served)) {
        const pcs = byPc[pcName(root)];
        const names = spellUp(root, pcs);
        spelled++;
        const letters = names.map((n) => n[0]).join('');
        const from = 'CDEFGABCDEFGAB'.indexOf(root[0]);
        if (names[0] !== root || letters !== 'CDEFGABCDEFGAB'.slice(from, from + 7)
            || names.some((n, i) => pcName(n) !== pcs[i])) spellBad.push(`${root} ${mode}: ${names.join(' ')}`);
      }
    }
  }
  ok('every key-row root\'s own scale is spelled from its letter, a letter a note, every pitch right',
     !spellBad.length && spelled === allKeys.length * 7 * Object.keys(served).length, spellBad.length ? spellBad.slice(0, 3).join('; ') : `${spelled} scales`);
}

/* 15. A note of a pinned scale the chord's scale leaves out is a rub, never praise --
       except the Blues pin's blue note, which is colour. Scales written out here. */
{
  const judge = (pc, chord, scale, pin, blue = null) => fitOf(pc, { pal: palette(chord, scale), scale, pin, blue });
  const cases = [
    // A minor, Key pin, over V (E G# B): its scale is the key with G#. G natural clashes.
    ['G over E in A minor, Key pin', judge(7, [4, 8, 11], [9, 11, 0, 2, 4, 5, 8], [9, 11, 0, 2, 4, 5, 7]), 'rub'],
    // C blues over IV7 (F A C Eb), its scale F mixolydian + C blues; Pentatonic pin C D E G A.
    ['E over F7 in the blues, Pentatonic pin', judge(4, [5, 9, 0, 3], [0, 2, 3, 5, 6, 7, 9, 10], [0, 2, 4, 7, 9]), 'rub'],
    // C blues over I7 (C E G Bb), Key pin: B against the chord's B flat.
    ['B over C7, Key pin', judge(11, [0, 4, 7, 10], [0, 2, 3, 4, 5, 6, 7, 9, 10], [0, 2, 4, 5, 7, 9, 11]), 'rub'],
    // C major, Blues pin (A blues: A C D Eb E G, blue Eb) over C: the blue note is colour.
    ['E♭ over C, Blues pin', judge(3, [0, 4, 7], [0, 2, 4, 5, 7, 9, 11], [9, 0, 2, 3, 4, 7], 3), 'color'],
    ['C♯ over C, no pin', judge(1, [0, 4, 7], [0, 2, 4, 5, 7, 9, 11], null), 'out'],
    ['F over C, no pin', judge(5, [0, 4, 7], [0, 2, 4, 5, 7, 9, 11], null), 'rub'],
    ['A over C, no pin', judge(9, [0, 4, 7], [0, 2, 4, 5, 7, 9, 11], null), 'color'],
  ];
  const bad = cases.filter(([, got, want]) => got !== want).map(([what, got, want]) => `${what}: ${got}, want ${want}`);
  ok('a pinned note outside the chord\'s scale is a rub; the blue note is colour; nothing pinned is out',
     !bad.length, bad.join('; '));
}

/* 16. Where a key change goes. The relative and the fifths, by arithmetic here. */
{
  const has = (pc, major) => keys.some((k) => k.pcs[0] === pc && (k.mode === 'major') === major);
  const id = (x) => `${x.pc}${x.major ? 'M' : 'm'}`;
  const bad = [];
  let stuck = [];
  for (const k of keys) {
    const major = k.mode === 'major', t = k.pcs[0];
    const rel = { pc: (t + (major ? 9 : 3)) % 12, major: !major };
    const fifths = [{ pc: (t + 7) % 12, major }, { pc: (t + 5) % 12, major }].filter((x) => has(x.pc, x.major));
    const four = keyMoves(t, major, 4, has).map(id).sort().join(' ');
    const want4 = (fifths.length ? fifths : [rel].filter((x) => has(x.pc, x.major))).map(id).sort().join(' ');
    if (four !== want4) bad.push(`${k.name} L4: ${four}, want ${want4}`);
    if (!fifths.length) stuck.push(k.name);
    const three = keyMoves(t, major, 3, has);
    const w = (x) => three.find((m) => id(m) === id(x))?.w;
    if (has(rel.pc, rel.major) && w(rel) !== 3) bad.push(`${k.name} L3: relative weighs ${w(rel)}`);
    if (fifths.some((x) => w(x) !== 1) || three.length !== fifths.length + (has(rel.pc, rel.major) ? 1 : 0)) bad.push(`${k.name} L3: ${three.map(id)}`);
  }
  ok('level 4 changes key by a fifth wherever one is offered; Wander keeps the relative at 3 to 1',
     !bad.length, bad.length ? bad.slice(0, 3).join('; ') : `relative only from ${stuck.join(', ') || 'nowhere'}`);
}

/* 17. jam.js's key-change clock over real level-4 progressions: compose() adds one at each
       phrase start, newGen() starts it at -1, a change sets it to 0 because the new key's
       first phrase is starting. Every key -- the first one too -- gets two phrases. */
{
  const bad = [];
  for (const k of keys.slice(0, 6)) {
    const mode = k.mode === 'major' ? 'major' : 'minor';
    for (const seed of [3, 9, 21]) {
      const rng = seeded(seed);
      let p = createProgression({ mode, scale: k.pcs, rng, level: 4 });
      let count = -1, phrases = 0;
      const per = [];
      for (let i = 0; i < 300; i++) {
        const up = p.peek();
        if (up.step === 0 && up.pass === 1) {
          count++;
          if (wanderDue(4, false, count)) {
            per.push(phrases);
            p = createProgression({ mode, scale: k.pcs, rng, level: 4 });
            count = 0;
            phrases = 0;
          }
          phrases++;
        }
        p.next();
      }
      if (per.length < 4 || per.some((n) => n !== 2)) bad.push(`${k.name} seed ${seed}: ${per}`);
    }
  }
  ok('level 4: every key plays two phrases before it changes, the opening key included', !bad.length,
     bad.slice(0, 3).join('; '));
}

console.log(JSON.stringify({ out, rootNames: [...rootNames] }));
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
        self.timers: list[int] = []
        self.sequencer = None

    def get_tick(self):
        return self.tick

    def register_client(self, name, cb):
        return 7

    def note(self, at, ch, key, vel, dur, source=-1, dest=-1):
        self.notes.append((at, ch, key, vel, dur, source))

    def timer(self, at, data=None, source=-1, dest=-1):
        self.timers.append(at)


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

    def fresh(style="pop", chart=None, bpm=120, **parts):
        b = band_mod.Band(eng)
        b._thread = SimpleNamespace(is_alive=lambda: True)    # we drive _fill() ourselves
        b._rng = random.Random(3)                             # the same velocities every run
        seq.notes.clear()
        fs.log.clear()
        seq.tick = 1000
        b.start(chart or [C, G, Am, F] * 2, style=style, bpm=bpm, **parts)
        return b

    b = fresh()
    b._fill()
    ch = b._channels
    ok("the band takes four channels no zone is using",
       len(set(ch.values())) == 4 and not set(ch.values()) & {0, 1} and all(1 <= c <= 8 for c in ch.values()),
       str(ch))
    ok("the drums are on a drum kit", ("prog", ch["drums"], 128, 0) in fs.log)
    bar_ms = b.bar_ms()
    t0 = b._anchor_tick
    bar0 = [n for n in seq.notes if t0 <= n[0] < t0 + bar_ms]
    ok("bar 0 has drums, bass and chords", {n[1] for n in bar0} == {ch["drums"], ch["bass"], ch["comp"]},
       f"{len(bar0)} notes")
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
           b._next_bar >= 4 and {n[1] for n in notes} == {c for p, c in b._channels.items() if p != "lead"}
           and all(0 <= n[2] <= 127 and 1 <= n[3] <= 127 for n in notes), f"{len(notes)} notes")
    # A seat: the part you sit in for plays nothing, and the others play exactly what they
    # would have. Velocities aside -- the muted part no longer draws from the jitter.
    def sounding(style, **parts):
        b = fresh(style=style, bpm=band_mod.STYLES[style]["bpm"], **parts)
        for i in range(4):
            seq.tick = int(b._anchor_tick + b.bar_ms() * i - 100)
            b._fill()
        return {part: [(n[0], n[2], n[4]) for n in seq.notes if n[1] == c] for part, c in b._channels.items()}

    for muted, kept in (("bass", ("drums", "comp")), ("comp", ("drums", "bass"))):
        bad = []
        for style in band_mod.STYLES:
            full, less = sounding(style), sounding(style, **{muted: False})
            if not full[muted] or less[muted]:
                bad.append(f"{style}: {len(less[muted])} {muted} notes")
            bad += [f"{style}: {p} changed" for p in kept if less[p] != full[p]]
        ok(f"{muted} off: not one note on its channel, the other parts unchanged, in every style",
           not bad, "; ".join(bad))

    # Switched off mid-play: the bars already queued play as written, then silence.
    b = fresh()
    b._fill()
    seq.tick = int(b._anchor_tick + b.bar_ms() * 0.9)
    b._fill()
    at = b._next_bar
    b.configure(bass=False)
    for i in range(1, 6):
        seq.tick = int(b._anchor_tick + b.bar_ms() * i - 100)
        b._fill()
    bars = sorted({int((n[0] - b._anchor_tick) // b.bar_ms()) for n in seq.notes if n[1] == b._channels["bass"]})
    drums = {int((n[0] - b._anchor_tick) // b.bar_ms()) for n in seq.notes if n[1] == b._channels["drums"]}
    ok("a part switched off mid-play goes quiet from the first bar not yet queued",
       at >= 1 and bars == list(range(at)) and drums >= set(range(5)), f"queued to {at}, bass in bars {bars}")
    ok("the band's status says which parts are playing",
       b.status()["bass"] is False and b.status()["comp"] is True and b.status()["drums"] is True)

    b = fresh(style="lofi", bpm=80)
    b._fill()
    six = b.bar_ms() / 16
    hats = sorted(n[0] - b._anchor_tick for n in seq.notes if n[1] == b._channels["drums"] and n[2] == band_mod.HAT)
    ok("lo-fi swings its off-beat eighths late", any(abs(h - 2 * six) > 5 and h < 4 * six for h in hats),
       str([round(h) for h in hats[:3]]))

    # The lead voice: the band's own melody, on its own channel, in every seat.
    lead = [[0, 4, 72, 84], [8, 4, 76, 90]]
    b = fresh(chart=[{**C, "lead": lead}, G, Am, F], bass=False, comp=False)
    for i in range(4):
        seq.tick = int(b._anchor_tick + b.bar_ms() * i - 100)
        b._fill()
    six = b.bar_ms() / 16
    mel = [(round((n[0] - b._anchor_tick) / six), n[2]) for n in seq.notes if n[1] == b._channels["lead"]]
    ok("a bar's melody plays on the lead channel, on its sixteenths, even with you in a seat",
       mel == [(0, 72), (8, 76)], str(mel))
    ok("...on a vibraphone", ("prog", b._channels["lead"], 0, band_mod.LEAD_PROGRAM) in fs.log)
    b = fresh(chart=[{**C, "lead": lead}])
    for i in range(4):
        seq.tick = int(b._anchor_tick + b.bar_ms() * i - 100)
        b._fill()
    bars = {int((n[0] - b._anchor_tick) // b.bar_ms()) for n in seq.notes if n[1] == b._channels["lead"]}
    ok("a chart run dry repeats its last chord, not its melody", bars == {0}, str(sorted(bars)))
    ok("a melody note out of bounds is clamped, not trusted",
       band_mod.Band._clean({"pcs": [0], "lead": [[99, 0, 300, 0]]})["lead"] == [(15, 1, 127, 84)])
    server = (ROOT / "backend" / "server.py").read_text(encoding="utf-8")
    ok("the server passes a bar's melody on", '"lead": [[int(x) for x in n][:4]' in server)

    # Few channels free: the lead shares the comp's channel and leaves its sound alone.
    eng.active_channels = (0, 1, 2, 3, 4, 5)
    b = fresh()
    b._fill()
    ch = b._channels
    ok("with only three channels free, the lead shares the comp's and does not turn it into a vibraphone",
       ch["lead"] == ch["comp"] and ("prog", ch["comp"], 0, band_mod.LEAD_PROGRAM) not in fs.log, str(ch))
    eng.active_channels = (0, 1)

    # The sixth the walking bass climbs to is the one the page sends: A flat over A minor.
    Am7 = {"root": 9, "pcs": [9, 0, 4], "six": 5}
    b = fresh(style="shuffle", chart=[Am7, Am7], bpm=84)
    b._fill()
    walk6 = [n[2] % 12 for n in sorted(seq.notes) if n[1] == b._channels["bass"]]
    ok("the shuffle's sixth is the key's own, not always a major sixth", walk6[3:4] == [5], str(walk6))

    # The shuffle walks the boogie line and swings on the triplet.
    C7 = {"root": 0, "pcs": [0, 4, 7, 10]}
    b = fresh(style="shuffle", chart=[C7, C7], bpm=84)
    b._fill()
    walk = [n[2] % 12 for n in sorted(seq.notes) if n[1] == b._channels["bass"]]
    ok("the shuffle's bass walks root, third, fifth, sixth", walk[:4] == [0, 4, 7, 9], str(walk))
    six = b.bar_ms() / 16
    rides = sorted(n[0] - b._anchor_tick for n in seq.notes if n[1] == b._channels["drums"] and n[2] == band_mod.RIDE)
    ok("...and its off-beats land on the last triplet", any(abs(r - 4 * 2 / 3 * six) < 3 for r in rides),
       str([round(r / six, 2) for r in rides[:3]]))

    # Sounds. Pop's own are piano (GM 0) over an electric bass (GM 33) -- written here from
    # the General MIDI table, not read from band.py.
    def progs(ch):
        return [e[3] for e in fs.log if e[0] == "prog" and e[1] == ch and e[2] == 0]

    def channel_of(notes, lo, hi):
        # The kit's keys share the bass's register: leave out the channel on the drum bank.
        kit = {e[1] for e in fs.log if e[0] == "prog" and e[2] == 128}
        chans = {n[1] for n in notes if lo <= n[2] <= hi and n[1] not in kit}
        return chans.pop() if len(chans) == 1 else None

    b = fresh(comp_program=48, bass_program=38)
    b._fill()
    comp_ch = channel_of(seq.notes, 52, 63)              # the comp's E3..D#4 window
    bass_ch = channel_of(seq.notes, 24, 50)
    ok("a band started with sounds picked plays them: strings on the chords' channel, synth bass on the bass's",
       comp_ch is not None and bass_ch is not None and progs(comp_ch)[-1:] == [48] and progs(bass_ch)[-1:] == [38],
       f"comp ch {comp_ch} {progs(comp_ch)}, bass ch {bass_ch} {progs(bass_ch)}")

    b = fresh()
    b._fill()
    comp_ch = channel_of(seq.notes, 52, 63)
    bass_ch = channel_of(seq.notes, 24, 50)
    ok("left alone, the style's own sounds: Pop is piano over electric bass",
       progs(comp_ch)[-1:] == [0] and progs(bass_ch)[-1:] == [33], f"{progs(comp_ch)} {progs(bass_ch)}")
    seq.tick = int(b._anchor_tick + b.bar_ms() * 0.9)
    b._fill()
    at = b._next_bar
    down = b._anchor_tick + at * b.bar_ms()            # the downbeat of the first unqueued bar
    fs.log.clear()
    seq.timers.clear()
    b.configure(comp_program=25)
    ok("a new sound is not put in while queued bars are still to play", 25 not in progs(comp_ch), str(progs(comp_ch)))
    seq.tick = int(down - 40)
    b._fill()
    early = 25 in progs(comp_ch)
    last_before = max(n[0] for n in seq.notes if n[0] < down)
    seq.tick = int(down - 20)
    b._fill()
    ok("...it lands just before the first unqueued bar's downbeat, after the last note of the bar before",
       not early and progs(comp_ch)[-1:] == [25] and last_before < down - 30 and any(down - 40 < t < down for t in seq.timers),
       f"bar {at}, timers {seq.timers}, last onset {round(down - last_before)} ms before")
    ok("...on the chords' channel only", not any(e[0] == "prog" and e[3] == 25 and e[1] != comp_ch for e in fs.log))
    b.configure(bass_program=32)
    b.configure(comp_program=None)
    for i in range(1, 6):
        seq.tick = int(b._anchor_tick + b.bar_ms() * (at + i) - 100)
        b._fill()
    ok("null puts the style's own sound back; a bass sound goes on the bass's channel",
       progs(comp_ch)[-1:] == [0] and progs(bass_ch)[-1:] == [32] and b.status()["comp_program"] is None
       and b.status()["bass_program"] == 32, f"{progs(comp_ch)} {progs(bass_ch)}")
    fs.log.clear()
    b.configure(style="rock")
    seq.tick = int(b._anchor_tick + b.bar_ms() * (b._next_bar + 1))
    b._fill()
    ok("a new style keeps a sound you picked, and brings its own where you did not",
       progs(bass_ch)[-1:] == [32] and progs(comp_ch)[-1:] == [29], f"{progs(comp_ch)} {progs(bass_ch)}")

    from backend import server
    refused = []
    for bad in (True, 1.5, "25", 128, -1, [0]):
        try:
            server._programs({"comp_program": bad})
        except server.HTTPException as err:
            refused.append(err.status_code)
    ok("the server takes a program 0-127 or null, refuses anything else, and leaves out what was not sent",
       server._programs({"comp_program": 0, "bass_program": None}) == {"comp_program": 0, "bass_program": None}
       and server._programs({"bass_program": 127}) == {"bass_program": 127} and server._programs({}) == {}
       and refused == [400] * 6, str(refused))

    eng.fs = None
    try:
        band_mod.Band(eng).start([C])
        ok("no engine is an error, not a crash", False)
    except RuntimeError:
        ok("no engine is an error, not a crash", True)
    eng.fs = fs


def served_scales() -> dict[str, dict[int, list[int]]]:
    """The scale My chords asks the server for, over a chord outside the key: one per
    pitch class and own mode, by the name harmony.js's SCALE_KEYS gives it -- read from
    the file, so a name the server refuses (G# mixolydian was a 400, and the band would
    not start) fails here. Also each minor key's harmonic minor, asked for by its own name."""
    src = (FRONTEND / "harmony.js").read_text(encoding="utf-8")
    names = re.search(r"export const SCALE_KEYS = \[([^\]]*)\]", src)
    own = re.search(r"export const OWN_MODE = \{([^}]*)\}", src)
    scale_keys = re.findall(r"'([^']*)'", names.group(1)) if names else []
    modes = sorted(set(re.findall(r":\s*'([a-z_]+)'", own.group(1)))) if own else []
    served: dict[str, dict[int, list[int]]] = {}
    refused = []
    for mode in modes:
        served[mode] = {}
        for pc, name in enumerate(scale_keys):
            try:
                plan = theory.scale_plan(name, mode)
            except (ValueError, KeyError) as err:
                refused.append(f"{name} {mode}: {err}")
                continue
            if plan["pcs"][0] != pc:
                refused.append(f"{name} {mode}: starts on {plan['pcs'][0]}, not {pc}")
            served[mode][pc] = plan["pcs"]
    for key, mode in KEYS:
        if mode == "natural_minor":
            try:
                theory.scale_plan(key, "harmonic_minor")
            except (ValueError, KeyError) as err:
                refused.append(f"{key} harmonic_minor: {err}")
    ok("the server gives every scale My chords asks for: every pitch class in every own mode, and harmonic minor",
       len(scale_keys) == 12 and len(modes) >= 4 and not refused,
       "; ".join(refused[:3]) or f"{len(modes)} modes x {len(scale_keys)} roots")
    return served


def chord_names_check(names: list[str]) -> None:
    """Every root name harmony.js spellRoot can give, in every key: the server must spell
    a chord of every kind on it, because that is the next request My chords makes."""
    refused = []
    for name in names:
        for quality in ("", "m", "7", "m7", "maj7", "sus4", "dim"):
            try:
                theory.chord_plan(name, quality)
            except (ValueError, KeyError) as err:
                refused.append(f"{name}{quality}: {err}")
    ok("the server spells a chord of every kind on every root name My chords can give",
       len(names) >= 12 and not refused, "; ".join(refused[:3]) or f"{len(names)} names")


def source_checks() -> None:
    src = (FRONTEND / "jam.js").read_text(encoding="utf-8")
    for key, mode in KEYS:
        if f"key: '{key}', mode: '{mode}'" not in src:
            ok("jam.js offers the keys this check covers", False, f"{key} {mode} missing")
            return
    ok("jam.js offers the keys this check covers", True, f"{len(KEYS)} keys")
    # Level 4 and Wander move by a fifth. Every key must have one on offer -- a fifth up
    # (+7) or down (+5) in the same mode -- or it falls back to its relative. The pitch
    # classes are counted here from the letter and its accidentals.
    letter = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
    pc = lambda name: (letter[name[0]] + name.count("#") - name[1:].count("b")) % 12  # noqa: E731
    offered = {(pc(key), mode) for key, mode in KEYS}
    stuck = [f"{key} {mode}" for key, mode in KEYS
             if ((pc(key) + 7) % 12, mode) not in offered and ((pc(key) + 5) % 12, mode) not in offered]
    ok("every key Jam offers has a key a fifth away to move to", not stuck, ", ".join(stuck) or f"{len(KEYS)} keys")
    # Pop is the major keys and Minor the minor ones; switching between them moves the key
    # to its relative (the same notes, the other home): a major key's is 9 semitones up, a
    # minor key's 3. Every key needs one on offer, or the switch keeps a key of the wrong mode.
    alone = [f"{key} {mode}" for key, mode in KEYS
             if ((pc(key) + (9 if mode == "major" else 3)) % 12, "natural_minor" if mode == "major" else "major")
             not in offered]
    ok("every key Jam offers has its relative on offer, for a switch between Pop and Minor", not alone,
       ", ".join(alone) or f"{len(KEYS)} keys")
    styles = [line.split("'")[1] for line in src.splitlines() if line.strip().startswith("{ id: '") and "bpm:" in line]
    ok("jam.js offers exactly the band's styles", sorted(styles) == sorted(band_mod.STYLES), str(styles))
    # Lights Out's counter lives in jam.js (the driver above runs the same rule): it
    # counts at each phrase start in compose() and goes back to "before the first" on
    # every road to a fresh progression.
    def body(name: str) -> str:
        m = re.search(r"\n  (?:async )?function " + name + r"\([^)]*\) \{\n(.*?)\n  \}\n", src, re.S)
        return m.group(1) if m else ""
    ok("compose() counts times through at each phrase start and stamps the level",
       "if (c.step === 0" in body("compose") and "times++;" in body("compose") and "guideLevel(" in body("compose"))
    missing = [f for f in ("wander", "setKey", "newPhrase", "start") if "times = -1;" not in body(f)]
    ok("a wander, a new key, New progression and Start each bring the lights back", not missing,
       f"no reset in {missing}" if missing else "")
    server = (ROOT / "backend" / "server.py").read_text(encoding="utf-8")
    ok("a seat reaches the band: jam.js sends the parts, the server passes them on, as with drums",
       "...parts()," in body("start") and "configure(parts())" in src
       and 'bass=bool(body.get("bass", True)), comp=bool(body.get("comp", True))' in server
       and 'bass=None if body.get("bass") is None else bool(body["bass"])' in server
       and 'comp=None if body.get("comp") is None else bool(body["comp"])' in server)
    # The pieces the driver above checks in harmony.js are the ones jam.js really uses.
    ok("compose() asks wanderDue() at each phrase start, with the level and the Wander switch",
       "wanderDue(opt.level, opt.wander, phrasesInKey)" in body("compose"))
    ok("Pop and Minor charts pass the level, and Solo the pin's safe chords; Blues passes the level",
       "level: opt.level,\n      allowed: m === 'songs' ? safeChords(plan) : null" in body("freshGen")
       and "createBlues({ tonic: pcOf(plan.key), minor: mode === 'minor', level: opt.level })" in body("freshGen"))
    ok("a note you play is judged by harmony.js fitOf, with the pin and its blue note (checked above)",
       "fitOf(pc, { pal: now.pal, scale: now.scale, sig: now.sig || [], pin: pin?.pcs, blue: pin?.blue })" in body("frame"))
    # The key-change clock the driver runs: -1 for a fresh stream, +1 at each phrase start,
    # 0 after a change (set after newGen(), which would put it back to -1).
    wander = body("wander")
    ok("jam.js counts phrases for a key change the way the driver does",
       "phrasesInKey = -1;" in body("newGen") and "phrasesInKey++;" in body("compose")
       and 0 <= wander.find("newGen();") < wander.find("phrasesInKey = 0;")
       and "neighbours(opt.key, opt.level)" in wander)
    ok("a neighbouring key and its pin are loaded with every key, pin and restart (ready), not only at mount",
       "await prefetchNeighbours()" in body("ready"))
    ok("Stop goes back to the key heard, saved, and a fresh progression (settle)",
       "settle();" in body("stop") and "e.wandered" in body("settle") and "save();" in body("settle")
       and "newGen();" in body("settle") and "if (moved) e.wandered = true;" in body("compose"))
    mine = body("loadMine")
    ok("My chords asks for a chord's own scale by a name the server knows, and falls back to the key",
       "scaleOf(SCALE_KEYS[ch.pcs[0]], own)" in mine and "spellUp(ch.root, sc.pcs)" in mine
       and ".catch(() => null)" in mine and "'harmonic_minor'" in mine)
    ok("My chords' roots are named for the key (spellRoot), in the picker and in the loop",
       "rootIn(plan, pc, c.quality)" in body("paintMine") and "rootIn(p, pcOf(c.root), c.quality)" in src)
    kb = (FRONTEND / "keyboard.js").read_text(encoding="utf-8")
    spicy = kb.find(".key.is-held:not(.is-scale){fill:var(--key-spicy")
    safe = kb.find(".key.is-held.is-pin{fill:var(--key-held")
    ok("a held pinned key is not painted spicy: its rule follows the spicy one",
       0 < spicy < safe and "kb.setMarks('pin'" in body("paintKeys"), f"spicy at {spicy}, pin at {safe}")
    ok("the band hears the parts' switches and sounds: start sends them, the toggles AND with the seat",
       "comp_program: opt.compSound, bass_program: opt.bassSound" in body("start")
       and "bass: opt.bass && seat() !== 'bass', comp: opt.comp && seat() !== 'chords'" in src
       and "**_programs(body))}" in server)
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

    names = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
    # The pin is checked on every tonic in both modes, not only the keys Jam offers:
    # a wander or a new key list must not find one where the pin rubs.
    all_keys = []
    for name in names:
        for mode in ("major", "natural_minor"):
            plan = theory.scale_plan(name, mode)
            all_keys.append({"name": f"{name} {mode}", "mode": mode, "pcs": plan["pcs"],
                             "chords": [c["pcs"] for c in plan["chords"]],
                             "roots": [c["root"] for c in plan["chords"]]})
    served = served_scales()
    room_modes = {"dorian": (2, "dorian"), "phrygian": (4, "phrygian"), "lydian": (5, "lydian"),
                  "mixolydian": (7, "mixolydian"), "aeolian": (9, "natural_minor")}
    rooms = {rid: theory.scale_plan(names[t], m)["pcs"] for rid, (t, m) in room_modes.items()}

    driver = FRONTEND / "_jam_check.mjs"
    driver.write_text(DRIVER, encoding="utf-8")
    try:
        proc = subprocess.run([node, str(driver)], capture_output=True, text=True,
                              env={**os.environ, "JAM_KEYS": json.dumps(keys),
                                   "JAM_ROOMS": json.dumps(rooms),
                                   "JAM_ALL_KEYS": json.dumps(all_keys),
                                   "JAM_SERVED": json.dumps(served)})
    finally:
        driver.unlink(missing_ok=True)
    if proc.returncode != 0:
        print("the driver did not run:")
        print(proc.stderr.strip() or proc.stdout.strip())
        return 1
    got = json.loads(proc.stdout.strip().splitlines()[-1])
    for r in got["out"]:
        ok(r["label"], r["passed"], r.get("detail", ""))
    chord_names_check(got["rootNames"])

    band_checks()
    source_checks()

    shutil.rmtree(SANDBOX, ignore_errors=True)
    passed = all(p for _, p, _ in results)
    for label, p, detail in results:
        print(f"  [{'PASS' if p else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))
    print()
    print(f"{'ALL CHECKS PASSED' if passed else 'FAILED'}  ({len(results)} assertions)")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
