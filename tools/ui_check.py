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

**Then Jam, with the band running.** The app here is silent (ui_sandbox's zero gain),
so the band really plays. Focus has to fill the window, leave by Esc without stopping
the band, and hand over to the roll without the two fighting over fullscreen.

**Then pages.** The builder's panel list against the panels the views really render,
each starter made through the builder and audited pane by pane at all three widths, and
the dock's highlight owners: Chords & scales across the whole keyboard, the touch keys
and Play's scale highlighter on top of it without wiping it, all of it back after a
range change. Every pane is held to two more rules: no control past its sides (a pane
does not scroll sideways) and no pane cut to a sliver.

**Then what several views in one document got wrong.** Layers beside Play following
Play's instrument pick; Touch response's labels surviving the band; G with an exercise
run waiting; a Settings reset keeping the pages; the builder's Save in reach; hidden
panels doing nothing per frame; and no listener or observer left behind by a page.

**Then resize.** Every managed panel of every tab and of each starter page, through
every span its arrows reach, at 1600x1000 and 1280x800: every click changes what the
panel shows, nothing sticks out of its box, no card is stretched to hide a hole, and no
hole is left too narrow for a panel; as shipped, no row stops short of the edge and the
gap under every panel is the grid's own. Saved layouts from before keep their order and
the widths that still exist. The builder's per-panel minimums are held against the
panels' measured ones, and its too-narrow warning against a real page's pane widths.
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


# The page's own view of the app: app.js is one module instance, so importing it from
# the console reaches the live ctx -- the band's state from the last 1 Hz status frame.
BAND = "async () => (await import('/app.js')).ctx.status?.band || {}"

# Every POST the page makes, so a check can say what was (and was not) sent.
SPY = """() => {
  window.__posts = [];
  const real = window.fetch;
  window.fetch = (url, opt = {}) => {
    if ((opt.method || 'GET').toUpperCase() === 'POST') window.__posts.push({ url: String(url), body: opt.body || '' });
    return real(url, opt);
  };
}"""

BAR = """() => {
  const b = document.querySelector('.jam__opts'), r = b.getBoundingClientRect();
  const kids = [...b.children].filter(k => k.offsetParent);
  const right = Math.max(...kids.map(k => k.getBoundingClientRect().right));
  return { h: Math.round(r.height), top: Math.round(r.top), opacity: +getComputedStyle(b).opacity,
    rows: (c => Math.max(...c) - Math.min(...c) <= 6 ? 1 : 2)(kids.map(k => k.getBoundingClientRect().top + k.offsetHeight / 2)),
    right: Math.round(right), exit: Math.round(document.querySelector('#focus-exit').getBoundingClientRect().left) };
}"""

FOCUS = """() => {
  const vis = (s) => { const e = document.querySelector(s); return !!e && getComputedStyle(e).display !== 'none'; };
  const st = document.getElementById('stage').getBoundingClientRect();
  const panel = document.querySelector('[data-focus="on"]');
  const pr = panel ? panel.getBoundingClientRect() : null;
  const ex = document.getElementById('focus-exit').getBoundingClientRect();
  const hit = document.elementFromPoint(ex.left + ex.width / 2, ex.top + ex.height / 2);
  const kb = document.querySelector('#dock .keys-kb').getBoundingClientRect();
  const strip = document.querySelector('[data-focus="on"] .strip');
  const sym = document.querySelector('[data-focus="on"] .jam__sym');
  return {
    on: document.body.classList.contains('is-focusing'),
    immersive: document.body.classList.contains('is-immersive'),
    jam: !!document.querySelector('[data-focus="on"] .jam'),
    chrome: ['.rail', '.nav', '.readout'].filter(vis),
    stage: [Math.round(st.left), Math.round(st.top), Math.round(st.width), innerWidth],
    // Against the stage's content box: focus pads the stage, and the panel fills inside that.
    fill: pr ? pr.height / (() => { const s = document.getElementById('stage'), c = getComputedStyle(s);
      return s.clientHeight - parseFloat(c.paddingTop) - parseFloat(c.paddingBottom); })() : 0,
    strip: strip ? Math.round(strip.getBoundingClientRect().height) : 0,
    sym: sym ? parseFloat(getComputedStyle(sym).fontSize) : 0,
    exit: !!hit && hit.closest('#focus-exit') !== null,
    kb: Math.round(kb.height),
    marked: document.querySelectorAll('[data-focus]').length,
  };
}"""


# Focused Jam on a window smaller than the one it was designed on: nothing in the stage
# (cards, strip, coach) may spill out of it onto the progression or the count, and the
# stage itself must not scroll. Each entry is a problem; none is a pass.
SPILL = """() => {
  const q = (s) => document.querySelector('[data-focus="on"] ' + s);
  const stage = q('.jam__stage').getBoundingClientRect();
  const out = [];
  for (const e of q('.jam__stage').querySelectorAll(':scope > *, .jam__mid > *, .jam__card > *')) {
    const r = e.getBoundingClientRect();
    if (r.height && r.bottom > stage.bottom + 1) out.push(`${e.className.split(' ')[0]} ends ${Math.round(r.bottom)} > stage ${Math.round(stage.bottom)}`);
  }
  for (const s of ['.jam__phrase', '.jam__stats']) {
    const r = q(s).getBoundingClientRect();
    if (r.height && r.top < stage.bottom - 1) out.push(`${s} starts inside the stage`);
  }
  const st = document.getElementById('stage');
  if (st.scrollHeight > st.clientHeight + 1) out.push(`the stage scrolls ${st.scrollHeight} > ${st.clientHeight}`);
  return out;
}"""
FOCUS_SIZES = ((1024, 680), (1280, 720), (1500, 1000))


# Dark: a white chord key, held and unheld, against an unmarked white key held (that
# one IS spicy -- the reference colour) and unheld (a plain key).
DARK = """async () => {
  const kb = (await import('/app.js')).ctx.kb;
  const svg = document.querySelector('#dock .keys-kb');
  const tones = [...svg.querySelectorAll('.key--white.is-tone')];
  const plain = [...svg.querySelectorAll('.key--white:not(.is-tone):not(.is-scale)')];
  if (tones.length < 2 || plain.length < 2) return { error: 'nothing marked' };
  const all = [tones[0], tones[1], plain[0], plain[1]];
  for (const k of all) k.style.transition = 'none';
  const [tHeld, tFree, pHeld, pFree] = all.map((k) => +k.dataset.midi);
  kb.noteOn(tHeld, 100); kb.noteOn(pHeld, 100);
  const fill = (k) => getComputedStyle(k).fill;
  const out = {
    dark: svg.classList.contains('kb--dark'),
    hidden: fill(tones[1]) === fill(plain[1]),
    held: fill(tones[0]), spicy: fill(plain[0]),
    guide: document.querySelector('.jam').dataset.guide,
  };
  kb.noteOff(tHeld); kb.noteOff(pHeld);
  for (const k of all) k.style.transition = '';
  return out;
}"""


def pick(page, opt: str, label: str, wait: int = 500) -> None:
    """Click one button of a Jam segment, found by the label above it."""
    page.evaluate("""([opt, label]) => [...document.querySelectorAll('.jam__opt')]
      .find((o) => o.querySelector('.jam__lbl')?.textContent === opt)
      .querySelectorAll('.seg__btn').forEach((b) => { if (b.textContent === label) b.click(); })""",
                  [opt, label])
    page.wait_for_timeout(wait)


# Play a note into Jam the way the websocket frame does: practice.js hands frames down.
PLAY = """async (ns) => {
  const view = (await import('/views/practice.js')).default;
  for (const n of ns) { view.frame({ on: [[n, 96]] }); await new Promise((r) => setTimeout(r, 180)); }
}"""

# The names in the coach's bold text ("C · E · G"), as MIDI around middle C, in order.
COACHED = """() => {
  const PC = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };
  const b = document.querySelector('.jam__coach b');
  return (b ? b.textContent : '').split('·').map((s) => s.trim()).filter(Boolean)
    .map((s) => 72 + ((PC[s[0]] + (s.includes('♯') ? 1 : 0) - (s.includes('♭') ? 1 : 0) + 12) % 12));
}"""


def mode_checks(page, check) -> list[str]:
    """Play: every mode starts from the next free bar, and the duets judge what you play."""
    def until(js: str, ms: int = 12000) -> bool:
        try:
            page.wait_for_function(js, timeout=ms)
            return True
        except Exception:  # noqa: BLE001 -- a timeout is the failure being reported
            return False

    text = "(s) => document.querySelector(s)?.textContent || ''"
    pick(page, "Music", "Modal", 300)
    shown = page.evaluate("""() => [...document.querySelectorAll('.jam__opt')]
      .filter((o) => o.style.display !== 'none').map((o) => o.querySelector('.jam__lbl').textContent)""")
    check("Modal: a room instead of a key", "Room" in shown and "Key" not in shown, str(shown))
    ringed = until("document.querySelector('#dock .key.is-sig') && document.querySelector('.strip__key.is-sig')")
    role = page.evaluate("() => document.querySelector('.strip__key.is-sig .strip__role')?.textContent")
    check("...the note that makes the mode is ringed, on the keys and the strip", ringed and role, f"labelled {role!r}")

    # Level 3 is the blues as it always was; level 1 is two chords, four bars each.
    pick(page, "Music", "Blues", 300)
    pick(page, "Level", "1", 300)
    vamp = until("document.querySelectorAll('.jam__chip').length === 8")
    chords = page.evaluate("new Set([...document.querySelectorAll('.jam__chip b')].map((b) => b.textContent)).size")
    check("Blues level 1: a two-chord vamp, four bars each", vamp and chords == 2, f"{chords} chords")
    pick(page, "Level", "3", 300)
    twelve = until("document.querySelectorAll('.jam__chip').length === 12")
    chips = page.evaluate("document.querySelectorAll('.jam__chip').length")
    style = page.evaluate(BAND).get("style")
    check("Blues: twelve bars, on the Shuffle", twelve and style == "shuffle", f"{chips} chips, {style}")
    check("...with the blue note ringed", until("[...document.querySelectorAll('.strip__key.is-sig .strip__role')]"
                                                ".some((e) => e.textContent === 'blue')"))

    pick(page, "Music", "Pop", 300)
    pick(page, "Play", "Answer", 300)
    shown = page.evaluate("""() => [...document.querySelectorAll('.jam__opt')]
      .filter((o) => o.style.display !== 'none').map((o) => o.querySelector('.jam__lbl').textContent)""")
    check("Answer: no seat to take, and no Level, which changes nothing there",
          "Seat" not in shown and "Level" not in shown and page.evaluate(BAND).get("bass") is True, str(shown))
    check("...the band's question lights the keys as it plays", until("document.querySelector('#dock .key.is-lead')", 15000))
    turn = until("document.querySelector('.jam__coach').textContent.startsWith('Your turn')", 15000)
    home = page.evaluate(COACHED)
    check("...then it is your turn, told where home is", turn and len(home) == 3, page.evaluate(text, ".jam__coach"))
    page.evaluate(PLAY, [home[0] - 5 if home else 67, home[0] if home else 72])
    judged = until("document.querySelector('.jam__heard').textContent.includes('on the root')", 8000)
    check("...and an answer ending on the root is heard as home", judged, page.evaluate(text, ".jam__heard"))
    # A pin is only drawn in Answer: it may not start the conversation again and zero the
    # sitting's count.
    pick(page, "Scale", "Pentatonic", 900)
    kept = page.evaluate(text, ".jam__stats")
    pick(page, "Scale", "Off", 300)
    check("...Scale leaves the conversation and its count alone", "of 1 answer" in kept, kept)

    pick(page, "Play", "Echo", 300)
    four = page.evaluate("""() => [...document.querySelectorAll('.jam__opt')].find((o) => o.style.display !== 'none'
      && o.querySelector('.jam__lbl')?.textContent === 'Level')?.querySelectorAll('.seg__btn')[3]?.title""")
    check("Echo keeps Level, and its 4 does not promise key changes", bool(four) and "Solo only" in four, repr(four))
    turn = until("document.querySelector('.jam__coach').textContent.startsWith('Your turn')", 15000)
    lick = page.evaluate(COACHED)
    check("Echo: after the lick, your turn, with its notes", turn and 2 <= len(lick) <= 6, str(lick))
    page.evaluate(PLAY, lick)
    judged = until("document.querySelector('.jam__heard').textContent.includes('note for note')", 8000)
    check("...and playing it back note for note counts", judged and "1 of 1" in page.evaluate(text, ".jam__stats"),
          f"{page.evaluate(text, '.jam__heard')} / {page.evaluate(text, '.jam__stats')}")
    pick(page, "Scale", "Pentatonic", 900)
    kept = page.evaluate(text, ".jam__stats")
    pick(page, "Scale", "Off", 300)
    check("...a Scale pin is only drawn in Echo: the count stays", "1 of 1" in kept, kept)

    pick(page, "Play", "Solo", 1500)
    check("back to Songs, the band still playing", page.evaluate(BAND).get("running") is True)
    return []


# The dock's keys by class, as MIDI numbers, and every key the dock has.
MARKED = "(c) => [...document.querySelectorAll('#dock .key.' + c)].map((k) => +k.dataset.midi).sort((a, b) => a - b)"
DOCK = "() => [...document.querySelectorAll('#dock .key')].map((k) => +k.dataset.midi).sort((a, b) => a - b)"
LETTER = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def key_pc(name: str) -> int:
    return (LETTER[name[0]] + name.count("#") - name[1:].count("b")) % 12


# Held, a pinned key that rubs (outlined, not filled) against an unmarked held key (spicy).
PIN_HELD = """async () => {
  const kb = (await import('/app.js')).ctx.kb;
  const svg = document.querySelector('#dock .keys-kb');
  const rub = svg.querySelector('.key.is-pin:not(.is-scale):not(.is-tone)');
  const plain = svg.querySelector('.key:not(.is-pin):not(.is-scale):not(.is-tone)');
  if (!rub || !plain) return { error: 'no pinned rub and unmarked key to compare' };
  for (const k of [rub, plain]) k.style.transition = 'none';
  kb.noteOn(+rub.dataset.midi, 100); kb.noteOn(+plain.dataset.midi, 100);
  const out = { rub: getComputedStyle(rub).fill, spicy: getComputedStyle(plain).fill,
    stroke: getComputedStyle(rub).strokeWidth };
  kb.noteOff(+rub.dataset.midi); kb.noteOff(+plain.dataset.midi);
  for (const k of [rub, plain]) k.style.transition = '';
  return out;
}"""


def pin_checks(page, check) -> None:
    """Scale: the pinned scale outlined on every key of it, and the band kept to chords it fits."""
    def until(js: str, ms: int = 12000) -> bool:
        try:
            page.wait_for_function(js, timeout=ms)
            return True
        except Exception:  # noqa: BLE001 -- a timeout is the failure being reported
            return False

    key = page.evaluate("document.querySelector('.jam__opt--key select').value")      # e.g. 'C-major'
    tonic, major = key_pc(key.split("-")[0]), key.endswith("major")
    # The pentatonic on the tonic, by its steps -- written here, not read from the app.
    want = {(tonic + s) % 12 for s in ((0, 2, 4, 7, 9) if major else (0, 3, 5, 7, 10))}
    pick(page, "Scale", "Pentatonic", 1500)
    until("document.querySelectorAll('#dock .key.is-pin').length > 0")
    dock = page.evaluate(DOCK)
    pinned = page.evaluate(MARKED, "is-pin")
    expect = [m for m in dock if m % 12 in want]
    check("Scale Pentatonic: every key of the pentatonic is outlined, across the whole keyboard, and no other",
          pinned == expect and len(pinned) >= 5, f"{len(pinned)} outlined, {len(expect)} expected, pcs {sorted(want)}")
    strip = page.evaluate("[...document.querySelectorAll('.strip__key')].map((k, pc) => k.classList.contains('is-pin') ? pc : -1).filter((pc) => pc >= 0)")
    name = page.evaluate("document.querySelector('.jam__note').textContent")
    check("...on the strip too, with its name on the Now card", set(strip) == want and "pentatonic" in name,
          f"strip {strip}, note {name!r}")
    # Only chords the pentatonic has no rub over: I ii IV vi in major, i III iv VI in minor.
    ok_romans = {"I", "ii", "IV", "vi"} if major else {"i", "III", "iv", "VI"}
    until("[...document.querySelectorAll('.jam__chip small')].length > 0")
    page.wait_for_timeout(2500)
    romans = page.evaluate("[...document.querySelectorAll('.jam__chip small')].map((s) => s.textContent)")
    check("...and the band plays only chords the pin fits", romans and set(romans) <= ok_romans, str(romans))
    pick(page, "Guide", "Dark", 600)
    dark = page.evaluate("document.querySelector('#dock .keys-kb').classList.contains('kb--nopin')")
    kept = page.evaluate(MARKED, "is-pin") == pinned
    check("Guide Dark stops drawing the outline but keeps the marks", dark and kept)
    pick(page, "Guide", "Full", 600)
    # Key: the whole key outlined, so the rubs are outlined too -- and held, not spicy.
    pick(page, "Scale", "Key", 1500)
    found = until("document.querySelector('#dock .key.is-pin:not(.is-scale):not(.is-tone)')"
                  " && document.querySelector('#dock .key:not(.is-pin):not(.is-scale):not(.is-tone)')", 15000)
    held = page.evaluate(PIN_HELD) if found else {"error": "no rub came up"}
    check("Scale Key: a held key of the pin that rubs keeps its outline and is not painted spicy",
          not held.get("error") and held["rub"] != held["spicy"] and float(held["stroke"].replace("px", "")) >= 1.5,
          str(held))
    pick(page, "Scale", "Off", 1200)
    check("Scale Off: nothing outlined", until("!document.querySelector('#dock .key.is-pin')", 5000))


def mine_checks(page, check) -> None:
    """My chords: build a loop in the editor and hear the band play it."""
    def until(js: str, ms: int = 12000) -> bool:
        try:
            page.wait_for_function(js, timeout=ms)
            return True
        except Exception:  # noqa: BLE001 -- a timeout is the failure being reported
            return False

    page.evaluate("window.__posts = []")
    pick(page, "Music", "My chords", 1500)
    shown = page.evaluate("""() => [...document.querySelectorAll('.jam__opt')]
      .filter((o) => o.offsetParent).map((o) => o.querySelector('.jam__lbl').textContent)""")
    slots = until("document.querySelectorAll('.jam__slot').length === 4")
    check("My chords: the editor shows, four chords to start, no Level or Play", slots and "Level" not in shown
          and "Play" not in shown and page.evaluate("getComputedStyle(document.querySelector('.jam__group--mine')).display") != "none",
          str(shown))
    # Slot 2 becomes D7: open it, root D, kind 7. Then a fifth chord, from the key's row.
    page.click(".jam__slot:nth-child(2) .jam__slotbtn")
    page.wait_for_timeout(300)
    page.evaluate("""() => {
      const rows = [...document.querySelectorAll('.jam__picker .jam__prow')];
      [...rows[0].querySelectorAll('.jam__pick')].find((b) => b.textContent === 'D').click();
    }""")
    page.wait_for_timeout(400)
    page.evaluate("""() => [...document.querySelectorAll('.jam__picker .jam__prow')][1]
      .querySelectorAll('.jam__pick').forEach((b) => { if (b.textContent === '7') b.click(); })""")
    out = until("document.querySelector('.jam__slot:nth-child(2)').classList.contains('is-out')", 8000)
    check("...a chord picked from the roots and kinds is a slot, marked out of the key",
          out and page.evaluate("document.querySelector('.jam__slot:nth-child(2) b').textContent") == "D7")
    page.click(".jam__slotadd")
    page.wait_for_timeout(300)
    page.evaluate("""() => [...document.querySelectorAll('.jam__picker .jam__prow')][2]
      .querySelectorAll('.jam__pick')[3].click()""")
    five = until("document.querySelectorAll('.jam__slot').length === 5")
    page.evaluate("[...document.querySelectorAll('.jam__picker .btn')].find((b) => b.textContent === 'Done').click()")
    check("...+ adds a chord, filled from the key's own row in one click", five
          and page.evaluate("document.querySelector('.jam__slot:nth-child(5) b').textContent") == "F")
    # D7 is D F# A C: written here as pitch classes. The band must get it in a chart.
    d7 = [2, 6, 9, 0]
    sent = until(f"""window.__posts.some((p) => p.url.endsWith('/api/band/chart')
      && JSON.parse(p.body).chart.some((c) => JSON.stringify(c.pcs) === '{d7}'.replace(/ /g, '')))""", 8000)
    check("...and the band is sent the loop, D7 in it", sent)
    # Past the change's first beat, where the coach is still saying where to land.
    heard = until("document.querySelector('.jam__sym').textContent === 'D7'"
                  " && document.querySelector('.jam__coach').textContent.includes('mixolydian')", 25000)
    coach = page.evaluate("document.querySelector('.jam__coach').textContent")
    check("...plays it, and the coach names the scale D7 is judged by", heard and "D mixolydian" in coach, coach)
    chips = page.evaluate("[...document.querySelectorAll('.jam__chip b')].map((b) => b.textContent)")
    check("...the phrase is your five chords", len(chips) == 5 and chips[1] == "D7", str(chips))
    page.evaluate("[...document.querySelectorAll('.jam__slotx')][4].click()")
    check("x takes one away again", until("document.querySelectorAll('.jam__slot').length === 4", 4000))
    # In A minor the key's row offers the dominant too: E and E7 after Em -- written here,
    # A minor's fifth degree with its third raised. One click, and it is in the key.
    page.select_option(".jam__opt--key select", "A-minor")
    page.wait_for_timeout(1200)
    page.click(".jam__slot:nth-child(2) .jam__slotbtn")
    page.wait_for_timeout(300)
    row = page.evaluate("""() => [...[...document.querySelectorAll('.jam__picker .jam__prow')][2]
      .querySelectorAll('.jam__pick')].map((b) => [b.querySelector('b').textContent, b.querySelector('small').textContent])""")
    syms = [b for b, _ in row]
    at = syms.index("Em") if "Em" in syms else -1
    check("My chords in A minor: the key's row has V and V7, E and E7, right after v",
          at >= 0 and row[at:at + 3] == [["Em", "v"], ["E", "V"], ["E7", "V7"]], str(row))
    page.evaluate("""() => [...document.querySelectorAll('.jam__picker .jam__pick')]
      .find((b) => b.querySelector('small')?.textContent === 'V7').click()""")
    inkey = until("document.querySelector('.jam__slot:nth-child(2) b').textContent === 'E7'", 4000)
    page.wait_for_timeout(1200)
    check("...and E7 picked from it is a slot in the key, not marked out",
          inkey and not page.evaluate("document.querySelector('.jam__slot:nth-child(2)').classList.contains('is-out')"))
    page.evaluate("[...document.querySelectorAll('.jam__picker .btn')].find((b) => b.textContent === 'Done').click()")
    page.select_option(".jam__opt--key select", "C-major")
    page.wait_for_timeout(1200)

    # The band's parts: a switch and a sound reach the band.
    page.evaluate("window.__posts = []")
    page.evaluate("""() => { const s = [...document.querySelectorAll('.jam__part')][2].querySelector('select');
      s.value = '48'; s.dispatchEvent(new Event('change')); }""")
    page.evaluate("[...document.querySelectorAll('.jam__part')][1].querySelector('input').click()")
    page.wait_for_timeout(900)
    sent = page.evaluate("""() => window.__posts.filter(p => p.url.endsWith('/api/band/config')).map(p => JSON.parse(p.body))""")
    band = page.evaluate(BAND)
    check("Band: Strings on the chords and the bass switched off reach the band", {"comp_program": 48} in sent
          and {"bass": False, "comp": True} in sent and band.get("comp_program") == 48 and band.get("bass") is False, str(sent))
    page.evaluate("[...document.querySelectorAll('.jam__part')][1].querySelector('input').click()")
    page.evaluate("""() => { const s = [...document.querySelectorAll('.jam__part')][2].querySelector('select');
      s.value = ''; s.dispatchEvent(new Event('change')); }""")
    pick(page, "Music", "Pop", 1500)


def key_change_checks(page, base: str, check) -> None:
    """Level 4 with the Scale picked after the panel opened, at 160 bpm: the key changes
    after two phrases, Stop goes back to the key heard, and Start opens on I in it."""
    def until(js: str, ms: int = 12000) -> bool:
        try:
            page.wait_for_function(js, timeout=ms)
            return True
        except Exception:  # noqa: BLE001 -- a timeout is the failure being reported
            return False

    key = "document.querySelector('.jam__opt--key select').value"
    page.request.post(f"{base}/api/band/stop")
    page.request.post(f"{base}/api/settings", data={"ui": {"jam": {
        "music": "pop", "game": "solo", "level": 1, "key": "C-major", "pin": "off", "bars": 1, "bpm": 160,
        "wander": False, "mine": []}}})
    page.goto(f"{base}/#practice", wait_until="networkidle")
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(2200)
    pick(page, "Level", "4", 600)
    pick(page, "Scale", "Pentatonic", 1500)
    page.click(".jam__go")
    # Under a Pentatonic pin the loops are four chords, played twice at level 4: two
    # phrases are 16 bars, and the change is announced four bars before it lands.
    nxt = until("document.querySelector('.jam__note').textContent.startsWith('Next:')", 40000)
    pos = page.evaluate(BAND).get("pos") or 0
    note = page.evaluate("document.querySelector('.jam__note').textContent")
    check("Level 4, Scale picked after opening: a key change comes, announced", nxt, note)
    check("...after two phrases, the first key's included (announced at bar 12, not bar 4)",
          nxt and 11 <= pos < 16, f"bar {pos:.1f}")
    heard = page.evaluate(key)
    page.click(".jam__go")                         # Stop, with the change still ahead
    page.wait_for_timeout(1000)
    shown = page.evaluate(key)
    music = page.evaluate("document.querySelector('.jam__group--music .seg__btn.is-on').textContent")
    saved = page.request.get(f"{base}/api/settings").json().get("ui", {}).get("jam", {})
    check("Stop before the change lands: the key shown and saved is the one heard",
          heard == shown == "C-major" and music == "Pop" and saved.get("key") == "C-major" and saved.get("music") == "pop",
          f"heard {heard}, shown {shown} {music}, saved {saved.get('key')} {saved.get('music')}")
    page.click(".jam__go")
    opened = until("document.querySelector('.jam__role').textContent.includes('·')", 8000)
    role = page.evaluate("document.querySelector('.jam__role').textContent")
    check("...and Start opens a fresh progression on I, in that key", opened and role.startswith("I ·")
          and page.evaluate(key) == "C-major", role)
    page.click(".jam__go")


def jam_checks(browser, port: int, errs: list[str]) -> list[str]:
    """Jam with the band playing: focus in and out by every door."""
    fails: list[str] = []
    page = browser.new_page(viewport={"width": 1600, "height": 1000})
    page.on("pageerror", lambda e: errs.append(str(e)))

    def check(label: str, passed: bool, detail: str = "") -> None:
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))
        if not passed:
            fails.append(f"jam: {label} {detail}".strip())

    def press(key: str, wait: int = 250) -> None:
        page.evaluate("document.activeElement?.blur?.()")
        page.keyboard.press(key)
        page.wait_for_timeout(wait)

    print("\njam, band running")
    # Settings from before Music and Play were split: Answer in A minor becomes Minor +
    # Answer at level 3, what it played before. Fields that make no sense fall back.
    base = f"http://127.0.0.1:{port}"
    page.request.post(f"{base}/api/settings", data={"ui": {"jam": {
        "mode": "answer", "key": "A-minor", "pin": "nope", "bars": 3, "compSound": 7,
        "mine": [{"root": "H", "quality": "q"}]}}})
    page.goto(f"{base}/#practice", wait_until="networkidle")
    page.wait_for_timeout(2200)
    shown = page.evaluate("""() => Object.fromEntries([...document.querySelectorAll('.jam__opt')].filter((o) => o.offsetParent)
      .map((o) => [o.querySelector('.jam__lbl').textContent, o.querySelector('.seg__btn.is-on')?.textContent ?? o.querySelector('select')?.value]))""")
    sound = page.evaluate("[...document.querySelectorAll('.jam__part select')][1].selectedOptions[0].textContent")
    # Answer shows no Level (it changes nothing there); the level it keeps is read off the control.
    level = page.evaluate("document.querySelector('.jam__opt--level .seg__btn.is-on')?.textContent")
    check("old settings carry over (Answer in A minor: Minor, Answer, level 3); nonsense falls back",
          shown.get("Music") == "Minor" and shown.get("Play") == "Answer" and "Level" not in shown and level == "3"
          and shown.get("Key") == "A-minor" and shown.get("Scale") == "Off" and sound == "Style's own", f"{shown} level {level} {sound}")
    page.request.post(f"{base}/api/settings", data={"ui": {"jam": {
        "music": "pop", "game": "solo", "level": 1, "key": "C-major", "pin": "off", "bars": 1, "compSound": None, "mine": []}}})
    page.reload(wait_until="networkidle")              # the same URL: goto() would not reload
    page.wait_for_timeout(2200)
    # Each key's label counts its black keys. Counted here from the key's name: the
    # major or natural minor steps from its tonic, and which pitch classes are black.
    labels = page.evaluate("[...document.querySelectorAll('.jam__opt--key select option')]"
                           ".map((o) => [o.value, o.textContent])")
    wrong = []
    for value, text in labels:
        name, mode = value.split("-")
        steps = (0, 2, 4, 5, 7, 9, 11) if mode == "major" else (0, 2, 3, 5, 7, 8, 10)
        black = sum((key_pc(name) + s) % 12 in (1, 3, 6, 8, 10) for s in steps)
        want = ("all white keys" if black == 0 else "all five black keys + 2 white" if black == 5
                else f"{black} black key{'' if black == 1 else 's'}")
        if f"— {want}" not in text:
            wrong.append(f"{text!r}, want {want}")
    check("every key's label counts its black keys", len(labels) >= 14 and not wrong,
          "; ".join(wrong[:3]) or f"{len(labels)} keys")
    page.evaluate(SPY)
    page.click(".jam__go")
    page.wait_for_timeout(1600)
    check("the band starts", page.evaluate(BAND).get("running") is True)

    page.click(".mod:has(.jam) .mod__focus")
    page.wait_for_timeout(400)
    f = page.evaluate(FOCUS)
    check("Focus puts Jam alone on the stage", f["on"] and f["jam"] and not f["chrome"],
          f"rail/nav/readout still showing: {f['chrome']}" if f["chrome"] else "")
    check("...the stage takes the whole window", f["stage"][:3] == [0, 0, f["stage"][3]], str(f["stage"]))
    check("...and the panel fills it", f["fill"] >= 0.97, f"{f['fill']:.2f} of the stage height")
    check("...big enough to read from the bench", f["strip"] >= 150 and f["sym"] >= 72,
          f"strip {f['strip']}px, chord {f['sym']}px")
    check("...the keys stay, and EXIT is on top", f["kb"] > 0 and f["exit"], f"keyboard {f['kb']}px")
    page.mouse.move(700, 400)
    page.mouse.move(720, 420)
    page.wait_for_timeout(700)
    bar = page.evaluate(BAR)
    check("...the options are one thin line across the top, clear of EXIT", bar["h"] <= 48 and bar["top"] == 0
          and bar["rows"] == 1 and bar["opacity"] > 0.9 and bar["right"] <= bar["exit"], str(bar))

    press("Escape", 1300)
    f = page.evaluate(FOCUS)
    posts = page.evaluate("window.__posts.map(p => p.url)")
    check("Esc leaves focus, not the band", not f["on"] and f["marked"] == 0
          and not any("/api/panic" in u for u in posts) and page.evaluate(BAND).get("running") is True)
    a = page.evaluate(AUDIT)
    bad = a.get("over", []) + a.get("junk", []) + a.get("hollow", [])
    check("...and Practice comes back laid out as it was", not a.get("error") and not bad, "; ".join(bad))

    # Smaller windows, the packaged one (1500x1000) among them. Resized out of focus:
    # focus is fullscreen, and a fullscreen window cannot be resized.
    for w, h in FOCUS_SIZES:
        page.set_viewport_size({"width": w, "height": h})
        page.wait_for_timeout(300)
        page.click(".mod:has(.jam) .mod__focus")
        page.wait_for_timeout(400)
        spill = page.evaluate(SPILL)
        check(f"Focus at {w}x{h}: nothing in the stage spills onto the rest", not spill, "; ".join(spill))
        page.mouse.move(w // 2, h // 2)
        page.mouse.move(w // 2 + 20, h // 2 + 20)
        page.wait_for_timeout(600)
        bar = page.evaluate(BAR)
        check("...and the options still make one line, clear of EXIT", bar["rows"] == 1 and bar["right"] <= bar["exit"],
              str(bar))
        press("Escape", 600)
    page.set_viewport_size({"width": 1600, "height": 1000})
    page.wait_for_timeout(300)

    # Lights Out. Dark hides the lit keys but keeps their marks: the held-key colours
    # read them, and a held key with none is painted spicy.
    pick(page, "Guide", "Dark")
    # Over some chords every white key is marked (F or Dm in C major: nothing rubs), and
    # the probe needs an unmarked one to compare against. Wait for a chord that has one.
    page.wait_for_function("""() => document.querySelectorAll('#dock .key--white:not(.is-tone):not(.is-scale)').length >= 2
      && document.querySelectorAll('#dock .key--white.is-tone').length >= 2""", timeout=15000)
    dark = page.evaluate(DARK)
    check("Guide Dark: the chord is marked but not drawn", dark["dark"] and dark["hidden"] and dark["guide"] == "3",
          str(dark))
    check("...and a held chord note is not painted spicy", dark["held"] != dark["spicy"], str(dark))
    pick(page, "Guide", "Full")

    # Seats: the band is told which part to drop, and only the seat's register is lit.
    for seat, want, lo, hi in (("Bass", {"bass": False, "comp": True}, 36, 55),
                               ("Chords", {"bass": True, "comp": False}, 48, 72),
                               ("Solo", {"bass": True, "comp": True}, 0, 127)):
        page.evaluate("window.__posts = []")
        pick(page, "Seat", seat, 1300)
        sent = page.evaluate("""() => window.__posts.filter(p => p.url.endsWith('/api/band/config'))
          .map(p => JSON.parse(p.body))""")
        band = page.evaluate(BAND)
        lit = page.evaluate("""() => [...document.querySelectorAll('#dock .key.is-tone:not(.is-quiet)')]
          .map(k => +k.dataset.midi)""")
        check(f"Seat {seat}: the band is told {want}, and plays it", sent == [want]
              and band.get("bass") is want["bass"] and band.get("comp") is want["comp"], f"sent {sent}")
        check(f"...lit only in {lo}-{hi}", lit and all(lo <= m <= hi for m in lit)
              and (seat != "Chords" or len(lit) >= 6), f"{len(lit)} keys, {min(lit, default=0)}-{max(lit, default=0)}")

    fails.extend(mode_checks(page, check))
    pin_checks(page, check)
    mine_checks(page, check)

    press("g")
    press("v", 300)
    f = page.evaluate(FOCUS)
    check("G is focus; V from focus hands over to the roll", f["immersive"] and not f["on"])
    page.wait_for_timeout(300)
    check("...and the roll stays (no fullscreen tug of war)", page.evaluate(FOCUS)["immersive"])
    page.click("#roll-exit")
    page.wait_for_timeout(250)

    press("g")
    page.click("#focus-exit")
    page.wait_for_timeout(250)
    check("EXIT leaves focus", not page.evaluate(FOCUS)["on"]
          and page.evaluate("document.fullscreenElement") is None)

    # An exercise run focuses too, and opening one leaves Jam's focus first.
    press("g")
    page.evaluate("document.querySelector('#ex-host .build .btn')?.click()")
    page.wait_for_timeout(600)
    check("opening an exercise leaves focus", not page.evaluate(FOCUS)["on"])
    page.click(".exrun .mod__focus")
    page.wait_for_timeout(300)
    run = page.evaluate("""() => ({
      on: !!document.querySelector('[data-focus="on"].exrun'),
      setup: getComputedStyle(document.querySelector('.exrun__setup')).display }) """)
    check("a run focuses, setup put away", run["on"] and run["setup"] == "none", str(run))
    page.evaluate("[...document.querySelectorAll('.exrun button')].find(b => b.textContent === 'Back to the shelf').click()")
    page.wait_for_timeout(400)
    check("Back to the shelf leaves focus and shows the shelf",
          not page.evaluate(FOCUS)["on"] and page.evaluate("!!document.querySelector('#ex-host .build')"))

    press("g")
    page.evaluate("location.hash = 'play'")
    page.wait_for_timeout(1200)
    f = page.evaluate(FOCUS)
    check("changing tab leaves focus", not f["on"] and f["marked"] == 0 and ".nav" in f["chrome"])
    press("g")
    check("off Practice, G does nothing", not page.evaluate(FOCUS)["on"])
    key_change_checks(page, base, check)
    page.close()
    return fails


# -- the transport: bar steps, Go to bar, loop by bars, the scrub, the sheet ---------
# A song written here rather than shipped: one note per beat, numbered 1..TR_BARS, long
# enough that the engraving runs to more than one page. Every expected value below is
# arithmetic on these constants -- bar n starts at quarter (n-1)*4 of TR_BARS*4 -- and
# never something read back from ghost.js.
TR_BARS = 160
TR_STEPS = "CDEFGAB"


def transport_fixture(count: int = TR_BARS) -> str:
    bars = []
    for i in range(count):
        attrs = ("<attributes><divisions>1</divisions><key><fifths>0</fifths></key>"
                 "<time><beats>4</beats><beat-type>4</beat-type></time>"
                 "<clef><sign>G</sign><line>2</line></clef></attributes>"
                 '<sound tempo="120"/>') if i == 0 else ""
        notes = "".join(f"<note><pitch><step>{TR_STEPS[(i + k) % 7]}</step><octave>4</octave></pitch>"
                        "<duration>1</duration><type>quarter</type></note>" for k in range(4))
        bars.append(f'<measure number="{i + 1}">{attrs}{notes}</measure>')
    return ('<?xml version="1.0" encoding="UTF-8"?><score-partwise version="3.1"><part-list>'
            '<score-part id="P1"><part-name>Piano</part-name></score-part></part-list>'
            '<part id="P1">' + "".join(bars) + "</part></score-partwise>")


# Import it the way the drawer does (raw bytes, name in a header), then open it on the
# roll through the same startGhost the song rows call.
TR_OPEN = """async ([xml, sheet]) => {
  const r = await fetch('/api/scores', { method: 'POST', headers: { 'x-filename': 'transport-check.musicxml' }, body: xml });
  const meta = (await r.json()).score;
  const payload = await (await fetch(`/api/scores/${meta.id}/notes`)).json();
  (await import('/app.js')).startGhost(payload, meta, { sheet });
  return meta.measures;
}"""

TR_READ = """() => ({
  bar: document.querySelector('#ghost-bar')?.textContent || '',
  read: document.querySelector('#ghost-read')?.textContent || '',
  play: document.querySelector('#ghost-play')?.textContent || '',
  left: parseFloat(document.querySelector('#ghost-section').style.left) || 0,
  width: parseFloat(document.querySelector('#ghost-section').style.width) || 0,
  from: document.querySelector('#ghost-loop-from')?.value, to: document.querySelector('#ghost-loop-to')?.value,
  roll: document.body.classList.contains('is-immersive'),
  song: document.body.classList.contains('is-ghosting'),
  pageN: document.querySelector('#roll-page-n')?.textContent || '',
  now: [...document.querySelectorAll('#roll-paper g.measure.is-now')].map((g) => +g.dataset.bar),
  loop: [...document.querySelectorAll('#roll-paper g.measure.is-loop')].map((g) => +g.dataset.bar),
})"""


# Every engrave Verovio does from here, timed, with the page it drew and the bar the
# readout showed then, and every time the page's SVG is swapped. The spy sits on the
# toolkit itself, under engrave.js. A turn that engraves does it in the same task as the
# swap, just before it; one served from a page engraved ahead has no engrave near it.
TURN_SPY = """async () => {
  const tk = await (await import('/engrave.js')).verovio();
  if (!window.__unspy) {
    const real = tk.renderToSVG;
    tk.renderToSVG = function (...a) {
      window.__engraves.push(performance.now());
      window.__drawn.push([a[0], document.querySelector('#ghost-bar')?.textContent || '']);
      return real.apply(this, a);
    };
    window.__unspy = () => { delete tk.renderToSVG; window.__unspy = null; };
  }
  window.__engraves = [];
  window.__drawn = [];
  window.__swaps = [];
  window.__swapObs?.disconnect();
  window.__swapObs = new MutationObserver(() => window.__swaps.push(performance.now()));
  window.__swapObs.observe(document.querySelector('#roll-paper'), { childList: true });
}"""

# The last swap, and how many engraves came in the 200 ms before it. Ends the spy.
TURN_READ = """() => {
  window.__swapObs.disconnect();
  window.__unspy?.();
  const turn = window.__swaps[window.__swaps.length - 1];
  return { turn: turn ?? null, engraves: window.__engraves.length, drawn: window.__drawn,
           before: window.__engraves.filter((t) => t < turn).length,
           late: window.__engraves.filter((t) => turn - t >= 0 && turn - t < 200).length };
}"""


def transport_checks(browser, port: int, errs: list[str]) -> list[str]:
    """The roll's transport, driven like a person: keys, buttons, the scrub, the page."""
    fails: list[str] = []
    page = browser.new_page(viewport={"width": 1600, "height": 1000})
    page.on("pageerror", lambda e: errs.append(str(e)))

    def check(label: str, passed: bool, detail: str = "") -> None:
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))
        if not passed:
            fails.append(f"transport: {label} {detail}".strip())

    def read() -> dict:
        return page.evaluate(TR_READ)

    def press(key: str, wait: int = 150) -> None:
        page.evaluate("document.activeElement?.blur?.()")
        page.keyboard.press(key)
        page.wait_for_timeout(wait)

    def stir() -> None:
        page.mouse.move(800, 600)
        page.mouse.move(810, 610)

    def bar_text(n: int) -> str:
        return f"bar {n} / {TR_BARS}"

    # Where the band for written bars a..b sits, in percent of the whole piece.
    def band(a: int, b: int) -> tuple[float, float]:
        total = TR_BARS * 4
        return (a - 1) * 4 / total * 100, (b - a + 1) * 4 / total * 100

    def near_band(r: dict, a: int, b: int) -> bool:
        left, width = band(a, b)
        return abs(r["left"] - left) < 0.02 and abs(r["width"] - width) < 0.02

    print("\ntransport, a song on the roll")
    page.goto(f"http://127.0.0.1:{port}/#play", wait_until="networkidle")
    page.wait_for_timeout(1500)
    page.evaluate(SPY)
    bars = page.evaluate(TR_OPEN, [transport_fixture(), False])
    page.wait_for_timeout(600)
    stir()
    r = read()
    check("the fixture opens on the roll at bar 1", bars == TR_BARS and r["song"] and r["bar"] == bar_text(1),
          f"{bars} bars, readout {r['bar']!r}")

    # -- bar steps, by key and by button --------------------------------------------
    press("ArrowRight")
    check("Right is a bar forward while nothing is waiting", read()["bar"] == bar_text(2), read()["bar"])
    press("ArrowRight")
    press("ArrowRight")
    page.click("#ghost-bar-fwd")
    page.wait_for_timeout(150)
    check("...one bar per press, key or button", read()["bar"] == bar_text(5), read()["bar"])
    press("ArrowLeft")
    check("Left from the top of a bar is the bar before", read()["bar"] == bar_text(4), read()["bar"])
    page.click("#ghost-bar-back")
    page.wait_for_timeout(150)
    check("...and so is the button", read()["bar"] == bar_text(3), read()["bar"])

    # -- Space -------------------------------------------------------------------------
    # Wait is on and nothing is held, so playing freezes on the bar's downbeat: the bar
    # number cannot drift between these reads.
    press("Space", 300)
    check("Space plays", read()["play"] == "Pause", read()["play"])
    press("Space", 300)
    check("...and Space again pauses", read()["play"] == "Play", read()["play"])
    page.focus("#ghost-bar-fwd")
    page.keyboard.press("Space")
    page.wait_for_timeout(300)
    r = read()
    check("Space with a bar button focused plays rather than pressing the button",
          r["play"] == "Pause" and r["bar"] == bar_text(3), f"{r['play']}, {r['bar']}")
    press("Space", 300)

    # A slider keeps its arrows, but Space still plays: dragging Tempo must not switch
    # the transport's key off until you click somewhere else.
    page.focus("#ghost-bpm")
    page.keyboard.press("Space")
    page.wait_for_timeout(200)
    on = read()["play"]
    page.keyboard.press("Space")
    page.wait_for_timeout(200)
    check("Space plays and pauses with the Tempo slider focused", on == "Pause" and read()["play"] == "Play",
          f"{on} then {read()['play']}")

    # A shortcut rebound to Space must not fire on top of play/pause.
    page.evaluate("async () => (await import('/app.js')).setBinds({ metronome: ' ' })")
    page.evaluate("window.__posts = []")
    press("Space", 200)
    on = read()["play"]
    press("Space", 200)
    posts = page.evaluate("window.__posts.map(p => p.url)")
    page.evaluate("async () => (await import('/app.js')).setBinds({})")
    check("a shortcut bound to Space does not fire as well as play/pause",
          on == "Pause" and not any("metronome" in u for u in posts), f"{on}, posts {posts}")

    # Space on a focused button in the drawer, Settings or the tour presses THAT button.
    # A Space taken for play/pause has its keyup cancelled; every other one must not be.
    press("s", 300)
    page.focus("#songs-close")
    page.keyboard.press("Space")
    page.wait_for_timeout(200)
    shut = page.evaluate("document.querySelector('#songs').hidden")
    check("Space on the songs drawer's close button closes it, and leaves the song paused",
          shut and read()["play"] == "Play", f"drawer hidden {shut}, {read()['play']}")
    press(",", 300)
    page.focus(".prefs__item:nth-child(3)")
    want = page.evaluate("document.activeElement?.dataset.id")
    page.keyboard.press("Space")
    page.wait_for_timeout(200)
    got = page.evaluate("document.querySelector('.prefs__item.is-on')?.dataset.id")
    page.focus(".prefs__close")
    page.keyboard.press("Space")
    page.wait_for_timeout(200)
    gone = page.evaluate("!document.querySelector('#prefs')")
    check("with a song loaded, Space still presses buttons in Settings",
          bool(want) and got == want and gone and read()["play"] == "Play",
          f"section {got!r} want {want!r}, closed {gone}, {read()['play']}")
    # Put things back whatever happened, so one failure here does not cascade.
    page.evaluate("async () => (await import('/settings-overlay.js')).closeSettings()")
    page.evaluate("document.querySelector('#songs').hidden || document.querySelector('#songs-close').click()")
    if read()["play"] == "Pause":
        press("Space", 200)

    # -- Go to bar -----------------------------------------------------------------------
    page.click("#ghost-bar")
    page.wait_for_timeout(100)
    shown = page.evaluate("() => !document.querySelector('#ghost-bar-in').hidden && document.activeElement?.id")
    page.keyboard.type("42")
    page.keyboard.press("Enter")
    page.wait_for_timeout(200)
    check("clicking the bar number opens a field, and Enter goes there",
          shown == "ghost-bar-in" and read()["bar"] == bar_text(42), f"focus {shown!r}, {read()['bar']}")
    page.evaluate("window.__posts = []")
    page.click("#ghost-bar")
    page.wait_for_timeout(100)
    page.keyboard.type("7")
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    r = read()
    posts = page.evaluate("window.__posts.map(p => p.url)")
    hidden = page.evaluate("document.querySelector('#ghost-bar-in').hidden")
    check("Esc in it cancels: no move, no panic, still on the roll",
          r["bar"] == bar_text(42) and hidden and r["roll"] and r["song"]
          and not any("/api/panic" in u for u in posts), f"{r['bar']}, roll {r['roll']}, posts {posts}")

    # -- loop by bars ------------------------------------------------------------------
    page.fill("#ghost-loop-from", "10")
    page.fill("#ghost-loop-to", "12")
    page.click("#ghost-loop-apply")
    page.wait_for_timeout(200)
    r = read()
    check("Loop bars 10 to 12 puts the band over exactly those bars", near_band(r, 10, 12),
          f"left {r['left']}% width {r['width']}%, want {band(10, 12)}")
    check("...the playhead moves into it, and the readout names it",
          r["bar"] == bar_text(10) and "looping bars 10-12" in r["read"], f"{r['bar']} / {r['read']!r}")
    press("ArrowRight")
    page.click("#ghost-loop-bar")
    page.wait_for_timeout(150)
    r = read()
    check("Loop this bar is the bar you are in", near_band(r, 11, 11) and r["from"] == "11" and r["to"] == "11",
          f"left {r['left']}% width {r['width']}%, fields {r['from']}-{r['to']}")
    page.click("#ghost-bar")
    page.keyboard.type("50")
    page.keyboard.press("Enter")
    page.wait_for_timeout(150)
    check("...and the section survives a seek elsewhere", near_band(read(), 11, 11) and read()["bar"] == bar_text(50))
    page.click("#ghost-loop-from")
    page.keyboard.type("99")
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    r = read()
    check("Esc in a loop field puts it back, and leaves the roll alone",
          r["from"] == "11" and r["roll"] and r["song"], f"field {r['from']!r}, roll {r['roll']}")

    # Enter applies AND lets go of the field, so the next Space plays the section.
    page.fill("#ghost-loop-from", "12")
    page.fill("#ghost-loop-to", "12")
    page.keyboard.press("Enter")
    page.wait_for_timeout(150)
    page.keyboard.press("Space")
    page.wait_for_timeout(250)
    r = read()
    page.keyboard.press("Space")
    page.wait_for_timeout(250)
    check("after Enter in a loop field, Space plays the section",
          r["play"] == "Pause" and r["bar"] == bar_text(12) and near_band(r, 12, 12),
          f"{r['play']}, {r['bar']}")

    # Real fullscreen keeps Esc for itself: the field never hears it, fullscreen just
    # ends. That exit, with a roll field focused, is the field's Esc -- not "close the
    # song". A synthetic Esc never leaves fullscreen, so the browser's half is played by
    # leaving it for real; the Esc keydown that can trail it must not close the song
    # either. Opened from a test, the roll did get real fullscreen -- it is checked, or
    # this passes on an exit that never happened.
    page.click("#ghost-loop-from")
    page.keyboard.type("99")
    was = page.evaluate("""async () => { const was = !!document.fullscreenElement;
      if (was) await new Promise((done) => {
        document.addEventListener('fullscreenchange', () => setTimeout(done), { once: true });
        document.exitFullscreen(); });
      return was; }""")
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    r = read()
    left = page.evaluate("!document.fullscreenElement")
    check("fullscreen ending under a loop field cancels the field and keeps the song",
          was and left and r["from"] == "12" and r["roll"] and r["song"] and near_band(r, 12, 12),
          f"was fullscreen {was}, left {left}, field {r['from']!r}, roll {r['roll']}, song {r['song']}")

    # -- the scrub ---------------------------------------------------------------------
    stir()
    box = page.evaluate("""() => { const r = document.querySelector('#ghost-scrub').getBoundingClientRect();
      return { x: r.left, y: r.top, w: r.width, h: r.height }; }""")
    hit = page.evaluate("""([x, y]) => !!document.elementFromPoint(x, y)?.closest('#ghost-scrub')""",
                        [box["x"] + box["w"] / 2, box["y"] + 10])
    check("the scrub is a 12 px target over its 3 px line", box["h"] >= 12 and hit, f"{box['h']}px tall")

    def at_bar(n: int) -> float:                 # x of the middle of written bar n
        return box["x"] + ((n - 1) * 4 + 2) / (TR_BARS * 4) * box["w"]

    y = box["y"] + 6
    page.mouse.move(at_bar(30), y)
    page.wait_for_timeout(120)
    tip = page.evaluate("() => { const t = document.querySelector('#ghost-scrub-tip'); return t.hidden ? '' : t.textContent; }")
    check("hovering the scrub names the bar under the pointer", tip.startswith("bar 30"), repr(tip))
    page.mouse.move(at_bar(80), y)
    page.mouse.down()
    page.mouse.move(at_bar(90), y, steps=4)
    mid = read()["bar"]
    page.mouse.move(at_bar(100), y, steps=4)
    page.mouse.up()
    page.wait_for_timeout(150)
    check("dragging the scrub moves the playhead with it", mid == bar_text(90) and read()["bar"] == bar_text(100),
          f"mid-drag {mid}, after {read()['bar']}")
    page.keyboard.down("Shift")
    page.mouse.move(at_bar(20), y)
    page.mouse.down()
    page.mouse.move(at_bar(23), y, steps=4)
    page.mouse.up()
    page.keyboard.up("Shift")
    page.wait_for_timeout(200)
    r = read()
    check("Shift-drag loops the bars swept, and lands in them", near_band(r, 20, 23) and r["bar"] == bar_text(20),
          f"left {r['left']}% width {r['width']}%, {r['bar']}")

    # -- the sheet ---------------------------------------------------------------------
    page.click("#roll-mode .btn[data-mode='sheet']")
    try:
        page.wait_for_function("document.querySelectorAll('#roll-paper g.measure[data-bar]').length > 0",
                               timeout=60000)
    except Exception:  # noqa: BLE001 -- reported below
        pass
    page.wait_for_timeout(300)
    page.evaluate(TURN_SPY)
    r = read()
    pages = int(r["pageN"].split("/")[1]) if "/" in r["pageN"] else 0
    check("Sheet engraves the song with its measures matched to bars, over more than one page",
          r["now"] == [19] and pages >= 2, f"current {r['now']}, pages {r['pageN']!r}")
    check("...the section's bars are banded on the page", sorted(r["loop"]) == [19, 20, 21, 22], str(r["loop"]))
    target = page.evaluate("""() => { const el = document.querySelector('#roll-paper g.measure[data-bar="5"] > .rollpaper__bar');
      if (!el) return null;
      el.scrollIntoView({ block: 'center' });
      const r = el.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }""")
    if target:
        page.mouse.click(*target)
        page.wait_for_timeout(200)
    r = read()
    check("clicking a measure seeks to it", bool(target) and r["bar"] == bar_text(6) and r["now"] == [5],
          f"{r['bar']}, current {r['now']}")

    page.click("#ghost-bar")
    page.keyboard.type(str(TR_BARS))
    page.keyboard.press("Enter")
    page.wait_for_function(f"document.querySelector('#roll-page-n').textContent === '{pages}/{pages}'", timeout=8000)
    page.wait_for_timeout(300)
    r = read()
    check("going to the last bar turns to the last page and marks it", r["pageN"] == f"{pages}/{pages}"
          and r["now"] == [TR_BARS - 1], f"page {r['pageN']}, current {r['now']}")
    # A page never shown before, so had the turn found it ready, it was engraved ahead.
    spy = page.evaluate(TURN_READ)
    check("...a page it had never shown, engraved ahead while paused: the turn engraves nothing",
          spy["turn"] is not None and spy["late"] == 0,
          f"{spy['late']} engrave(s) in the 200 ms before the turn, {spy['engraves']} in all")
    page.click("#roll-page-prev")
    page.wait_for_timeout(1600)                # past a heartbeat
    check("...a page turned back by hand stays turned while paused",
          read()["pageN"] == f"{pages - 1}/{pages}", read()["pageN"])

    # ...but pressing Play is the music moving on. Wait is on and nothing is held, so the
    # clock freezes on bar TR_BARS's downbeat: the page has to show it, or it waits for a
    # chord nobody can see.
    press("Space", 1200)
    r = read()
    press("Space", 200)
    check("Play brings back the page the current bar is on", r["play"] == "Pause"
          and r["pageN"] == f"{pages}/{pages}" and r["now"] == [TR_BARS - 1], f"{r['pageN']}, current {r['now']}")
    page.evaluate("document.querySelector('#roll-page-prev').click()")   # no-op if disabled
    page.wait_for_timeout(1200)
    page.click("#ghost-bar")
    page.keyboard.type(str(TR_BARS))
    page.keyboard.press("Enter")
    page.wait_for_timeout(1200)
    check("...and so does Go to the bar you are already in", read()["pageN"] == f"{pages}/{pages}",
          read()["pageN"])
    page.evaluate("document.querySelector('#roll-page-prev').click()")
    page.wait_for_timeout(1600)

    # Playing over a page break turns the page. The last bar on this page comes from the
    # page itself; the bar after it is, by construction, on the next one.
    page.click("#ghost-loop-clear")
    last = page.evaluate("Math.max(...[...document.querySelectorAll('#roll-paper g.measure[data-bar]')].map((g) => +g.dataset.bar))")
    page.evaluate(TURN_SPY)
    page.click("#ghost-wait")                  # wait off: the clock runs without a piano
    page.evaluate("""() => { const s = document.querySelector('#ghost-bpm'); s.value = '240';
      s.dispatchEvent(new Event('input', { bubbles: true })); }""")
    page.click("#ghost-bar")
    page.keyboard.type(str(last + 1))          # written number = index + 1 in this fixture
    page.keyboard.press("Enter")
    page.wait_for_timeout(400)
    before = read()["pageN"]
    press("Space", 1800)                       # one 4/4 bar at 240 is a second
    r = read()
    press("Space", 200)
    page.click("#ghost-wait")                  # and back on, as the settings had it
    check("playing over the page break turns the page", before == f"{pages - 1}/{pages}"
          and r["pageN"] == f"{pages}/{pages}" and r["now"] and r["now"][0] > last,
          f"{before} -> {r['pageN']}, current {r['now']} past {last}")
    spy = page.evaluate(TURN_READ)
    check("...from a page engraved ahead: the turn itself engraves nothing",
          spy["turn"] is not None and spy["late"] == 0,
          f"{spy['late']} engrave(s) in the 200 ms before the turn, {spy['engraves']} in all")

    # -- the pages ahead, engraved while the song is stopped -------------------------------
    # A longer copy of the fixture, freshly imported: a new score, so engrave.js holds none
    # of its pages. Played the moment page 1 is up: an engrave is one long frame wherever
    # it lands, so nothing may engrave in idle time while the song runs.
    big = TR_BARS * 2
    page.evaluate(TR_OPEN, [transport_fixture(big), True])
    try:
        page.wait_for_function("document.querySelector('#roll-page-n').textContent.startsWith('1/')"
                               " && document.querySelectorAll('#roll-paper g.measure[data-bar]').length > 0",
                               timeout=60000)
    except Exception:  # noqa: BLE001 -- reported below
        pass
    page.evaluate(TURN_SPY)
    page.click("#ghost-wait")                  # wait off: the clock runs without a piano
    page.evaluate("""() => { const s = document.querySelector('#ghost-bpm'); s.value = '240';
      s.dispatchEvent(new Event('input', { bubbles: true })); }""")
    press("Space", 0)
    t_play = page.evaluate("performance.now()")
    page.wait_for_timeout(1500)
    playing = page.evaluate("[...window.__engraves]")
    t_stop = page.evaluate("performance.now()")
    press("Space", 200)
    page.click("#ghost-wait")                  # and back on
    during = [t for t in playing if t_play + 30 < t < t_stop]
    check("played straight away, nothing is engraved ahead while the song runs",
          read()["pageN"].startswith("1/") and not during, f"{len(during)} engrave(s) while playing")
    page.wait_for_timeout(2500)                # stopped: the pages ahead, one per idle moment

    def last_on() -> int:                      # the last bar printed on the page shown
        return page.evaluate("Math.max(...[...document.querySelectorAll('#roll-paper g.measure[data-bar]')]"
                             ".map((g) => +g.dataset.bar))")

    r = read()
    pages = int(r["pageN"].split("/")[1]) if "/" in r["pageN"] else 0
    end1 = last_on()
    check(f"a {big}-bar copy engraves to at least four pages", pages >= 4, r["pageN"])
    drawn = sorted({p for p, _ in page.evaluate("window.__drawn")})
    check("...and stopped, every page after the one shown is engraved ahead",
          drawn == list(range(2, pages + 1)), f"engraved {drawn} of {pages}")
    page.evaluate(TURN_SPY)

    # A scrub drag from page 1 into page 2, held there. Each step is a seek; none may
    # engrave or swap a page while the button is down -- page 3 included, the page after
    # the one under the pointer.
    sb = page.evaluate("""() => { const r = document.querySelector('#ghost-scrub').getBoundingClientRect();
      return { x: r.left, y: r.top, w: r.width, h: r.height }; }""")
    sy = sb["y"] + sb["h"] / 2

    def at(n: int) -> float:                   # x of the middle of written bar n of the copy
        return sb["x"] + ((n - 1) * 4 + 2) / (big * 4) * sb["w"]

    goal = end1 + 10                           # index end1 + 9: page 2, ten bars in
    page.mouse.move(at(2), sy)
    page.mouse.down()
    page.mouse.move(at(goal), sy, steps=16)
    page.wait_for_timeout(1500)
    during = page.evaluate("[window.__engraves.length, window.__swaps.length]")
    page.mouse.up()
    page.wait_for_function(f"document.querySelector('#roll-page-n').textContent === '2/{pages}'", timeout=8000)
    page.wait_for_timeout(800)
    r = read()
    check("a scrub drag across a page engraves and turns nothing until it lets go, then turns once",
          during == [0, 0] and r["pageN"] == f"2/{pages}" and r["now"] == [goal - 1],
          f"{during[0]} engrave(s) and {during[1]} swap(s) mid-drag, then page {r['pageN']}, current {r['now']}")

    # Playing across two turns, page 2 to 3 by Go to and 3 to 4 by playing over the break:
    # each turn swaps in a page engraved while stopped, and nothing engraves at all.
    page.click("#ghost-wait")                  # wait off: the clock runs without a piano
    page.evaluate("""() => { const s = document.querySelector('#ghost-bpm'); s.value = '240';
      s.dispatchEvent(new Event('input', { bubbles: true })); }""")
    end2 = last_on()

    def goto(n: int) -> None:
        page.click("#ghost-bar")
        page.keyboard.type(str(n))
        page.keyboard.press("Enter")

    press("Space", 300)
    page.evaluate(TURN_SPY)
    goto(end2 + 2)                             # index end2 + 1: page 3's first bar
    page.wait_for_function(f"document.querySelector('#roll-page-n').textContent === '3/{pages}'", timeout=8000)
    end3 = last_on()
    goto(end3 - 4)                             # index end3 - 5: five bars from the end of the page
    page.wait_for_function(f"document.querySelector('#roll-page-n').textContent === '4/{pages}'", timeout=12000)
    page.wait_for_timeout(300)
    r = read()
    press("Space", 200)
    page.click("#ghost-wait")                  # and back on, as the settings had it
    spy = page.evaluate(TURN_READ)
    check("playing over two turns engraves nothing: both pages were engraved while stopped",
          r["pageN"] == f"4/{pages}" and spy["turn"] is not None and spy["engraves"] == 0,
          f"now {r['pageN']}, {spy['engraves']} engrave(s) while playing, at {spy['drawn']}")

    # The tour over a play-along: Space on its buttons still presses them.
    page.evaluate("""async () => { const a = await import('/app.js');
      (await import('/tour.js')).startTutorial(a.ctx); }""")
    page.wait_for_timeout(200)
    page.focus("#tour .btn--lg")
    page.keyboard.press("Space")
    page.wait_for_timeout(200)
    title = page.evaluate("document.querySelector('#tour .tour__title')?.textContent")
    check("with a song loaded, Space presses the tour's Next", title == "Your keyboard", repr(title))
    page.evaluate("document.querySelector('#tour .tour__scrim')?.click()")
    page.close()
    return fails


