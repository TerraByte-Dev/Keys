# README brand assets

| File | Used by | How it's made |
|---|---|---|
| `keys-wordmark.png` | README hero (black-bg wordmark) | Generated. Prompt below. **Not yet created.** |
| `keys-wordmark-transparent.png` | Alternate hero — swap into the README `<img>` if preferred | Generated. Transparent variant. |
| `social-preview.png` | Repo **Settings → General → Social preview** (1280×640) | Generated. Not referenced by the README body. |
| `terrabyte-logo.png` | README "brought to you by" + footer | Existing TerraByte Solutions mark. |
| `screenshots/*.png` | README screenshots grid | Captured live — see [`screenshots/README.md`](screenshots/README.md). |

The hero must match the in-app aesthetic: a **1970s Japanese synthesizer front panel**, not a CRT terminal. That
is the deliberate difference from the other TerraByte products — Keys is hardware, not a screen. Brand tokens
come straight from `frontend/style.css`:

| Token | Hex | Role |
|---|---|---|
| amber | `#FFA62B` | the lamp — means *sounding* |
| amber-hot | `#FFD08A` | lit core |
| amber-deep | `#C26B00` | shadow / track fill |
| ink | `#E7E1D3` | silkscreen cream — panel lettering |
| panel | `#101114` → `#272B31` | anodized graphite |
| background | `#08090A` | |

The UI typeface is **Bahnschrift** (Windows' DIN). DIN is the lettering standard actually screen-printed on
instrument panels, and it ships with Windows 11 — which is why the app needs no webfont and works offline.

## Image-gen prompt — `keys-wordmark.png` (hero, wide, on black)

> Wide banner logotype reading "KEYS" in a single line. Condensed industrial DIN-style sans-serif — the
> engineering lettering screen-printed on 1970s synthesizer front panels — uppercase, geometric, slightly wide
> letter-spacing, medium weight. The letters are rendered in warm silkscreen cream (#E7E1D3) with a soft
> tungsten-amber (#FFA62B) glow bleeding from behind them, as if backlit by indicator lamps. Pure black
> (#08090A) background with a very subtle brushed-aluminium horizontal grain and a faint dark vignette. To the
> left of the word, a small row of illuminated amber pilot lamps. Precise, engineered, restrained — the look of
> expensive audio equipment photographed in a dark studio. Flat 2D, no 3D bevel, no perspective, no
> reflections, no extra text. Aspect ratio 3:1 (wide). High resolution.

**Variant — transparent background** (drop the background line, append): *"Transparent background (alpha), keep
only the cream letterforms and their amber glow."*

## Optional — GitHub social-preview card (`social-preview.png`, 1280×640)

> A 1280×640 social card on a near-black (#08090A) brushed-graphite panel. Centered, the condensed industrial
> DIN-style logotype "KEYS" in silkscreen cream (#E7E1D3) with a warm amber (#FFA62B) backlight glow. Below it,
> in a smaller monospaced technical font in dim cream, the tagline: "A MIDI PIANO THAT ANSWERS IN THREE
> MILLISECONDS". Along the bottom edge, a thin abstract row of piano keys with two of them lit amber. Subtle
> brushed-metal grain, faint vignette, precise hairline rules. Lots of negative space, balanced composition,
> flat 2D studio-hardware aesthetic, no other text or logos.

(Set this under repo **Settings → Social preview** — it doesn't need to live in the README.)
