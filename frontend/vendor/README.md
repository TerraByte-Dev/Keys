# Vendored third-party code

Everything in this directory was written by someone else, is shipped unmodified, and
is here on purpose rather than through a package manager: the Keys frontend has no
build step, no `package.json` and no `node_modules`, so a dependency arrives as a file
you can read the licence of.

## Verovio 6.2.0 — music notation engraver

    verovio.mjs          14 KB   hand-written ESM wrapper, exports VerovioToolkit
    verovio-module.mjs  7.0 MB   Emscripten build, WASM embedded as base64

**Licence: LGPL-3.0-or-later.** Verified three ways on 2026-07-27: the upstream repo
ships both `COPYING` (GPL text) and `COPYING.LESSER` (LGPL text), which is how an LGPL
project distributes; `api.github.com/repos/rism-digital/verovio` returns
`spdx_id: LGPL-3.0`; and the npm package declares `"license": "LGPL-3.0-or-later"`.
Both licence texts are reproduced here as `COPYING.txt` and `COPYING.LESSER.txt`.

Keys itself is MIT. That is compatible: Verovio is used unmodified, as a separable
file loaded at runtime, and the LGPL obligation is to say so, ship the licence, and
leave the user able to replace it — all three of which this directory does. **Do not
edit these files.** Modifying them would make Keys a derivative work and pull the
whole application under the LGPL. To upgrade, replace them wholesale and update the
version above.

Chosen over OpenSheetMusicDisplay (BSD-3-Clause, 1.27 MB) for one reason:
`renderToTimemap()` returns each note's onset in both quarter-notes and milliseconds
with its element id. OSMD has no timemap, so onsets have to be derived by hand from
Fraction timestamps and BPM — about forty lines that must get repeats, tuplets and
mid-piece tempo changes right, which is exactly where the bugs live.

No network requests: Verovio embeds its SMuFL glyphs as SVG paths, so there is no
webfont to fetch. That was a hard requirement, not a preference.