def free_port() -> int:
    """A port nothing is listening on. Several checkouts run this suite at once, and a
    fixed port silently tests whichever app answered first."""
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]



# ── custom pages, and the highlight owners they need ─────────────────────────────────
# The same audit, aimed at one pane's grid at a time: a page holds several.
PANE_AUDIT = AUDIT.replace(
    "() => {\n  const grid = document.querySelector('#stage .grid');\n"
    "  if (!grid) return { error: 'no grid in #stage' };",
    "(sel) => {\n  const grid = document.querySelector(sel);\n"
    "  if (!grid) return { error: 'no grid at ' + sel };")
assert PANE_AUDIT != AUDIT, "AUDIT's opening changed; PANE_AUDIT has to follow it"

# What a view's own grid says its panels are: layout.js's data-panel on each panel, and
# the data-slot of a nested grid that is not one (Practice's exercise shelf).
DOM_PANELS = """() => [...document.querySelector('#stage > .grid').children]
  .map((e) => e.dataset.panel || e.dataset.slot).filter(Boolean)"""

# Every dock key that is lit, and every dock key there is.
LIT = """() => ({
  lit: [...document.querySelectorAll('#dock .key.is-highlight')].map((k) => +k.dataset.midi),
  all: [...document.querySelectorAll('#dock .key')].map((k) => +k.dataset.midi) })"""

