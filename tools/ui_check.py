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
    pick(page, "Play", "Modal", 300)
    shown = page.evaluate("""() => [...document.querySelectorAll('.jam__opt')]
      .filter((o) => o.style.display !== 'none').map((o) => o.querySelector('.jam__lbl').textContent)""")
    check("Modal: a room instead of a key", "Room" in shown and "Key" not in shown, str(shown))
    ringed = until("document.querySelector('#dock .key.is-sig') && document.querySelector('.strip__key.is-sig')")
    role = page.evaluate("() => document.querySelector('.strip__key.is-sig .strip__role')?.textContent")
    check("...the note that makes the mode is ringed, on the keys and the strip", ringed and role, f"labelled {role!r}")

    pick(page, "Play", "Blues", 300)
    twelve = until("document.querySelectorAll('.jam__chip').length === 12")
    chips = page.evaluate("document.querySelectorAll('.jam__chip').length")
    style = page.evaluate(BAND).get("style")
    check("Blues: twelve bars, on the Shuffle", twelve and style == "shuffle", f"{chips} chips, {style}")
    check("...with the blue note ringed", until("[...document.querySelectorAll('.strip__key.is-sig .strip__role')]"
                                                ".some((e) => e.textContent === 'blue')"))

    pick(page, "Play", "Answer", 300)
    shown = page.evaluate("""() => [...document.querySelectorAll('.jam__opt')]
      .filter((o) => o.style.display !== 'none').map((o) => o.querySelector('.jam__lbl').textContent)""")
    check("Answer: no seat to take", "Seat" not in shown and page.evaluate(BAND).get("bass") is True, str(shown))
    check("...the band's question lights the keys as it plays", until("document.querySelector('#dock .key.is-lead')", 15000))
    turn = until("document.querySelector('.jam__coach').textContent.startsWith('Your turn')", 15000)
    home = page.evaluate(COACHED)
    check("...then it is your turn, told where home is", turn and len(home) == 3, page.evaluate(text, ".jam__coach"))
    page.evaluate(PLAY, [home[0] - 5 if home else 67, home[0] if home else 72])
    judged = until("document.querySelector('.jam__heard').textContent.includes('on the root')", 8000)
    check("...and an answer ending on the root is heard as home", judged, page.evaluate(text, ".jam__heard"))

    pick(page, "Play", "Echo", 300)
    turn = until("document.querySelector('.jam__coach').textContent.startsWith('Your turn')", 15000)
    lick = page.evaluate(COACHED)
    check("Echo: after the lick, your turn, with its notes", turn and 2 <= len(lick) <= 6, str(lick))
    page.evaluate(PLAY, lick)
    judged = until("document.querySelector('.jam__heard').textContent.includes('note for note')", 8000)
    check("...and playing it back note for note counts", judged and "1 of 1" in page.evaluate(text, ".jam__stats"),
          f"{page.evaluate(text, '.jam__heard')} / {page.evaluate(text, '.jam__stats')}")

    pick(page, "Play", "Songs", 1500)
    check("back to Songs, the band still playing", page.evaluate(BAND).get("running") is True)
    return []


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
    page.goto(f"http://127.0.0.1:{port}/#practice", wait_until="networkidle")
    page.wait_for_timeout(2200)
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
    page.close()
    return fails


# -- the transport: bar steps, Go to bar, loop by bars, the scrub, the sheet ---------
# A song written here rather than shipped: one note per beat, numbered 1..TR_BARS, long
# enough that the engraving runs to more than one page. Every expected value below is
# arithmetic on these constants -- bar n starts at quarter (n-1)*4 of TR_BARS*4 -- and
# never something read back from ghost.js.
TR_BARS = 160
TR_STEPS = "CDEFGAB"


def transport_fixture() -> str:
    bars = []
    for i in range(TR_BARS):
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
TR_OPEN = """async (xml) => {
  const r = await fetch('/api/scores', { method: 'POST', headers: { 'x-filename': 'transport-check.musicxml' }, body: xml });
  const meta = (await r.json()).score;
  const payload = await (await fetch(`/api/scores/${meta.id}/notes`)).json();
  (await import('/app.js')).startGhost(payload, meta);
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
    bars = page.evaluate(TR_OPEN, transport_fixture())
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
    page.click("#roll-page-prev")
    page.wait_for_timeout(1600)                # past a heartbeat
    check("...a page turned back by hand stays turned while paused",
          read()["pageN"] == f"{pages - 1}/{pages}", read()["pageN"])

    # Playing over a page break turns the page. The last bar on this page comes from the
    # page itself; the bar after it is, by construction, on the next one.
    page.click("#ghost-loop-clear")
    last = page.evaluate("Math.max(...[...document.querySelectorAll('#roll-paper g.measure[data-bar]')].map((g) => +g.dataset.bar))")
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
    page.close()
    return fails


def free_port() -> int:
    """A port nothing is listening on. Several checkouts run this suite at once, and a
    fixed port silently tests whichever app answered first."""
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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
