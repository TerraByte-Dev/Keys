"""Key the green out of the generated logo and cut every icon the app needs.

    .venv\\Scripts\\python.exe tools\\make_icons.py <source.png>   # key a fresh render
    .venv\\Scripts\\python.exe tools\\make_icons.py                # recut from the master

The artwork comes from an image model that cannot output transparency, so it is
rendered on flat chroma green and keyed here. That is a one-way step, so the KEYED
master is what lives in the repository -- `docs/assets/keys-icon.png` -- and every
other size is cut from it. Re-running with no argument regenerates the derivatives and
never touches the master.

Keying is on **greenness**, `G - max(R, B)`, not on distance from one green. The render
has several hundred slightly different greens in it (a lossy pass somewhere upstream),
so matching a single colour leaves a confetti of survivors along every edge. Greenness
separates cleanly: the key green scores about +113, the near-black tile -5, the cream
keys -7, and the amber -89.

Edge pixels get partial alpha from the same measure, then a despill pass clamps green
to the larger of red and blue. Without despill an anti-aliased edge keeps a green
fringe that is invisible on a green background and obvious on a white README.

Pillow is a dev dependency only. Nothing the app ships imports it.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from PIL import Image
except ImportError:
    print("  Pillow is needed for this tool only:  .venv\\Scripts\\pip install pillow")
    raise SystemExit(1)

MASTER = ROOT / "docs" / "assets" / "keys-icon.png"

# Below OPAQUE the pixel is art; above CLEAR it is background; between, it is an edge.
OPAQUE_BELOW = 12
CLEAR_ABOVE = 55

# What gets cut, and where it goes.
PNG_SIZES = {
    ROOT / "frontend" / "icon-512.png": 512,
    ROOT / "frontend" / "icon-180.png": 180,
    ROOT / "frontend" / "icon-32.png": 32,
    ROOT / "docs" / "assets" / "keys-icon-256.png": 256,
}
ICO_PATH = ROOT / "packaging" / "keys.ico"
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]


def key_out(src: Path) -> Image.Image:
    im = Image.open(src).convert("RGBA")
    px = im.load()
    w, h = im.size
    cleared = edged = 0
    for y in range(h):
        for x in range(w):
            r, g, b, _a = px[x, y]
            other = max(r, b)
            greenness = g - other
            if greenness >= CLEAR_ABOVE:
                px[x, y] = (0, 0, 0, 0)
                cleared += 1
                continue
            if greenness > OPAQUE_BELOW:
                # An edge. Alpha falls off across the transition, and the green is
                # pulled back to the neighbouring channels so no fringe survives.
                span = CLEAR_ABOVE - OPAQUE_BELOW
                alpha = int(round(255 * (1.0 - (greenness - OPAQUE_BELOW) / span)))
                px[x, y] = (r, other, b, max(0, min(255, alpha)))
                edged += 1
            elif greenness > 0:
                px[x, y] = (r, other, b, 255)      # despill only
    print(f"  keyed: {cleared:,} px cleared, {edged:,} edge px feathered")
    return im


def trim(im: Image.Image) -> Image.Image:
    """Crop to the artwork, then re-pad to a square with a small even margin.

    The render puts the tile at roughly 84% of the frame, but "roughly" is not a
    number an icon grid can use -- cropping to what is actually there and re-padding
    is what makes the 32px cut land on whole pixels.
    """
    box = im.getbbox()
    if box is None:
        return im
    art = im.crop(box)
    side = max(art.size)
    pad = int(side * 0.06)
    out = Image.new("RGBA", (side + pad * 2, side + pad * 2), (0, 0, 0, 0))
    out.paste(art, ((out.size[0] - art.size[0]) // 2, (out.size[1] - art.size[1]) // 2))
    return out


def main() -> int:
    if len(sys.argv) > 1:
        src = Path(sys.argv[1])
        if not src.exists():
            print(f"  no such file: {src}")
            return 1
        print(f"1. keying {src.name}")
        master = trim(key_out(src))
        master = master.resize((1024, 1024), Image.LANCZOS)
        MASTER.parent.mkdir(parents=True, exist_ok=True)
        master.save(MASTER)
        print(f"  master -> {MASTER.relative_to(ROOT)}  {master.size[0]}x{master.size[1]}")
    else:
        if not MASTER.exists():
            print(f"  no master at {MASTER.relative_to(ROOT)}; pass a source render")
            return 1
        master = Image.open(MASTER).convert("RGBA")
        print(f"1. recutting from {MASTER.relative_to(ROOT)}")

    print("2. PNG sizes")
    for path, size in PNG_SIZES.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        master.resize((size, size), Image.LANCZOS).save(path)
        print(f"  {size:>4}px -> {path.relative_to(ROOT)}")

    print("3. Windows icon")
    ICO_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Pillow builds every size into the one file; the 16px is what Explorer and the
    # taskbar actually reach for, and letting Windows downscale a 256 instead is how
    # an icon ends up a grey smudge.
    master.save(ICO_PATH, sizes=[(s, s) for s in ICO_SIZES])
    print(f"  {len(ICO_SIZES)} sizes {ICO_SIZES} -> {ICO_PATH.relative_to(ROOT)}"
          f"  ({ICO_PATH.stat().st_size:,} B)")

    print("\n  done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