# Per pane: which panels show, read off the DOM.
SHOWN = """() => [...document.querySelectorAll('.page__pane')].map((p) => ({
  view: p.dataset.view,
  shown: [...p.querySelectorAll(':scope > .grid > *')]
    .filter((e) => e.getBoundingClientRect().height > 0)
    .map((e) => e.dataset.panel || e.dataset.slot).filter(Boolean) }))"""

# Every pane on the page, measured as a user meets it: no control past the pane's sides
# (the pane does not scroll sideways, so one there cannot be reached -- Chords & scales'
# Bb and B were), and no pane cut to a sliver. A pane may be short only if it shows all
# it holds; otherwise it gets SLIVER_PX at least.
PANE_FIT = """() => {
  const out = [];
  for (const pane of document.querySelectorAll('.page__pane')) {
    const pr = pane.getBoundingClientRect();
    const v = pane.dataset.view;
    if (pr.height < SLIVER && pane.scrollHeight > pane.clientHeight + 1) {
      out.push(`${v}: a ${Math.round(pr.height)}px sliver over ${pane.scrollHeight}px of panels`);
    }
    for (const el of pane.querySelectorAll('button, select, input, a[href]')) {
      if (el.closest('.is-unpicked')) continue;
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height) continue;
      // Inside a sideways scroller of its own (the interval ribbon), it can be scrolled to.
      let n = el.parentElement, own = false;
      for (; n && n !== pane; n = n.parentElement) {
        const ox = getComputedStyle(n).overflowX;
        if ((ox === 'auto' || ox === 'scroll') && n.scrollWidth > n.clientWidth) { own = true; break; }
      }
      if (own || (r.right <= pr.right + 1 && r.left >= pr.left - 1)) continue;
      const what = (el.textContent || el.title || el.id || el.type || '').trim().slice(0, 16);
      out.push(`${v}: ${el.tagName.toLowerCase()} "${what}" at x ${Math.round(r.left)}-${Math.round(r.right)}, `
        + `pane ${Math.round(pr.left)}-${Math.round(pr.right)}`);
    }
  }
  return out;
}""".replace("SLIVER", "300")

