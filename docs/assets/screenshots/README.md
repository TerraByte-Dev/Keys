# README screenshots — shot list & capture guide

The README's **Screenshots** grid pulls PNGs from this folder. Capture them from the running app so they show
real data — an empty practice history photographs badly, and the whole point of the Practice view is that it has
something in it.

## Capture settings (keep them consistent)

- **Viewport:** 1440 × 900 at 2× device scale factor. The app is a fixed console layout that does not scroll,
  so the viewport *is* the shot.
- **State:** connect a MIDI keyboard and play for a few minutes first. The calendar, key heatmap and velocity
  histogram all need real note events.
- **Audio mode:** exclusive, so the header reads `BUF 3ms` rather than `BUF shared`. That number is the product.
- **Format:** PNG, native resolution. Don't upscale — let the README size them.

Automated capture (needs `pip install playwright && playwright install chromium`, with the app already running):

```python
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
    pg.goto("http://127.0.0.1:8770", wait_until="networkidle")
    pg.wait_for_timeout(3000)
    for view in ("play", "practice", "metronome", "zones", "read", "settings"):
        pg.evaluate(f"location.hash='{view}'")
        pg.wait_for_timeout(1400)
        pg.screenshot(path=f"docs/assets/screenshots/{view}.png")
    b.close()
```

## In the README now

| File | View | Shows |
|---|---|---|
| `play.png` | Play | **Featured shot.** Presets, the instrument browser, and the 88-key dock that never leaves. |
| `practice.png` | Practice | The idle-gapped clock, 90-day calendar, key heatmap and timing analysis. |
| `read.png` | Read | The hand-rolled SVG grand staff mid-exercise, with the amber target note. |
| `zones.png` | Zones | The zone editor and its visual key-range bar. |
| `metronome.png` | Metro | Tempo, meter, beat lamps and the tempo-ramp controls. |

Also captured but not currently in the grid: `settings.png` (audio device switching, latency instrumentation).

For the hero shot, holding a chord makes the keyboard light up and fills the chord readout — much better than a
silent keybed.
