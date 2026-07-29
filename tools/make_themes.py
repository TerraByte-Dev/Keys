"""Derive the dark themes from four colours each, and write them into style.css.

    .venv\\Scripts\\python tools\\make_themes.py

A theme in Keys is ~40 CSS variables. Four of them are decisions -- background,
the SOUNDING accent, a secondary accent, and ink -- and the rest is arithmetic:
panels are tints of the background, hairlines are tints of the panels, the heat
ramp is the accent at three strengths, ink-dim and ink-faint are ink mixed back
toward the background. Writing that out by hand is forty chances per theme to get
one of them backwards, and `--wash` backwards is invisible until someone opens
the theme and finds every row the wrong side of its panel.

So the generated themes are data, and this file is the only place their arithmetic
lives. It rewrites the block between the two markers in style.css and touches
nothing else.

**The first four are NOT generated.** midnight, blueprint, phosphor and paper were
tuned by eye against real screenshots -- paper especially, where a light theme has
to invert the surface tokens rather than scale them, and the amber has to darken
to stay legible on a white card. Rules that produce those four from a spec would
be rules bent until they fit, which is not a rule. They stay hand-written above
the marker; everything below it comes from the table here.
"""

from __future__ import annotations

import colorsys
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHEET = ROOT / "frontend" / "style.css"

START = "/* -- generated themes: tools/make_themes.py owns everything below ------- */"
END = "/* -- end generated themes ----------------------------------------------- */"

# id, name, blurb, background, accent (SOUNDING), accent2, ink.
THEMES = [
    ("ultraviolet", "Ultraviolet", "Electric violet over midnight indigo.",
     "#050316", "#a06bff", "#5ad2ff", "#d3c4ff"),
    ("synthwave", "Synthwave", "Magenta neon and cyan on deep violet.",
     "#0a0320", "#ff3ac8", "#4fe3ff", "#f0c8ff"),
    ("crimson", "Crimson", "Red neon on charred maroon.",
     "#0b0305", "#ff2e4d", "#ffb03a", "#ffb6c0"),
    ("tangerine", "Tangerine", "Hot orange on scorched black.",
     "#0a0500", "#ff7a18", "#ffd166", "#ffcfa4"),
    ("ice", "Ice", "Cyan-white over deep navy.",
     "#02060f", "#6fe6ff", "#9db4ff", "#cfeeff"),
    ("gold", "Gold", "Champagne gold. Quiet and expensive.",
     "#080703", "#e8c46a", "#9fd8c4", "#ece2c0"),
    ("slate", "Slate", "Steel blue. The flattest thing here.",
     "#0b0e12", "#8fb4e0", "#7fd6c0", "#ccd7e4"),
]