# The three starters as the spec words them -- not read from pages.js, which is what is
# being tested. Blank gets two panels ticked by hand.
STARTER_WANT = {
    "solo": {"practice": ["jam"], "play": ["scale-highlighter", "tempo"], "tools": ["chords-scales"]},
    "everything": None,          # every panel of all four views: checked against the DOM
    "blank": {"tools": ["pedal"], "practice": ["now"]},
}


# A stringified nothing anywhere in the stage, the builder's footer included: the AUDIT
# above walks only a tab's grid.
JUNK = """() => {
  const out = [];
  const w = document.createTreeWalker(document.getElementById('stage'), NodeFilter.SHOW_TEXT);
  for (let n = w.nextNode(); n; n = w.nextNode()) {
    if (/^(null|undefined|NaN|\\[object Object\\])+$/.test(n.textContent.trim())) {
      out.push(`"${n.textContent.trim()}" in #${n.parentElement.id || n.parentElement.className}`);
    }
  }
  return out;
}"""

# Jam's pane on a page, the band playing: is the Now chord, the Next chord and the strip
# where you can see them -- inside the pane, and the part of it the panes area shows?
JAM_SEEN = """() => {
  const pane = document.querySelector('.page__pane[data-view="practice"]');
  const p = pane.getBoundingClientRect(), a = pane.closest('.page__panes').getBoundingClientRect();
  const top = Math.max(p.top, a.top), bottom = Math.min(p.bottom, a.bottom);
  const r = (s) => pane.querySelector(s)?.getBoundingClientRect();
  const inside = (b) => !!b && b.height > 0 && b.top >= top - 1 && b.bottom <= bottom + 1;
  const strip = r('.jam__mid .strip');
  return { go: inside(r('.jam__go')), now: inside(r('.jam__sym:not(.jam__sym--next)')),
           next: inside(r('.jam__sym--next')),
           strip: !!strip && strip.height > 0 && (Math.min(strip.bottom, bottom) - strip.top) / strip.height,
           pane: [Math.round(top), Math.round(bottom)], sym: Math.round(r('.jam__sym')?.top ?? -1) };
}"""

# Under Jam's flow fills, a highlight owner must change no key's fill (it lit exactly the
# keys Jam leaves dark, in Jam's chord colour) and must draw a ring Jam does not use. Each
# highlighted key is read as it is, then with its is-highlight taken off for a moment.
HL_FILLS = """() => {
  const keys = [...document.querySelectorAll('#dock .key')];
  for (const k of keys) k.style.transition = 'none';
  const lit = keys.filter((k) => k.classList.contains('is-highlight'));
  const cs = (k) => getComputedStyle(k);
  const ring = (k) => !k.classList.contains('is-suggest2') && !k.classList.contains('is-sig');
  // Jam's chord colour and its pin's, as the dock resolves them.
  const colour = (v) => { const e = document.createElement('span'); e.style.color = `var(${v})`;
    document.getElementById('dock').append(e); const c = getComputedStyle(e).color; e.remove(); return c; };
  const toneFill = colour('--key-tone'), pinStroke = colour('--key-pin');
  const out = { flow: document.querySelector('#dock .keys-kb').classList.contains('kb--flow'),
    lit: lit.length, changed: [], plain: [], toneFill, sameAsTone: 0, sameAsPin: 0, dashedElsewhere: 0 };
  const withHl = lit.map((k) => [cs(k).fill, cs(k).stroke, cs(k).strokeDasharray]);
  lit.forEach((k) => k.classList.remove('is-highlight'));
  lit.forEach((k, i) => {
    if (cs(k).fill !== withHl[i][0]) out.changed.push(`${k.dataset.midi}: ${withHl[i][0]} vs ${cs(k).fill}`);
    if (ring(k) && (withHl[i][2] === 'none' || cs(k).strokeDasharray !== 'none')) out.plain.push(k.dataset.midi);
    if (withHl[i][1] === toneFill) out.sameAsTone++;
    if (withHl[i][1] === pinStroke) out.sameAsPin++;
  });
  lit.forEach((k) => k.classList.add('is-highlight'));
  out.dashedElsewhere = keys.filter((k) => !lit.includes(k) && cs(k).strokeDasharray !== 'none').length;
  for (const k of keys) k.style.transition = '';
  return out;
}"""

# Every shortcut 0.13.0 shipped, as it shipped them, with the metronome moved to 7 -- a
# map saved before pages existed. Written out here, not read from app.js.
KEYS_013 = {"view:play": "1", "view:practice": "2", "view:layers": "3", "view:tools": "4",
            "view:stats": "5", "view:settings": "6", "panic": "Escape", "metronome": "7",
            "roll": "v", "immersive": "f", "focus": "g", "songs": "s", "settings": ",",
            "octave-down": "z", "octave-up": "x"}
# And 0.14's own, page shortcuts included, to put back afterwards.
KEYS_014 = {**KEYS_013, "metronome": "m", "page:1": "7", "page:2": "8", "page:3": "9"}

METRO = "async () => (await (await fetch('/api/state')).json()).metronome.running"


def pages_checks(browser, port: int, errs: list[str]) -> list[str]:
    """Pages: the registry against the DOM, the builder, panes, hotkeys, reload; and
    Chords & scales lighting the whole dock without the touch keys wiping it."""
    fails: list[str] = []
    page = browser.new_page(viewport={"width": 1600, "height": 1000})
    page.on("pageerror", lambda e: errs.append(str(e)))

    def check(label: str, passed: bool, detail: str = "") -> None:
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))
        if not passed:
            fails.append(f"pages: {label} {detail}".strip())

    def go(hash_: str, wait: int = 2200) -> None:
        page.evaluate(f"location.hash = '{hash_}'")
        page.wait_for_timeout(wait)

    print("\npages, and the dock's highlight owners")
    page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
    page.wait_for_timeout(1600)

    # 1. The builder's panel list is the panels the views really render.
    registry = page.evaluate("async () => (await import('/pages.js')).PANELS")
    every = {}
    for view in ("play", "practice", "layers", "tools", "stats"):
        go(view)
        dom = page.evaluate(DOM_PANELS)
        every[view] = dom
        reg = [p["slug"] for p in registry if p["view"] == view]
        check(f"registry = the panels {view} renders", sorted(dom) == sorted(reg),
              f"DOM {dom} vs registry {reg}")

    # 2. Chords & scales lights every key of the scale across the dock (spec D).
    go("tools")
    page.click("#th-roots button:text-is('B')")
    page.wait_for_timeout(400)
    page.select_option("#th-shape", "minor_pentatonic")
    page.wait_for_timeout(800)
    want_pcs = {11, 2, 4, 6, 9}            # B D E F# A
    k = page.evaluate(LIT)
    want = sorted(n for n in k["all"] if n % 12 in want_pcs)
    check("B minor pentatonic lights every B D E F# A on the dock, and nothing else",
          sorted(k["lit"]) == want and len(want) > 20, f"{len(k['lit'])} lit, {len(want)} expected")
    page.click("#touch-show")
    page.wait_for_timeout(300)
    on = page.evaluate(LIT)["lit"]
    check("...touch keys add theirs on top", set(on) == set(want) | {57, 58, 59, 60}, f"{len(on)} lit")
    page.click("#touch-show")
    page.wait_for_timeout(300)
    off = page.evaluate(LIT)["lit"]
    check("...and turning them off leaves the scale exactly as it was", sorted(off) == want,
          f"{len(off)} lit")
    # A new range rebuilds the dock and drops every layer; the status beat puts it back.
    page.evaluate("async () => (await import('/app.js')).ctx.kb.setRange(36, 96)")
    page.wait_for_timeout(1600)
    k = page.evaluate(LIT)
    narrow = sorted(n for n in range(36, 97) if n % 12 in want_pcs)
    check("...and after a range change it comes back within a beat, over the new range",
          sorted(k["lit"]) == narrow, f"{len(k['lit'])} lit, {len(narrow)} expected")
    page.evaluate("""async () => { const a = await import('/app.js');
      a.ctx.kb.setRange(a.instrument().low, a.instrument().high); }""")
    page.wait_for_timeout(1300)

    # 3. Each starter, made through the builder.
    go("page/new", 600)
    junk = page.evaluate(JUNK)
    check("a new page's builder prints no stringified nothing (its footer's absent buttons)",
          not junk, "; ".join(junk))
    made = {}
    for key in ("solo", "everything", "blank"):
        go("page/new", 600)
        page.click(f"#pb-start [data-start='{key}']")
        if key == "blank":
            for view, slugs in STARTER_WANT["blank"].items():
                for slug in slugs:
                    page.click(f"label:has(> input[data-view='{view}'][data-slug='{slug}'])")
        page.fill("#pb-name", f"Test {key}")
        page.click("#pb-save")
        try:
            page.wait_for_function("/^#page\\/p[a-z0-9]+$/.test(location.hash)", timeout=5000)
        except Exception:  # noqa: BLE001 -- the failure is reported below
            pass
        made[key] = page.evaluate("location.hash.slice(6)")
        page.wait_for_timeout(2600)
        shown = {p["view"]: sorted(p["shown"]) for p in page.evaluate(SHOWN)}
        want = STARTER_WANT[key] or every
        want = {v: sorted(s) for v, s in want.items()}
        check(f"{key}: the builder makes a page showing exactly what was picked",
              made[key] and shown == want, f"shown {shown}")
    navs = page.evaluate("[...document.querySelectorAll('#nav-pages .nav__item')].map(a => a.textContent)")
    check("...and each is a nav item under the tabs", len(navs) == 3, str(navs))
    # A page's shortcut prints under the tabs as Settings prints it: Space, not a blank chip.
    page.evaluate("async () => (await import('/app.js')).setBinds({ 'page:1': ' ' })")
    chip = page.evaluate("document.querySelector('#nav-pages .nav__key')?.textContent")
    page.evaluate("async () => (await import('/app.js')).setBinds({})")
    check("...a page bound to Space says Space on its chip", chip == "Space", repr(chip))

    # 4. Every page at every size, each pane audited like a tab.
    for w, h in WIDTHS:
        page.set_viewport_size({"width": w, "height": h})
        for key, pid in made.items():
            go(f"page/{pid}", 2600)
            panes = page.evaluate("[...document.querySelectorAll('.page__pane')].map(p => p.dataset.view)")
            bad = []
            for v in panes:
                a = page.evaluate(PANE_AUDIT, f".page__pane[data-view='{v}'] > .grid")
                if a.get("error"):
                    bad.append(f"{v}: {a['error']}")
                    continue
                bad += [f"{v}: {b}" for b in a["over"] + a["junk"] + a["hollow"]]
                bad += [f"{v}: {t} has no row span" for t in a["unspanned"]]
            bad += page.evaluate(PANE_FIT)
            check(f"{key} @{w}x{h}: {len(panes)} panes, no overlap, no gaps, nothing stringified, "
                  "every control in reach, no slivers",
                  panes and not bad, "; ".join(bad))
    page.set_viewport_size({"width": 1600, "height": 1000})

    # 5. Solo practice: the band, Play's scale highlighter and Jam's marks on one dock.
    # Chords & scales opens on C major; on a page it lights nothing until asked, or the
    # whole dock would be its scale before Jam had lit a key.
    go(f"page/{made['solo']}", 2600)
    quiet = page.evaluate(LIT)["lit"]
    asked = page.evaluate("document.getElementById('th-dock')?.classList.contains('is-on')")
    check("Solo practice: Chords & scales leaves the dock alone until Show on keys is pressed",
          quiet == [] and asked is False, f"{len(quiet)} lit, toggle on: {asked}")
    for w, h in ((1280, 800), (1600, 1000)):
        page.set_viewport_size({"width": w, "height": h})
        go(f"page/{made['solo']}", 2600)
        page.click(".page__pane[data-view='practice'] .jam__go")
        page.wait_for_timeout(2600)
        seen = page.evaluate(JAM_SEEN)
        check(f"Solo practice @{w}x{h}, the band playing: Stop, the Now and Next chords and most of the "
              "strip are in Jam's pane without scrolling it",
              seen["go"] and seen["now"] and seen["next"] and seen["strip"] >= 0.6, str(seen))
        page.click(".page__pane[data-view='practice'] .jam__go")
        page.wait_for_timeout(600)
    page.set_viewport_size({"width": 1600, "height": 1000})
    go(f"page/{made['solo']}", 2600)
    page.click(".page__pane[data-view='practice'] .jam__go")
    # C blues on Play's highlighter, beside Chords & scales' C major: two owners whose
    # union is checked, so neither can be hiding behind the other.
    page.click("#th-dock")
    page.select_option("#scale-mode", "blues")
    page.click("#scale-toggle")
    page.wait_for_timeout(1800)
    union = {0, 3, 5, 6, 7, 10} | {0, 2, 4, 5, 7, 9, 11}
    k = page.evaluate(LIT)
    want = sorted(n for n in k["all"] if n % 12 in union)
    jam = page.evaluate("document.querySelectorAll('#dock .key.is-tone, #dock .key.is-scale').length")
    check("Solo practice: the band plays", page.evaluate(BAND).get("running") is True)
    check("...and the scale highlighter, Chords & scales and Jam's marks are on the dock together",
          sorted(k["lit"]) == want and jam > 0, f"{len(k['lit'])} lit of {len(want)}, {jam} Jam marks")
    fills = page.evaluate(HL_FILLS)
    check("...where the highlight changes no key's fill, Jam's lit or unlit, and rings each of its keys "
          "dashed, in neither Jam's chord colour nor its pin's",
          fills["flow"] and fills["lit"] == len(want) and not fills["changed"] and not fills["plain"]
          and fills["toneFill"] and fills["sameAsTone"] == 0 and fills["sameAsPin"] == 0
          and fills["dashedElsewhere"] == 0, str(fills)[:600])
    # The same under Fading, Dark and a pin, where Jam paints its marked keys plain.
    for guide, pin in (("Dark", "Key"), ("Fading", "Pentatonic")):
        page.click(f".page__pane[data-view='practice'] .jam__opt--guide .seg__btn:text-is('{guide}')")
        page.click(f".page__pane[data-view='practice'] .jam__opt--pin .seg__btn:text-is('{pin}')")
        page.wait_for_timeout(1500)
        fills = page.evaluate(HL_FILLS)
        check(f"...and with the guide on {guide} and a {pin} pin",
              fills["flow"] and fills["lit"] > 0 and not fills["changed"] and not fills["plain"]
              and fills["dashedElsewhere"] == 0, str(fills)[:600])
    page.click(".page__pane[data-view='practice'] .jam__opt--guide .seg__btn:text-is('Full')")
    page.click(".page__pane[data-view='practice'] .jam__opt--pin .seg__btn:text-is('Off')")
    page.wait_for_timeout(600)
    page.evaluate("async () => (await import('/app.js')).ctx.kb.setRange(36, 96)")
    page.wait_for_timeout(1600)
    lit = sorted(page.evaluate(LIT)["lit"])
    narrow = sorted(n for n in range(36, 97) if n % 12 in union)
    check("...and both come back after a range change", lit == narrow, f"{len(lit)} lit of {len(narrow)}")
    page.evaluate("""async () => { const a = await import('/app.js');
      a.ctx.kb.setRange(a.instrument().low, a.instrument().high); }""")
    page.wait_for_timeout(1300)
    page.click(".mod:has(.jam) .mod__focus")
    page.wait_for_timeout(400)
    f = page.evaluate("""() => ({ on: document.body.classList.contains('is-focusing'),
      jam: !!document.querySelector('[data-focus="on"] .jam'),
      others: [...document.querySelectorAll('.page__pane')].filter(p => !p.dataset.focus
        && getComputedStyle(p).display !== 'none').length,
      fill: document.querySelector('[data-focus="on"]').getBoundingClientRect().height
        / (() => { const s = document.getElementById('stage'), c = getComputedStyle(s);
          return s.clientHeight - parseFloat(c.paddingTop) - parseFloat(c.paddingBottom); })() })""")
    check("...Focus works inside a page: Jam alone, filling the stage, the other panes put away",
          f["on"] and f["jam"] and f["others"] == 0 and f["fill"] >= 0.97, str(f))
    page.evaluate("document.activeElement?.blur?.()")
    page.keyboard.press("Escape")
    page.wait_for_timeout(1300)
    check("...Esc leaves focus and the band plays on",
          not page.evaluate("document.body.classList.contains('is-focusing')")
          and page.evaluate(BAND).get("running") is True)

    # A view that rebuilds itself (saving a profile does) rebuilds its own pane only:
    # the Jam beside it, and the band, carry on.
    go(f"page/{made['everything']}", 3000)
    page.click(".page__pane[data-view='practice'] .jam__go")
    page.wait_for_timeout(1200)
    page.evaluate("document.querySelector('.jam').__kept = 1")
    page.click("#shelf-profiles")
    page.fill("#preset-name", "Page check")
    page.press("#preset-name", "Enter")
    page.wait_for_timeout(3000)
    kept = page.evaluate("""() => ({ jam: document.querySelector('.jam')?.__kept === 1,
      play: document.querySelectorAll('.page__pane[data-view="play"] [data-panel]').length,
      saved: [...document.querySelectorAll('#profile-list .list__row')].some(r => r.textContent.includes('Page check')) })""")
    check("a pane that refreshes rebuilds alone: Jam untouched, the band still playing",
          kept["jam"] and kept["play"] == len(every["play"]) and kept["saved"] and page.evaluate(BAND).get("running") is True,
          str(kept))

    # 6. Leaving the page stops the band and clears every highlight owner.
    go("play", 2200)
    left = page.evaluate("document.querySelectorAll('#dock .key.is-highlight').length")
    check("leaving the page stops the band and clears the dock",
          page.evaluate(BAND).get("running") is not True and left == 0, f"{left} still lit")

    # 7. Hotkey 7 is page 1, and a reload comes back to it.
    page.evaluate("document.activeElement?.blur?.()")
    page.keyboard.press("7")
    page.wait_for_timeout(1800)
    check("hotkey 7 opens page 1", page.evaluate("location.hash") == f"#page/{made['solo']}")
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(2600)
    again = page.evaluate(SHOWN)
    check("a reload restores the page and its panes",
          page.evaluate("location.hash") == f"#page/{made['solo']}" and len(again) == 3
          and page.evaluate("document.querySelectorAll('#nav-pages .nav__item').length") == 3, str(again))

    # 7b. Shortcuts saved by 0.13, before pages had any: a metronome on 7 stays the
    #     metronome, and page 1 starts unset instead of shadowing it.
    post = """(keys) => fetch('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ keys }) })"""
    page.evaluate(post, KEYS_013)
    go("play", 300)
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(2000)
    binds = page.evaluate("async () => (await import('/app.js')).getBinds()")
    page.evaluate("document.activeElement?.blur?.()")
    page.keyboard.press("7")
    page.wait_for_timeout(1200)
    ran, where = page.evaluate(METRO), page.evaluate("location.hash")
    check("a 0.13 map with the metronome on 7: 7 starts the metronome, page 1 is left unset",
          ran is True and where == "#play" and binds.get("metronome") == "7" and not binds.get("page:1")
          and binds.get("page:2") == "8", f"running {ran}, at {where}, binds {binds}")
    page.keyboard.press("7")
    page.wait_for_timeout(600)
    page.evaluate(post, KEYS_014)
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(2000)

    # 8. Edit, then delete with the inline confirm.
    go(f"page/{made['blank']}", 1800)
    page.click(".page__edit")
    page.wait_for_timeout(500)
    junk = page.evaluate(JUNK)
    page.click("#pb-delete")
    junk += page.evaluate(JUNK)
    check("an edited page's builder prints no stringified nothing, before Delete and while it asks",
          not junk, "; ".join(junk))
    page.click("#pb-yes")
    page.wait_for_timeout(1200)
    names = page.evaluate("[...document.querySelectorAll('#nav-pages .nav__item')].map(a => a.textContent)")
    saved = page.evaluate("async () => (await (await fetch('/api/state')).json()).settings.ui.pages.length")
    check("Delete page asks inline, then the page is gone and saved gone", len(names) == 2 and saved == 2,
          f"nav {names}, saved {saved}")
    page.close()
    return fails