def hex_to_rgb(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def rgb_to_hex(c: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{max(0, min(255, round(x * 255))):02x}" for x in c)


def mix(a: str, b: str, t: float) -> str:
    """`t` of the way from a to b."""
    ca, cb = hex_to_rgb(a), hex_to_rgb(b)
    return rgb_to_hex(tuple(x + (y - x) * t for x, y in zip(ca, cb)))


def shift(colour: str, *, light: float = 0.0, sat: float = 1.0) -> str:
    r, g, b = hex_to_rgb(colour)
    hu, li, sa = colorsys.rgb_to_hls(r, g, b)
    li = max(0.0, min(1.0, li + light))
    sa = max(0.0, min(1.0, sa * sat))
    return rgb_to_hex(colorsys.hls_to_rgb(hu, li, sa))


def rotate(colour: str, degrees: float) -> str:
    r, g, b = hex_to_rgb(colour)
    hu, li, sa = colorsys.rgb_to_hls(r, g, b)
    return rgb_to_hex(colorsys.hls_to_rgb((hu + degrees / 360.0) % 1.0, li, sa))


def alpha(colour: str, a: float) -> str:
    return colour + f"{max(0, min(255, round(a * 255))):02x}"


def build(theme_id: str, name: str, blurb: str,
          bg: str, accent: str, accent2: str, ink: str) -> str:
    # Panels climb away from the background, tinted very slightly toward the accent
    # so a theme reads as one material rather than grey with a coloured lamp on it.
    tint = mix(bg, accent, 0.06)
    panels = [bg,
              mix(tint, ink, 0.045),
              mix(tint, ink, 0.075),
              mix(tint, ink, 0.115),
              mix(tint, ink, 0.165)]
    hairline = mix(tint, ink, 0.20)
    hairline_hi = mix(tint, ink, 0.30)

    # Ivory keys, not tinted ones. The ink carries the theme's hue at full
    # saturation, and a keybed the colour of the text reads as a decoration rather
    # than as an instrument -- lavender keys under Synthwave was the giveaway. A
    # trace of the hue stays so the keyboard still belongs to the theme.

    # Text on an accent fill. Near-black, carrying the accent's hue so it does not
    # read as a hole punched in the colour.
    on_accent = shift(mix(bg, accent, 0.14), light=-0.02)

    return f"""
/* {name} — {blurb} */
[data-theme="{theme_id}"] {{
  --panel-0: {panels[0]};
  --panel-1: {panels[1]};
  --panel-2: {panels[2]};
  --panel-3: {panels[3]};
  --panel-4: {panels[4]};
  --hairline: {hairline};
  --hairline-hi: {hairline_hi};

  --ink: {ink};
  --ink-dim: {mix(ink, bg, 0.42)};
  --ink-faint: {mix(ink, bg, 0.66)};

  --amber: {accent};
  --amber-hot: {shift(accent, light=0.18, sat=0.85)};
  --amber-deep: {shift(accent, light=-0.20)};
  --cyan: {accent2};
  --green: {shift(rotate(accent2, 40), light=0.04)};
  --red: {shift(rotate(accent, -8) if theme_id != "crimson" else "#ff5f4d", light=0.02)};

  --wash: {alpha(ink, 0.03)};
  --wash-hi: {alpha(ink, 0.07)};
  --gloss: {alpha(ink, 0.09)};
  --shadow: #000000aa;
  --shadow-soft: #00000055;
  --shadow-deep: #000000cc;
  --stage-glow: {alpha(accent, 0.05)};

  --on-accent: {on_accent};
  --amber-glow: {alpha(accent, 0.33)};
  --amber-wash: {alpha(accent, 0.10)};
  --amber-faint: {alpha(accent, 0.05)};
  --amber-fill-hi: {mix(bg, accent, 0.26)};
  --amber-fill-lo: {mix(bg, accent, 0.12)};

  --cyan-deep: {shift(accent2, light=-0.26, sat=0.7)};
  --green-deep: {shift(rotate(accent2, 40), light=-0.26, sat=0.7)};
  --red-deep: {shift(rotate(accent, -8), light=-0.26, sat=0.7)};
  --red-ink: {shift(rotate(accent, -8), light=0.28, sat=0.8)};
  --red-fill: {mix(bg, rotate(accent, -8), 0.16)};
  --red-fill-hi: {mix(bg, rotate(accent, -8), 0.28)};

  --control: {mix(panels[4], ink, 0.34)};
  --control-lo: {panels[3]};
  --control-edge: {mix(panels[4], ink, 0.50)};
  --led-off: {hairline};

  --heat-1: {mix(bg, accent, 0.28)};
  --heat-2: {mix(bg, accent, 0.58)};
  --heat-3: {mix(bg, accent, 0.86)};

  --key-white: {mix(shift(ink, sat=0.30, light=0.02), bg, 0.13)};
  --key-black: {mix(bg, ink, 0.07)};
  --key-sustain: {shift(accent, light=-0.24, sat=0.75)};
  --key-ghost: {mix(bg, accent2, 0.34)};
  --key-dead: {mix(bg, ink, 0.42)};
  --key-label: {mix(bg, ink, 0.40)};
  --key-label-black: {mix(bg, ink, 0.55)};

  --zone-1: {accent};
  --zone-2: {accent2};
  --zone-3: {shift(rotate(accent2, 40), light=0.04)};
  --zone-4: {rotate(accent, 55)};
  --zone-5: {rotate(accent, -45)};
  --zone-6: {rotate(accent2, -40)};
}}"""


def main() -> int:
    sheet = SHEET.read_text(encoding="utf-8")
    if START not in sheet or END not in sheet:
        print(f"  markers missing in {SHEET.name}; add them once and re-run")
        return 1

    body = "\n".join(build(*t) for t in THEMES)
    head, rest = sheet.split(START, 1)
    _, tail = rest.split(END, 1)
    SHEET.write_text(head + START + "\n" + body + "\n" + END + tail, encoding="utf-8")

    print(f"  {len(THEMES)} themes written into {SHEET.relative_to(ROOT)}")
    for tid, name, blurb, *_ in THEMES:
        print(f"    {tid:<12} {name:<12} {blurb}")

    # The picker and the tutorial both read a list; drifting from it is how a theme
    # ends up in the sheet and nowhere in the UI.
    app = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
    listed = re.search(r"export const THEMES = \[(.*?)\];", app, re.S)
    names = set(re.findall(r"'([a-z]+)'", listed.group(1))) if listed else set()
    missing = [t[0] for t in THEMES if t[0] not in names]
    if missing:
        print(f"\n  NOT IN app.js THEMES: {', '.join(missing)}")
        return 1
    print(f"\n  app.js lists all {len(names)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