# Counts what the app has hooked to the window and the DOM, from before its first line
# runs: resize listeners on window, ResizeObservers not yet disconnected, and every
# WebSocket (so a check can hand the app a frame as if the server sent it).
HOOKS = """(() => {
  const add = window.addEventListener, rem = window.removeEventListener;
  window.__resize = new Set();
  window.addEventListener = function (t, f, o) {
    if (t === 'resize') window.__resize.add(f);
    return add.call(this, t, f, o);
  };
  window.removeEventListener = function (t, f, o) {
    if (t === 'resize') window.__resize.delete(f);
    return rem.call(this, t, f, o);
  };
  const RO = window.ResizeObserver;
  window.__ro = new Set();
  window.ResizeObserver = class extends RO {
    constructor(cb) { super(cb); window.__ro.add(this); }
    disconnect() { window.__ro.delete(this); return super.disconnect(); }
  };
  const WS = window.WebSocket;
  window.__ws = [];
  window.WebSocket = class extends WS {
    constructor(...a) { super(...a); window.__ws.push(this); }
  };
})()"""

HOOKED = "() => ({ resize: window.__resize.size, ro: window.__ro.size })"

ENGINE = """async () => (await (await fetch('/api/state')).json()).engine.zones
  .map((z) => [z.program, z.name, z.enabled])"""


def pages_fix_checks(browser, port: int, errs: list[str]) -> list[str]:
    """What reviewers found once several views shared a document: Layers beside Play,
    what a page leaves behind, hidden panels doing work, labels, focus, the builder."""
    fails: list[str] = []
    page = browser.new_page(viewport={"width": 1600, "height": 1000})
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.add_init_script(HOOKS)

    def check(label: str, passed: bool, detail: str = "") -> None:
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))
        if not passed:
            fails.append(f"pages+: {label} {detail}".strip())

    def go(hash_: str, wait: int = 2200) -> None:
        page.evaluate(f"location.hash = '{hash_}'")
        page.wait_for_timeout(wait)

    def make(name: str, picks: dict[str, list[str]], cols: int) -> str:
        go("page/new", 700)
        page.click("#pb-start [data-start='blank']")
        for view, slugs in picks.items():
            for slug in slugs:
                page.click(f"label:has(> input[data-view='{view}'][data-slug='{slug}'])")
        page.click(f"#pb-cols button:text-is('{cols}')")
        page.fill("#pb-name", name)
        page.click("#pb-save")
        page.wait_for_function("/^#page\\/p[a-z0-9]+$/.test(location.hash)", timeout=5000)
        page.wait_for_timeout(2600)
        return page.evaluate("location.hash.slice(6)")

    print("\npages, once several views share a document")
    page.goto(f"http://127.0.0.1:{port}/#play", wait_until="networkidle")
    page.wait_for_timeout(2200)
    pages = page.evaluate("async () => (await (await fetch('/api/settings')).json()).ui.pages")
    ids = {p["name"]: p["id"] for p in pages}
    solo, everything = ids.get("Test solo"), ids.get("Test everything")

    # 1. The builder: which starter it holds, and Save in reach without scrolling.
    page.set_viewport_size({"width": 1280, "height": 720})
    go("page/new", 800)
    on = page.evaluate("[...document.querySelectorAll('#pb-start .is-on')].map(b => b.dataset.start)")
    check("a new page's builder shows Solo practice as the starter it holds", on == ["solo"], str(on))
    reach = page.evaluate("""() => { const b = document.getElementById('pb-save').getBoundingClientRect();
      const s = document.getElementById('stage').getBoundingClientRect();
      return { inside: b.top >= s.top && b.bottom <= s.bottom,
               hit: document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2)?.id }; }""")
    check("...and Make the page is on screen at 1280x720 without a scroll",
          reach["inside"] and reach["hit"] == "pb-save", str(reach))
    page.click("label:has(> input[data-view='tools'][data-slug='pedal'])")
    on = page.evaluate("document.querySelectorAll('#pb-start .is-on').length")
    check("...and a panel ticked by hand means no starter is lit", on == 0, f"{on} lit")
    page.click("#pb-cancel")
    page.wait_for_timeout(600)
    page.set_viewport_size({"width": 1600, "height": 1000})

    # 2. Layers beside Play: Play's pick reaches the zone editor, and the editor's
    #    toggle applies on top of it instead of posting a stale copy back.
    make("Zones", {"play": ["instruments"], "layers": ["zone-editor"]}, 2)
    rows = page.evaluate("""() => [...document.querySelectorAll('#inst-list .list__row')]
      .map((r) => [+r.children[0].textContent, r.children[1].textContent])""")
    first = rows[40] if len(rows) > 40 else None
    page.click("#inst-list .list__row >> nth=40")
    page.wait_for_timeout(1600)
    shown = page.evaluate("document.getElementById('zname-0')?.textContent")
    check("Play's instrument pick shows in the Layers pane beside it within a beat",
          first is not None and shown == first[1], f"picked {first}, Layers shows {shown!r}")
    toggle = "document.querySelector('#zone-list .zone__head label.toggle').click()"
    page.evaluate(toggle)
    page.wait_for_timeout(700)
    z = page.evaluate(ENGINE)
    check("...and turning the zone off there keeps Play's instrument",
          first is not None and z[:1] == [[first[0], first[1], False]], str(z))
    page.evaluate(toggle)
    page.wait_for_timeout(700)
    # Inside the beat: the pick's POST answered, no status frame since.
    second = rows[24] if len(rows) > 24 else None
    page.evaluate("""async () => { const a = await import('/app.js');
      window.__eng = a.ctx.state.engine; }""")
    page.click("#inst-list .list__row >> nth=24")
    page.wait_for_function("""async () => (await import('/app.js')).ctx.state.engine !== window.__eng""",
                           timeout=3000)
    page.evaluate(toggle)
    page.wait_for_timeout(700)
    z = page.evaluate(ENGINE)
    check("...even toggled straight after the pick, before a heartbeat",
          second is not None and z[:1] == [[second[0], second[1], False]], str(z))
    page.evaluate(toggle)
    page.wait_for_timeout(500)

    # 3. A one-column page is a stack, and a 2x2 on a short window is not four slivers.
    sid = make("Stack", {"practice": ["songs"], "tools": ["chords-scales"], "play": ["instruments"]}, 1)
    page.set_viewport_size({"width": 1280, "height": 720})
    for name, pid in (("Stack", sid), ("Everything", everything)):
        go(f"page/{pid}", 2600)
        hs = page.evaluate("""[...document.querySelectorAll('.page__pane')]
          .map(p => Math.round(p.getBoundingClientRect().height))""")
        bad = page.evaluate(PANE_FIT)
        check(f"{name} @1280x720: panes {hs}px tall, none a sliver, every control in reach",
              bool(pid) and bool(hs) and not bad, "; ".join(bad))
    go(f"page/{sid}", 1800)
    inner = page.evaluate("""() => [...document.querySelectorAll('.page__pane')]
      .filter((p) => p.scrollHeight > p.clientHeight + 1).map((p) => p.dataset.view)""")
    check("...and a one-column page has no scrollers inside it, only the page", inner == [], str(inner))
    page.set_viewport_size({"width": 1280, "height": 860})
    go(f"page/{solo}", 2600)
    b = page.evaluate("""() => { const el = [...document.querySelectorAll('#th-roots button')]
        .find((x) => x.textContent === 'B');
      el.scrollIntoView({ block: 'center' });
      const r = el.getBoundingClientRect();
      return document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)?.textContent; }""")
    check("Solo practice @1280x860: Chords & scales' B root is on screen to click", b == "B", repr(b))
    page.set_viewport_size({"width": 1600, "height": 1000})

    # 4. Hidden panels cost nothing per frame. Solo practice keeps Tools' Touch response
    #    in the DOM, unpicked; a frame with notes in it must not rebuild it. Counted per
    #    frame, synchronously, so the 1 Hz status beat landing in between is not.
    go(f"page/{solo}", 2600)
    added = page.evaluate("""async () => {
      let n = 0;
      const mo = new MutationObserver(() => {});
      mo.observe(document.getElementById('stage'), { childList: true, subtree: true });
      const ws = window.__ws[window.__ws.length - 1];
      for (let i = 0; i < 60; i++) {
        mo.takeRecords();
        ws.onmessage({ data: JSON.stringify({ t: 'f', on: [[60 + (i % 12), 40 + i]] }) });
        for (const m of mo.takeRecords()) {
          if (m.target.closest?.('.is-unpicked')) n += m.addedNodes.length;
        }
        await new Promise((r) => requestAnimationFrame(r));
      }
      mo.disconnect();
      return { n, hidden: !!document.querySelector('.is-unpicked #touch-fill') };
    }""")
    check("60 frames of notes add nothing inside unpicked panels",
          added["hidden"] and added["n"] == 0, str(added))

    # 5. Focus on a page with a run waiting on the server: G still focuses Jam.
    page.evaluate("fetch('/api/exercises/reading/start', { method: 'POST' })")
    page.wait_for_timeout(500)
    go("play", 1500)
    go(f"page/{solo}", 2600)
    page.evaluate("document.activeElement?.blur?.()")
    page.keyboard.press("g")
    page.wait_for_timeout(500)
    f = page.evaluate("""() => ({ on: document.body.classList.contains('is-focusing'),
      jam: !!document.querySelector('[data-focus="on"] .jam, [data-focus="on"].jam'),
      run: !!document.querySelector('.page .exrun') })""")
    check("G focuses Jam on a page while an exercise run waits, and the run stays off the page",
          f["on"] and f["jam"] and not f["run"], str(f))
    page.keyboard.press("Escape")
    page.wait_for_timeout(400)
    page.evaluate("fetch('/api/exercises/stop', { method: 'POST' })")
    page.wait_for_timeout(300)

    # 6. Labels have owners: the band relabels a key every bar and every beat, and the
    #    touch keys' names stay.
    make("Touch", {"practice": ["jam"], "tools": ["touch-response"]}, 2)
    page.click("#touch-show")
    page.click(".page__pane[data-view='practice'] .jam__go")
    page.wait_for_timeout(3500)
    labels = page.evaluate("[...document.querySelectorAll('#dock .key-label.is-forced')].map(t => t.textContent)")
    marks = page.evaluate("document.querySelectorAll('#dock .key.is-tone').length")
    check("with the band playing beside it, the touch keys keep FIXED SOFT MEDIUM HARD",
          {"FIXED", "SOFT", "MEDIUM", "HARD"} <= set(labels) and marks > 0,
          f"labels {labels}, {marks} Jam marks")

    # 7. Settings' reset keeps the pages you built, and the nav keeps showing them.
    before = page.evaluate("document.querySelectorAll('#nav-pages .nav__item').length")
    page.evaluate("""fetch('/api/data/reset', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ what: 'settings', confirm: 'settings' }) })""")
    page.wait_for_timeout(600)
    go("play", 1500)
    kept = page.evaluate("async () => ((await (await fetch('/api/settings')).json()).ui.pages || []).length")
    check("a Settings reset keeps every page", kept == before and before == 5, f"{kept} of {before}")

    # 8. Leaving a page leaves nothing behind: as many window listeners and observers
    #    after ten round trips through the four-pane page as before them.
    go("play", 1800)
    base = page.evaluate(HOOKED)
    for _ in range(10):
        go(f"page/{everything}", 1400)
        go("play", 900)
    page.wait_for_timeout(800)
    after = page.evaluate(HOOKED)
    check("ten visits to a four-pane page leak no resize listener and no observer",
          after == base, f"before {base}, after {after}")
    page.close()
    return fails


# ── the nav: folded to icons, a page's icon, its right-click menu; the builder's foot ──
# Per pane of the page on screen: the gap from the last panel of one pane to the first
# panel of the next (the panels' own edges, which is what the eye reads as the gap),
# and the width of each pane's scrollbar.
PANE_GAPS = """() => {
  const panes = [...document.querySelectorAll('.page__pane')];
  const edge = (p, side) => {
    const xs = [...p.querySelectorAll(':scope > .grid > [data-panel]')]
      .filter((e) => e.getBoundingClientRect().height > 0)
      .map((e) => e.getBoundingClientRect()[side]);
    return side === 'right' ? Math.max(...xs) : Math.min(...xs);
  };
  const gaps = [];
  for (let i = 1; i < panes.length; i++) {
    const a = panes[i - 1].getBoundingClientRect(), b = panes[i].getBoundingClientRect();
    if (Math.abs(a.top - b.top) > 2) continue;       // the next row, not the next column
    gaps.push(Math.round(edge(panes[i], 'left') - edge(panes[i - 1], 'right')));
  }
  return { gaps, bars: panes.map((p) => p.offsetWidth - p.clientWidth) };
}"""

# The builder card, its scrolling middle and its footer, measured.
FOOT = """() => {
  const card = document.querySelector('.page__builder > .mod').getBoundingClientRect();
  const body = document.querySelector('.pb__body');
  const foot = document.getElementById('pb-foot').getBoundingClientRect();
  const stage = document.getElementById('stage');
  return { cardBottom: Math.round(card.bottom), footBottom: Math.round(foot.bottom),
           footTop: Math.round(foot.top), stageBottom: Math.round(stage.getBoundingClientRect().bottom),
           stageScrolls: stage.scrollHeight > stage.clientHeight + 1,
           bodyScrolls: body.scrollHeight > body.clientHeight + 1 };
}"""


def nav_checks(browser, port: int, errs: list[str]) -> list[str]:
    """The nav folded to icons and back, saved; a page's icon from the builder's picker;
    right-click Edit / Rename / Delete, inline; the builder's footer on its bottom edge;
    Stats on a page; and panes that read as one workspace."""
    fails: list[str] = []
    page = browser.new_page(viewport={"width": 1600, "height": 1000})
    page.on("pageerror", lambda e: errs.append(str(e)))

    def check(label: str, passed: bool, detail: str = "") -> None:
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))
        if not passed:
            fails.append(f"nav: {label} {detail}".strip())

    def go(hash_: str, wait: int = 2200) -> None:
        page.evaluate(f"location.hash = '{hash_}'")
        page.wait_for_timeout(wait)

    print("\nthe nav: folding, page icons, the right-click menu; the builder's foot; Stats on a page")
    page.goto(f"http://127.0.0.1:{port}/#play", wait_until="networkidle")
    # The tour put away again: the Settings reset in the section before brought it back.
    page.evaluate("""() => fetch('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ui: { tour_seen: true } }) })""")
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(1600)
    saved_pages =page.evaluate("async () => (await (await fetch('/api/settings')).json()).ui.pages || []")

    # 1. Fold: icons only, each named by its tooltip, the number keys as before, kept.
    page.click("#nav-fold")
    page.wait_for_timeout(500)
    folded = page.evaluate("""() => ({
      w: document.getElementById('nav').getBoundingClientRect().width,
      labels: [...document.querySelectorAll('#nav .nav__label, #nav .nav__key')]
        .filter((e) => e.getBoundingClientRect().width > 0).length,
      untitled: [...document.querySelectorAll('#nav .nav__item')].filter((a) => !a.title).length,
      stage: document.getElementById('stage').getBoundingClientRect().left })""")
    check("Fold: the nav is a ~52px strip of icons, no label or key chip showing, every item titled",
          folded["w"] <= 56 and folded["labels"] == 0 and folded["untitled"] == 0, str(folded))
    page.evaluate("document.activeElement?.blur?.()")
    page.keyboard.press("2")
    page.wait_for_timeout(1500)
    check("...the number keys still change tab", page.evaluate("location.hash") == "#practice")
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(1600)
    kept = page.evaluate("""async () => ({ body: document.body.classList.contains('is-navfold'),
      saved: (await (await fetch('/api/settings')).json()).ui.nav_collapsed })""")
    check("...and it stays folded after a reload (ui.nav_collapsed)", kept["body"] and kept["saved"] is True, str(kept))
    page.click("#nav-fold")
    page.wait_for_timeout(500)
    w = page.evaluate("document.getElementById('nav').getBoundingClientRect().width")
    saved = page.evaluate("async () => (await (await fetch('/api/settings')).json()).ui.nav_collapsed")
    check("...and unfolds to its full width, saved", w >= 170 and saved is False, f"{w}px, saved {saved}")

    # 2. The builder: an icon picker, and the footer on the card's bottom edge.
    for vw, vh in ((1280, 720), (1600, 1000)):
        page.set_viewport_size({"width": vw, "height": vh})
        go("page/new", 900)
        f = page.evaluate(FOOT)
        page.evaluate("document.querySelector('.pb__body').scrollTop = 99999")
        page.wait_for_timeout(200)
        g = page.evaluate(FOOT)
        check(f"builder @{vw}x{vh}: the footer is the card's bottom edge, inside the stage, the stage "
              "itself not scrolling, and it does not move when the middle scrolls",
              abs(f["cardBottom"] - f["footBottom"]) <= 2 and f["footBottom"] <= f["stageBottom"]
              and not f["stageScrolls"] and f["footTop"] == g["footTop"], f"{f} / {g}")
    page.set_viewport_size({"width": 1600, "height": 1000})
    go("page/new", 900)
    icons = page.evaluate("[...document.querySelectorAll('#pb-icon [data-icon]')].map((b) => b.dataset.icon)")
    real = page.evaluate("async () => (await import('/icons.js')).ICON_NAMES")
    check("the builder offers every preset icon", len(icons) >= 24 and icons == real, f"{len(icons)} of {len(real)}")
    page.click("#pb-start [data-start='blank']")
    page.click("label:has(> input[data-view='stats'][data-slug='all-time'])")
    page.click("label:has(> input[data-view='stats'][data-slug='keys-you-have-played'])")
    page.click("label:has(> input[data-view='practice'][data-slug='now'])")
    page.click("#pb-icon [data-icon='drum']")
    page.fill("#pb-name", "Nav check")
    page.click("#pb-save")
    page.wait_for_function("/^#page\\/p[a-z0-9]+$/.test(location.hash)", timeout=5000)
    page.wait_for_timeout(2600)
    pid = page.evaluate("location.hash.slice(6)")
    stored = page.evaluate("""async (id) => (await (await fetch('/api/settings')).json()).ui.pages
      .find((p) => p.id === id)""", pid)
    drawn = page.evaluate("(id) => document.querySelector(`#nav-pages [data-page='${id}'] svg`)?.innerHTML", pid)
    want = page.evaluate("async () => (await import('/icons.js')).icon('drum').innerHTML")
    check("...the picked icon is saved with the page and drawn on its nav item",
          (stored or {}).get("icon") == "drum" and drawn == want, str(stored)[:200])
    bad = page.evaluate("""async () => (await import('/pages.js')).validPages([{ id: 'x', icon: 'bogus',
      panes: [{ view: 'stats', panels: ['all-time'] }] }])[0].icon""")
    check("...and an icon this build does not draw loads as the default", bad == "note", repr(bad))

    # 3. Stats on a page: exactly what was picked, the pane's width, and still so after it
    #    refreshes itself (which replaces every panel it has).
    shown = {p["view"]: sorted(p["shown"]) for p in page.evaluate(SHOWN)}
    check("Stats is on the page with only the panels picked",
          shown.get("stats") == ["all-time", "keys-you-have-played"], str(shown))
    page.wait_for_timeout(5200)          # stats.js REFRESH_MS
    page.evaluate("""async () => { const v = (await import('/views/stats.js')).default;
      v.status({ practice: { today_notes: 1 } }); v.status({ practice: { today_notes: 2 } }); }""")
    page.wait_for_timeout(2000)
    after = page.evaluate("""() => { const pane = document.querySelector('.page__pane[data-view="stats"]');
      const grid = pane.querySelector(':scope > .grid'), gw = grid.getBoundingClientRect().width;
      const vis = [...grid.children].filter((e) => e.getBoundingClientRect().height > 0);
      return { shown: vis.map((e) => e.dataset.panel).sort(), narrow: vis.filter((e) =>
        e.getBoundingClientRect().width < gw - 2).map((e) => e.dataset.panel) }; }""")
    check("...and after Stats refreshes itself, still only those, each the pane's full width",
          after["shown"] == ["all-time", "keys-you-have-played"] and not after["narrow"], str(after))

    # 4. Panes as one workspace: panel to panel across a pane edge is the grid's own 14px,
    #    and no pane draws a scrollbar wider than a hairline.
    two = [p for p in saved_pages if p.get("cols") == 2 and len(p.get("panes", [])) >= 2]
    target = two[0]["id"] if two else pid
    for vw, vh in ((1600, 1000), (1280, 800)):
        page.set_viewport_size({"width": vw, "height": vh})
        go(f"page/{target}", 2600)
        g = page.evaluate(PANE_GAPS)
        check(f"panes @{vw}x{vh}: 14px from one pane's panels to the next's, scrollbars a hairline at most",
              g["gaps"] and all(12 <= x <= 16 for x in g["gaps"]) and all(b <= 4 for b in g["bars"]), str(g))
    page.set_viewport_size({"width": 1600, "height": 1000})

    # 5. Right-click a page in the nav: Edit, Rename, Delete -- inline.
    def menu() -> None:
        page.click(f"#nav-pages [data-page='{pid}']", button="right")
        page.wait_for_timeout(250)

    menu()
    acts = page.evaluate("[...document.querySelectorAll('#nav-menu [data-act]')].map((b) => b.dataset.act)")
    check("right-click on a page: a menu of Edit, Rename and Delete", acts == ["edit", "rename", "delete"], str(acts))
    page.keyboard.press("Escape")
    page.wait_for_timeout(250)
    check("...Esc closes it", not page.evaluate("!!document.getElementById('nav-menu')"))
    menu()
    page.click("#nav-menu [data-act='edit']")
    page.wait_for_timeout(800)
    check("...Edit opens the builder on that page", page.evaluate("location.hash") == f"#page/{pid}/edit"
          and page.evaluate("!!document.getElementById('pb-save')"))
    page.click("#pb-cancel")
    page.wait_for_timeout(1500)
    menu()
    page.click("#nav-menu [data-act='rename']")
    page.wait_for_timeout(200)
    page.fill("#nav-rename", "Renamed 7")
    page.press("#nav-rename", "Enter")
    page.wait_for_timeout(900)
    names = page.evaluate("""async (id) => ({ nav: document.querySelector(`#nav-pages [data-page="${id}"]`).textContent,
      head: document.querySelector('.page__name')?.textContent, hash: location.hash,
      saved: (await (await fetch('/api/settings')).json()).ui.pages.find((p) => p.id === id).name })""", pid)
    check("...Rename edits the name in place (typing 7 does not jump to page 1), saved and shown",
          names["nav"].endswith("Renamed 7") and names["saved"] == "Renamed 7" and names["head"] == "Renamed 7"
          and names["hash"] == f"#page/{pid}", str(names))
    menu()
    page.click("#nav-menu [data-act='delete']")
    page.wait_for_timeout(200)
    asking = page.evaluate("!!document.getElementById('nav-menu-yes')")
    still = page.evaluate("(id) => !!document.querySelector(`#nav-pages [data-page='${id}']`)", pid)
    page.click("#nav-menu-yes")
    page.wait_for_timeout(1200)
    gone = page.evaluate("""async (id) => ({ nav: !!document.querySelector(`#nav-pages [data-page="${id}"]`),
      saved: (await (await fetch('/api/settings')).json()).ui.pages.some((p) => p.id === id), hash: location.hash })""", pid)
    check("...Delete asks in the menu first, then the page is gone, saved gone, and you are back on Play",
          asking and still and not gone["nav"] and not gone["saved"] and gone["hash"] == "#play", str(gone))
    page.close()
    return fails


# ── resize: every panel at every width its arrows reach ──────────────────────────────
# The 0.14 audit walked 42 panels through the old 3/6/12 spans at two windows and found
# panels sticking out of their own box, rows that stopped short of the edge, and vertical
# gaps from 16 to 88px. This is that audit as a guard, through the real arrows, for every
# managed panel of every tab and of every starter page, at 1600x1000 and 1280x800. As
# shipped, the whole grid must
#   - leave no row short: every band of panels that overlap vertically reaches the
#     grid's right edge;
#   - keep its gaps: the space from each panel to the next panel under it is the grid's
#     own column gap, read off the DOM, to within the 2px row quantum.
# Every arrow click must change how many columns the panel shows, in the arrow's own
# direction, down to the narrowest it allows and up to the full width. A width you pick
# may open a gap beside the panel (room for the next one), so at every width reached the
# grid must
#   - stick nothing out: no element wider than its box (scrollWidth > clientWidth + 1)
#     and no box past its panel's sides, except inside a declared sideways scroller;
#   - stretch no card: none more than RS_STRETCH_PX taller than what it holds, which is
#     how a hole was hidden from the gap rule (a card drawn down 594px to the panel
#     under it measures as an even 14px gap);
#   - end every slider at its own value readout (a slider capped inside its field
#     stopped 169px short of the number it sets);
#   - leave no hole too narrow to use: empty space down a column, more than RS_HOLE_PX
#     between two cards, must be at least RS_HOLE_COLS columns wide at that height --
#     the narrowest a panel goes -- or no panel can ever go into it.
RESIZE_SIZES = ((1600, 1000), (1280, 800))
RS_STRETCH_PX = 100    # the 64-82px draw-downs that close a hole above a wide panel, and no more
RS_HOLE_PX = 24        # card to card down a column; the even gap is 14
RS_HOLE_COLS = 4       # a third of the grid, the narrowest step the design offers

# Each visible managed panel under `sel`, by id, so a click that rebuilds the view (Stats
# does) can find it again.
RS_TAG = """(sel) => [...document.querySelectorAll(sel + ' > [data-panel]')]
  .filter((e) => e.getClientRects().length).map((e) => e.dataset.panel)"""

RS_STATE = """([sel, id]) => {
  const el = document.querySelector(`${sel} > [data-panel="${id}"]`);
  if (!el) return null;
  const [less, more] = el.querySelectorAll(':scope > .mod > .mod__head .mod__grip > button');
  const g = el.parentElement.getBoundingClientRect();
  const cg = parseFloat(getComputedStyle(el.parentElement).columnGap);
  const colW = (g.width - 11 * cg) / 12;
  return { saved: +(([...el.classList].find((c) => /^col-\\d+$/.test(c)) || 'col-0').slice(4)),
           cols: Math.round((el.getBoundingClientRect().width + cg) / (colW + cg)),
           less: !!less && !less.disabled, more: !!more && !more.disabled };
}"""

# The pointer arriving on the panel, as it does before anyone can see its arrows.
RS_HOVER = """async ([sel, id]) => {
  document.querySelector(`${sel} > [data-panel="${id}"] > .mod`)?.dispatchEvent(new PointerEvent('pointerenter'));
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
}"""

RS_CLICK = """([sel, id, n]) => document.querySelector(
  `${sel} > [data-panel="${id}"] > .mod > .mod__head .mod__grip > button:nth-child(${n})`).click()"""

# Back as the view authored it, between panels: its class, not sized, and the layout the
# clicks saved forgotten, so a view that rebuilds itself (Stats) does not bring it back.
RS_RESTORE = """async ([sel, id, span]) => {
  (await import('/layout.js')).primeLayout({});
  const el = document.querySelector(`${sel} > [data-panel="${id}"]`);
  if (!el) return;
  for (const c of [...el.classList]) if (/^col-\\d+$/.test(c)) el.classList.remove(c);
  el.classList.add('col-' + span);
  delete el.dataset.sized;
  el.parentElement.repack?.();
}"""

RS_AUDIT = """([sel, strict, STRETCH, HOLE, HOLE_COLS]) => {
  const grid = document.querySelector(sel);
  const gr = grid.getBoundingClientRect();
  const cg = parseFloat(getComputedStyle(grid).columnGap);
  const name = (n) => n.tagName.toLowerCase() + (n.id ? '#' + n.id : '')
    + [...n.classList].slice(0, 2).map((c) => '.' + c).join('');
  const scrolls = (n) => ['auto', 'scroll'].includes(getComputedStyle(n).overflowX);
  const px = (v) => parseFloat(v) || 0;
  const bad = [];
  const kids = [...grid.children].filter((e) => e.getClientRects().length);
  const boxes = kids.map((e) => ({ id: e.dataset.panel || e.dataset.slot || e.className,
    r: (e.dataset.panel ? e.firstElementChild : e).getBoundingClientRect() }));
  for (const e of kids.filter((k) => k.dataset.panel)) {
    const mod = e.firstElementChild, mr = mod.getBoundingClientRect();
    for (const n of [e, mod, ...mod.querySelectorAll('*')]) {
      if (n.closest('[popover]')) continue;
      const r = n.getBoundingClientRect();
      if (!r.width || !r.height || getComputedStyle(n).visibility === 'hidden') continue;
      if (n instanceof SVGElement || n.tagName === 'CANVAS') continue;
      let inner = false;
      for (let p = n.parentElement; p && p !== e; p = p.parentElement) if (scrolls(p)) { inner = true; break; }
      if (inner) continue;
      if (!scrolls(n) && n.clientWidth && n.scrollWidth > n.clientWidth + 1) {
        bad.push(`${e.dataset.panel}: ${name(n)} holds ${n.scrollWidth}px in ${n.clientWidth}px`);
      } else if (r.right > mr.right + 1.5 || r.left < mr.left - 1.5) {
        bad.push(`${e.dataset.panel}: ${name(n)} ${Math.round(Math.max(r.right - mr.right, mr.left - r.left))}px past its panel`);
      }
    }
    // How far the card runs on past the last thing in it.
    const cs = getComputedStyle(mod);
    let end = mr.top + px(cs.borderTopWidth) + px(cs.paddingTop);
    for (const k of mod.children) {
      const ks = getComputedStyle(k);
      if (!k.getClientRects().length || k.matches('[popover]') || ['absolute', 'fixed'].includes(ks.position)) continue;
      end = Math.max(end, k.getBoundingClientRect().bottom + Math.max(0, px(ks.marginBottom)));
    }
    const spare = mr.bottom - px(cs.borderBottomWidth) - px(cs.paddingBottom) - end;
    if (spare > STRETCH) bad.push(`${e.dataset.panel}: card ${Math.round(spare)}px taller than what it holds`);
    // A slider ends where its own value readout does, not short of it.
    for (const f of mod.querySelectorAll('.field')) {
      const range = f.querySelector(':scope > input[type=range]'), val = f.querySelector('.field__value');
      if (!range || !val || !range.getClientRects().length || !val.getClientRects().length) continue;
      const short = val.getBoundingClientRect().right - range.getBoundingClientRect().right;
      if (short > 4) bad.push(`${e.dataset.panel}: a slider ends ${Math.round(short)}px short of its value`);
    }
  }
  // Holes: down each column's centre, card to card, then how many columns are empty
  // beside it at that height (and have a card further down, or it is just the bottom).
  const colW = (gr.width - 11 * cg) / 12;
  const cx = (c) => gr.left + c * (colW + cg) + colW / 2;
  const on = (r, c) => r.left <= cx(c) && cx(c) <= r.right;
  const rects = boxes.map((b) => b.r);
  const empty = (c, y) => !rects.some((r) => on(r, c) && r.top - cg <= y && y <= r.bottom + cg)
    && rects.some((r) => on(r, c) && r.top > y);
  for (let c = 0; c < 12; c++) {
    let y = gr.top - cg;
    for (const b of boxes.filter((b) => on(b.r, c)).sort((a, z) => a.r.top - z.r.top)) {
      if (b.r.top - y > HOLE) {
        const mid = (y + b.r.top) / 2;
        let lo = c, hi = c;
        while (lo > 0 && empty(lo - 1, mid)) lo--;
        while (hi < 11 && empty(hi + 1, mid)) hi++;
        if (hi - lo + 1 < HOLE_COLS) {
          bad.push(`a ${hi - lo + 1}-column hole (columns ${lo + 1}-${hi + 1}) ${Math.round(b.r.top - y - cg)}px tall above ${b.id}`);
        }
      }
      y = Math.max(y, b.r.bottom);
    }
  }
  if (!strict) return [...new Set(bad)];
  // Bands: boxes that overlap vertically, joined transitively.
  const band = boxes.map((_, i) => i);
  const root = (i) => (band[i] === i ? i : (band[i] = root(band[i])));
  for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
    const a = boxes[i].r, b = boxes[j].r;
    if (a.top < b.bottom - 1 && b.top < a.bottom - 1) band[root(j)] = root(i);
  }
  const reach = new Map();
  boxes.forEach((b, i) => reach.set(root(i), Math.max(reach.get(root(i)) || 0, b.r.right)));
  for (const [i, right] of reach) {
    if (right < gr.right - 2) bad.push(`the band at ${boxes[i].id} stops ${Math.round(gr.right - right)}px short of the edge`);
  }
  for (const a of boxes) {
    const under = boxes.filter((b) => b !== a && b.r.top >= a.r.bottom - 1
      && Math.min(a.r.right, b.r.right) - Math.max(a.r.left, b.r.left) > 2).sort((x, y) => x.r.top - y.r.top)[0];
    if (!under) continue;
    const gap = under.r.top - a.r.bottom;
    if (gap < cg - 0.5 || gap > cg + 2.5) bad.push(`${Math.round(gap)}px from ${a.id} down to ${under.id}`);
  }
  return [...new Set(bad)];
}"""


def resize_sweep(page, sel: str, where: str) -> tuple[int, int, list[str]]:
    """Every panel under `sel` through every span its arrows reach, the grid audited at each.
    Returns the states audited, the arrow clicks that changed a panel, and what was wrong."""
    states, clicks, bad = 0, 0, []

    def settle() -> None:
        page.evaluate("() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")
        page.wait_for_timeout(160)

    def audit(label: str, strict: bool = False) -> None:
        nonlocal states
        states += 1
        args = [sel, strict, RS_STRETCH_PX, RS_HOLE_PX, RS_HOLE_COLS]
        bad.extend(f"{where} {label}: {b}" for b in page.evaluate(RS_AUDIT, args))

    def state(pid: str) -> dict:
        return page.evaluate(RS_STATE, [sel, pid])

    audit("as shipped", strict=True)
    for pid in page.evaluate(RS_TAG, sel):
        start = state(pid)
        if not start:
            continue
        # Down to the narrowest it allows, then up to the whole width. Each enabled arrow
        # must change how many columns the panel shows, and the right way.
        for key, n, sign, word in (("less", 1, -1, "narrower"), ("more", 2, 1, "wider")):
            if sign > 0:
                audit(f"{pid} at its narrowest, {state(pid)['cols']} columns")
            for _ in range(5):
                page.evaluate(RS_HOVER, [sel, pid])
                st = state(pid)
                if not st[key]:
                    break
                page.evaluate(RS_CLICK, [sel, pid, n])
                settle()
                after = state(pid)
                if (after["cols"] - st["cols"]) * sign <= 0:
                    bad.append(f"{where} {pid}: '{word}' at {st['cols']} columns left it at {after['cols']}")
                    break
                clicks += 1
                if sign > 0:
                    audit(f"{pid} at {after['saved']} (shows {after['cols']})")
        end = state(pid)
        if end["cols"] != 12:
            bad.append(f"{where} {pid}: the arrows stop at {end['cols']} columns, not the full width")
        page.evaluate(RS_RESTORE, [sel, pid, start["saved"]])
        settle()
        back = state(pid)
        if back["cols"] != start["cols"]:
            bad.append(f"{where} {pid}: put back at {start['saved']} it shows {back['cols']} columns, "
                       f"not the {start['cols']} it showed as shipped")
    return states, clicks, bad


# A layout saved by an older Keys, set before the app loads: Read's authored 5 and 7 as
# a layout saves them (every panel, every span), and Stats' panels reversed with the spans
# a pre-0.14 grid had (6, 3, 12). Read keeps its 5 + 7; Stats keeps the saved order and
# takes the widths its view authors today.
RS_ROWS = """(sel) => [...document.querySelectorAll(sel + ' > [data-panel]')]
  .map((e) => [e.dataset.panel, [...e.classList].find((c) => /^col-\\d+$/.test(c))])"""


def migrate_checks(page, check) -> None:
    def go(hash_: str) -> None:
        page.evaluate(f"location.hash = '{hash_}'")
        page.wait_for_timeout(2200)

    go("read")
    read = page.evaluate(RS_ROWS, "#stage > .grid")
    go("stats")
    stats = page.evaluate(RS_ROWS, "#stage > .grid")
    spans = lambda cls: int(cls[4:])  # noqa: E731
    old_spans = (6, 3, 12)
    seeded = {
        "read": [{"id": i, "span": spans(c)} for i, c in read],
        "stats": [{"id": i, "span": old_spans[k % 3]} for k, (i, _) in enumerate(reversed(stats))],
    }
    page.evaluate("""(layout) => fetch('/api/settings', { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ui: { layout } }) })""", seeded)
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(1600)
    go("read")
    got = page.evaluate(RS_ROWS, "#stage > .grid")
    check("a saved Read layout keeps the widths its view authors (5 + 7), not the nearest step",
          len(read) >= 3 and got == read, f"authored {read}, after a save {got}")
    go("stats")
    got = page.evaluate(RS_ROWS, "#stage > .grid")
    want = [[i, dict(stats)[i]] for i, _ in reversed(stats)]
    check("a pre-0.14 Stats layout (with a 3) keeps its order and takes today's authored widths",
          len(stats) >= 3 and got == want, f"want {want}, got {got}")
    page.evaluate("""() => fetch('/api/settings', { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ui: { layout: { read: [], stats: [] } } }) })""")
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(1600)


# How narrow each panel really goes, measured in its narrow layout: the panel forced to
# 300px (inside every narrow query), the arrows shown as a hover shows them.
RS_MIN = """() => [...document.querySelectorAll('#stage > .grid > [data-panel]')].map((el) => {
  const mod = el.firstElementChild, grip = mod.querySelector('.mod__grip');
  const keep = [el.style.width, mod.style.width, grip.style.cssText];
  el.style.width = '300px'; mod.style.width = 'min-content';
  grip.style.cssText = 'max-width: none; margin-left: 0';
  const w = Math.ceil(mod.getBoundingClientRect().width);
  [el.style.width, mod.style.width, grip.style.cssText] = keep;
  return [el.dataset.panel, w];
})"""


def resize_checks(browser, port: int, errs: list[str]) -> list[str]:
    """Spans, rows and gaps on every tab and every starter page; the builder's warning."""
    fails: list[str] = []
    page = browser.new_page(viewport={"width": 1600, "height": 1000})
    page.on("pageerror", lambda e: errs.append(str(e)))

    def check(label: str, passed: bool, detail: str = "") -> None:
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))
        if not passed:
            fails.append(f"resize: {label} {detail}".strip())

    def go(hash_: str, wait: int = 2200) -> None:
        page.evaluate(f"location.hash = '{hash_}'")
        page.wait_for_timeout(wait)

    print("\nresize: every panel at every span its arrows reach")
    page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
    page.wait_for_timeout(1600)

    # The starter pages as this sandbox's pages (the sections before are done with it),
    # and the tour put away again: the Settings reset above brought it back.
    starters = page.evaluate("async () => (await import('/pages.js')).STARTERS")
    made = [{"id": f"rs-{k}", "name": f"Resize {k}", "cols": s["cols"], "panes": s["panes"]}
            for k, s in starters.items() if s["panes"]]
    page.evaluate("""(list) => fetch('/api/settings', { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ui: { pages: list, tour_seen: true } }) })""", made)
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(1600)
    migrate_checks(page, check)

    for w, h in RESIZE_SIZES:
        page.set_viewport_size({"width": w, "height": h})
        for view in VIEWS:
            go(view)
            # A whole tab always has a panel narrower than the window; arrows that are all
            # disabled would pass everything else.
            n, clicks, bad = resize_sweep(page, "#stage > .grid", f"{view} @{w}")
            check(f"{view} @{w}x{h}: {n} states, {clicks} clicks each shown, nothing sticks out, no "
                  "stretched card, no unusable hole; as shipped no short row and even gaps",
                  clicks > 0 and not bad, "; ".join(bad[:8]))
        for p in made:
            go(f"page/{p['id']}", 2600)
            for pane in p["panes"]:
                sel = f".page__pane[data-view='{pane['view']}'] > .grid"
                n, clicks, bad = resize_sweep(page, sel, f"{p['name']}/{pane['view']} @{w}")
                check(f"{p['name']} @{w}x{h}, {pane['view']} pane: {n} states, {clicks} clicks each shown, "
                      "nothing sticks out, no stretched card, no unusable hole", n > 0 and not bad,
                      "; ".join(bad[:8]))
    page.set_viewport_size({"width": 1600, "height": 1000})

    # The builder's minimums against the panels themselves: never under what the panel
    # measures (or the warning would miss it), and not so far over that it cries wolf.
    panels = page.evaluate("async () => (await import('/pages.js')).PANELS")
    registry = {p["slug"]: p.get("min") for p in panels}
    titles = {p["slug"]: p["title"] for p in panels}
    off = []
    for view in ("play", "practice", "layers", "tools", "stats"):
        go(view)
        for slug, w in page.evaluate(RS_MIN):
            want = registry.get(slug)
            if want is None or not w <= want <= w + 30:
                off.append(f"{slug}: registry {want}, measured {w}")
    check("the builder's panel minimums match what each panel measures in its narrow layout",
          not off, "; ".join(off))

    # The builder warns about exactly the panels a pane of that window cannot hold: Build
    # one, Pedal and Now at three columns in a 1024px window -- then the page is made and
    # each pane's real width read off it.
    page.set_viewport_size({"width": 1024, "height": 768})
    go("page/new", 900)
    page.click("#pb-start [data-start='blank']")
    picks = {"layers": "build-one", "tools": "pedal", "practice": "now"}
    for v, slug in picks.items():
        page.click(f"label:has(> input[data-view='{v}'][data-slug='{slug}'])")
    page.click("#pb-cols button:text-is('3')")
    warn = page.evaluate("() => { const w = document.getElementById('pb-warn'); return w.hidden ? '' : w.textContent; }")
    page.fill("#pb-name", "Resize warn")
    page.click("#pb-save")
    page.wait_for_timeout(2800)
    real = page.evaluate("""() => Object.fromEntries([...document.querySelectorAll('.page__pane')]
      .map((p) => [p.dataset.view, p.querySelector(':scope > .grid').clientWidth]))""")
    wrong = [f"{titles[s]}: min {registry[s]}, pane {real.get(v)}px, {'warned' if titles[s] in warn else 'not warned'}"
             for v, s in picks.items() if (registry[s] > real.get(v, 0)) != (titles[s] in warn)]
    check("at 1024px, three columns: the builder warns for exactly the panels too wide for their pane",
          len(real) == 3 and not wrong, "; ".join(wrong) or f"panes {real}, warning {warn!r}")
    page.set_viewport_size({"width": 1600, "height": 1000})
    page.close()
    return fails


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
        port = free_port()
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
            fails.extend(jam_checks(browser, port, errs))
            fails.extend(transport_checks(browser, port, errs))
            fails.extend(pages_checks(browser, port, errs))
            fails.extend(pages_fix_checks(browser, port, errs))
            fails.extend(nav_checks(browser, port, errs))
            fails.extend(resize_checks(browser, port, errs))
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
