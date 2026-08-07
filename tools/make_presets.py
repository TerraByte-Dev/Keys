"""Generate the shipped preset pack from a table of recipes.

    .venv\\Scripts\\python.exe tools\\make_presets.py          # write presets/
    .venv\\Scripts\\python.exe tools\\make_presets.py --check   # verify without writing

A preset is a named set of zones, and the interesting ones are layers: two sounds on
the same keys with the second one quiet and soft-curved so it colours the first rather
than competing with it. Hand-writing sixty of those as JSON is unreadable and
unmaintainable; the recipes below are the actual content, and the JSON is output.

**Every instrument is looked up by name against the SoundFont.** A bank/program pair
typed by hand and wrong does not fail -- FluidSynth falls back to Grand Piano, and you
get a preset called "Hardstyle Lead" that sounds like a piano. Looking them up means a
typo is a build error instead of a mystery.

What this cannot do, stated plainly rather than approximated badly:

* **No wobble.** A dubstep bass is an LFO sweeping a filter cutoff. FluidSynth plays
  SoundFont samples with a fixed filter per preset; there is no per-note modulation to
  automate. The bass presets here are the harmonic content, not the movement.
* **No hardstyle kick.** That is a distorted, pitch-swept sample, not a playable
  instrument. Use a drum kit zone.
* **No real didgeridoo.** General MIDI has 128 programs and none of them is one. The
  entry here is a drone built from a low reed plus a vocal formant layer, transposed
  down an octave. It is an impression.

**Zones never leave a key silent.** A range limit on a single-instrument preset makes
half the keyboard do nothing with nothing on screen saying why -- so ranges appear only
where two zones tile the whole keyboard between them, which is what a split is. The
check below enforces it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import config  # noqa: E402
from backend.engine import Engine  # noqa: E402

LOW, HIGH = config.LOW_KEY, config.HIGH_KEY

# (id, name, description, [zone, ...])
# A zone is (instrument-name, lo, hi, transpose, gain, pan, reverb, chorus, curve).
# Defaults fill the rest; only the first two are required.
Z = dict


def z(name, lo=LOW, hi=HIGH, transpose=0, gain=1.0, pan=0.5,
      reverb=0.30, chorus=0.0, curve="linear", soundfont=None, bank=0, program=0):
    """One zone.

    `name` is looked up against the DEFAULT SoundFont, which is how a typo becomes a
    build error instead of a preset that mysteriously sounds like a piano.

    Pass `soundfont` to point a zone at a different file, and then bank/program are
    taken literally rather than looked up -- a second font is not enumerated here, and
    a preset that names one is asserting it knows what is in it. Zones are per-zone
    all the way down in the engine, so a split or a layer can span two fonts.
    """
    return Z(name=name, lo=lo, hi=hi, transpose=transpose, gain=gain, pan=pan,
             reverb=reverb, chorus=chorus, curve=curve,
             soundfont=soundfont, bank=bank, program=program)


RECIPES = [
    # ── keys ────────────────────────────────────────────────────────────────
    ("grand-piano", "Acoustic Grand", "The default. One instrument, A0 to C8.",
     [z("Grand Piano")]),
    ("bright-piano", "Bright Piano", "Forward and present; cuts through a mix.",
     [z("Bright Grand Piano", reverb=0.22)]),

    ("piano-strings", "Piano + Strings", "Strings under the piano, felt more than heard.",
     [z("Grand Piano", reverb=0.25),
      z("Slow Strings", gain=0.42, reverb=0.55, curve="soft")]),
    ("piano-pad", "Piano + Halo", "A slow pad breathing behind the piano.",
     [z("Grand Piano", reverb=0.25),
      z("Halo Pad", gain=0.34, reverb=0.62, curve="softer")]),
    ("honky-tonk", "Honky Tonk", "Detuned barroom upright.",
     [z("Honky-Tonk Piano", reverb=0.20)]),
    ("rhodes", "Rhodes", "Electric piano, bell-like on top.",
     [z("Tine Electric Piano", reverb=0.30, chorus=0.25)]),
    ("rhodes-warm", "Warm Rhodes", "Rhodes with a pad underneath for ballads.",
     [z("Tine Electric Piano", reverb=0.32, chorus=0.30),
      z("Warm Pad", gain=0.30, reverb=0.60, curve="softer")]),
    ("fm-ep", "FM Electric Piano", "Digital, glassy, eighties.",
     [z("FM Electric Piano", reverb=0.28, chorus=0.20)]),
    ("clav", "Clavinet", "Percussive and funky. Play it staccato.",
     [z("Clavinet", reverb=0.12, curve="hard")]),
    ("harpsichord", "Harpsichord", "Fixed velocity, because the real one has no dynamics.",
     [z("Harpsichord", reverb=0.22, curve="fixed")]),
    ("celesta", "Celesta", "Music box, glassy and small.",
     [z("Celeste", reverb=0.40)]),
    ("toy-piano", "Toy Piano", "Bells an octave up over a quiet upright.",
     [z("Grand Piano", gain=0.55, reverb=0.20),
      z("Glockenspiel", transpose=0, gain=0.55, reverb=0.45)]),

    # ── organs ──────────────────────────────────────────────────────────────
    ("drawbar-organ", "Drawbar Organ", "Hammond-ish, no dynamics -- an organ has none.",
     [z("Tonewheel Organ", reverb=0.25, curve="fixed")]),
    ("rock-organ", "Rock Organ", "Overdriven and loud.",
     [z("Rock Organ", reverb=0.28, chorus=0.30, curve="fixed")]),
    ("church-organ", "Church Organ", "Full stops, long tail.",
     [z("Pipe Organ", reverb=0.75, curve="fixed")]),
    ("gospel-organ", "Gospel Organ", "Percussive organ over a bass pedal.",
     [z("Percussive Organ", lo=48, reverb=0.30, curve="fixed"),
      z("Pipe Organ", lo=LOW, hi=47, gain=0.70, reverb=0.55, curve="fixed")]),
    ("organ-bass-split", "Organ / Bass Split", "Pedal bass left, manual right.",
     [z("Acoustic Bass", lo=LOW, hi=47, gain=0.90, reverb=0.15),
      z("Percussive Organ", lo=48, curve="fixed")]),
    ("accordion", "Accordion", "Bellows and reeds.",
     [z("Accordion", reverb=0.30, chorus=0.35)]),

    # ── EDM and synth ───────────────────────────────────────────────────────
    ("hardstyle-lead", "Hardstyle Lead",
     "Detuned saw stack an octave up, hard curve. Screech, not sparkle.",
     [z("Saw Lead", transpose=12, gain=0.95, reverb=0.18, chorus=0.45, curve="hard"),
      z("5th Saw Wave", transpose=12, gain=0.55, pan=0.62, reverb=0.20, curve="hard")]),
    ("hardstyle-stack", "Hardstyle Stack",
     "The lead with a sub under it, so it has a floor.",
     [z("Saw Lead", lo=52, transpose=12, gain=0.92, chorus=0.45, curve="hard"),
      z("Synth Bass 2", lo=LOW, hi=51, transpose=-12, gain=1.0, reverb=0.08)]),
    ("dubstep-bass", "Dubstep Bass",
     "Low saw plus sub. Harmonic content only -- the wobble is a filter LFO Keys has no way to automate.",
     [z("Synth Bass 2", transpose=-12, gain=1.0, reverb=0.05, curve="hard"),
      z("Synth Bass 1", gain=0.55, reverb=0.05)]),
    ("reese-bass", "Reese Bass", "Two detuned saws beating against each other.",
     [z("Synth Bass 1", transpose=-12, gain=0.95, chorus=0.55, reverb=0.06),
      z("Saw Lead", transpose=-24, gain=0.45, chorus=0.60, reverb=0.06)]),
    ("trance-supersaw", "Trance Supersaw", "Wide, detuned, drenched. Play chords.",
     [z("Saw Lead", gain=0.80, pan=0.35, reverb=0.55, chorus=0.70, curve="soft"),
      z("5th Saw Wave", gain=0.60, pan=0.65, reverb=0.55, chorus=0.70, curve="soft")]),
    ("house-stab", "House Stab", "Short, bright, chord-shaped.",
     [z("Sawtooth Stab", reverb=0.30, chorus=0.25, curve="hard")]),
    ("acid-lead", "Acid Lead", "Squelchy mono-style lead.",
     [z("Square Lead", gain=0.90, reverb=0.22, chorus=0.30, curve="hard")]),
    ("chiptune", "Chiptune", "Square wave, fixed velocity, no reverb. 8-bit.",
     [z("Square Lead", reverb=0.0, curve="fixed")]),
    ("synthwave", "Synthwave", "Bass & lead with a wide pad. Eighties in a box.",
     [z("Bass & Lead", gain=0.85, reverb=0.45, chorus=0.50),
      z("Sweep Pad", gain=0.40, reverb=0.70, curve="softer")]),
    ("saw-stack", "Saw Stack", "Bright saws, layered and detuned.",
     [z("Bright Saw Stack", gain=0.85, pan=0.40, reverb=0.40, chorus=0.55),
      z("Saw Lead", gain=0.50, pan=0.60, reverb=0.40, chorus=0.55)]),
    ("synth-brass-stack", "Synth Brass", "Fat, punchy, eighties brass.",
     [z("Synth Brass 1", gain=0.90, reverb=0.35, chorus=0.30, curve="hard"),
      z("Synth Brass 2", gain=0.55, reverb=0.35)]),
    ("vocoder-voice", "Vocoder Voice", "Synth voice over a saw. Robot choir.",
     [z("Synth Voice", gain=0.85, reverb=0.50, chorus=0.40),
      z("Saw Lead", gain=0.30, reverb=0.45, curve="soft")]),

    # ── world ───────────────────────────────────────────────────────────────
    ("didgeridoo", "Didgeridoo",
     "An impression, not the instrument -- GM has no didgeridoo. A low reed drone with a "
     "vocal formant layer, ranged to the bottom two octaves so it plays like one.",
     [z("Bassoon", transpose=-12, gain=1.0, reverb=0.55, curve="soft"),
      z("Synth Voice", transpose=-12, gain=0.45, reverb=0.65, curve="softer"),
      z("Tuba", transpose=-12, gain=0.35, reverb=0.50, curve="soft")]),
    ("sitar", "Sitar", "Drone-friendly; hold the low strings.",
     [z("Sitar", reverb=0.55)]),
    ("koto", "Koto", "Japanese zither.", [z("Koto", reverb=0.45)]),
    ("kalimba", "Kalimba", "Thumb piano, soft and round.",
     [z("Kalimba", reverb=0.45, curve="soft")]),
    ("bagpipe", "Bagpipe", "Fixed velocity, because a bag has one pressure.",
     [z("Bagpipes", reverb=0.45, curve="fixed")]),
    ("shakuhachi", "Shakuhachi", "Breathy bamboo flute.",
     [z("Shakuhachi", reverb=0.60, curve="soft")]),
    ("steel-drum", "Steel Drum", "Pan, bright and tuned.",
     [z("Steel Drums", reverb=0.40)]),
    ("taiko", "Taiko", "Big drum. Play the bottom octave.",
     [z("Taiko Drum", reverb=0.50, curve="hard")]),

    # ── strings, brass, winds ───────────────────────────────────────────────
    ("strings", "String Ensemble", "Full section, slow attack.",
     [z("Slow Strings", reverb=0.60, curve="soft")]),
    ("cinematic-strings", "Cinematic Strings", "Section plus choir. Big and slow.",
     [z("Slow Strings", gain=0.85, reverb=0.70, curve="softer"),
      z("Concert Choir", gain=0.45, reverb=0.78, curve="softer")]),
    ("solo-violin", "Solo Violin", "One player, exposed.",
     [z("Violin", reverb=0.50, curve="soft")]),
    ("brass-section", "Brass Section", "Punchy horns; dig in for the top.",
     [z("Brass Section", reverb=0.35, curve="hard")]),
    ("sax", "Tenor Sax", "Breathy and expressive.",
     [z("Tenor Sax", reverb=0.40, curve="soft")]),
    ("flute", "Flute", "Clean and airy.", [z("Flute", reverb=0.50, curve="soft")]),

    # ── guitars and basses ──────────────────────────────────────────────────
    ("nylon-guitar", "Nylon Guitar", "Fingerpicked classical.",
     [z("Nylon Guitar", reverb=0.35)]),
    ("steel-guitar", "Steel Guitar", "Strummed acoustic.",
     [z("Steel Guitar", reverb=0.32)]),
    ("clean-electric", "Clean Electric", "Chorused clean tone.",
     [z("Clean Guitar", reverb=0.30, chorus=0.40)]),
    ("overdrive-guitar", "Overdrive Guitar", "Dirty, sustaining.",
     [z("Overdrive Guitar", reverb=0.28, curve="hard")]),
    ("bass-split", "Bass / Piano Split", "Walking bass left, piano right.",
     [z("Acoustic Bass", lo=LOW, hi=47, gain=0.90, reverb=0.15),
      z("Grand Piano", lo=48)]),
    ("fretless-split", "Fretless / Rhodes", "Fretless bass under a Rhodes.",
     [z("Fretless Bass", lo=LOW, hi=47, gain=0.90, reverb=0.20),
      z("Tine Electric Piano", lo=48, chorus=0.25)]),
    ("slap-bass", "Slap Bass", "Play it hard.",
     [z("Slap Bass 1", reverb=0.15, curve="hard")]),
    ("upright-bass", "Upright Bass", "Acoustic, for walking lines.",
     [z("Acoustic Bass", reverb=0.25)]),

    # ── pads and atmospheres ────────────────────────────────────────────────
    ("poly-pad", "Poly Pad", "Warm and wide. Hold chords.",
     [z("Warm Pad", gain=0.85, pan=0.40, reverb=0.65, curve="softer"),
      z("Polysynth", gain=0.45, pan=0.60, reverb=0.60, curve="soft")]),
    ("glass-pad", "Glass Pad", "Bowed glass, cold and clear.",
     [z("Bowed Glass", reverb=0.72, curve="softer")]),
    ("space-pad", "Space Pad", "Atmosphere plus halo. Very slow.",
     [z("Atmosphere", gain=0.80, reverb=0.80, curve="softer"),
      z("Halo Pad", gain=0.45, reverb=0.80, curve="softer")]),
    ("metal-pad", "Metal Pad", "Ringing and metallic.",
     [z("Metal Pad", reverb=0.68, curve="soft")]),
    ("choir", "Choir", "Aahs, wide and slow.",
     [z("Concert Choir", reverb=0.75, curve="softer")]),
    ("horror-pad", "Horror Pad", "Goblin over a low drone. Not for practice.",
     [z("Goblin", gain=0.80, reverb=0.78, curve="softer"),
      z("Sweep Pad", transpose=-12, gain=0.45, reverb=0.80, curve="softer")]),

    # ── percussion ──────────────────────────────────────────────────────────
    ("drum-pads", "Drum Pads", "The standard kit across the bottom, piano above.",
     [Z(name="Standard 1", lo=LOW, hi=59, transpose=0, gain=1.0, pan=0.5,
        reverb=0.20, chorus=0.0, curve="linear", drums=True),
      z("Grand Piano", lo=60)]),
    ("vibraphone", "Vibraphone", "Mallets with a slow chorus.",
     [z("Vibraphone", reverb=0.45, chorus=0.35, curve="soft")]),
    ("marimba", "Marimba", "Wooden and dry.", [z("Marimba", reverb=0.30)]),
    ("music-box", "Music Box", "Tiny and nostalgic.",
     [z("Music Box", reverb=0.50, curve="soft")]),
]


def build() -> tuple[list[dict], list[str]]:
    eng = Engine()
    eng.start()
    if not eng.started:
        return [], ["audio engine did not start -- cannot read the SoundFont"]
    catalogue = eng.list_presets()
    eng.stop()

    by_name: dict[str, dict] = {}
    for entry in catalogue:
        by_name.setdefault(entry["name"], entry)

    out: list[dict] = []
    errors: list[str] = []
    for pid, name, desc, zones in RECIPES:
        built = []
        for i, spec in enumerate(zones):
            inst_name = spec["name"]
            if spec.get("soundfont"):
                # A named font: bank/program are taken as given, and the only thing
                # that can be checked here is that the file is actually present.
                if config.find_asset("soundfonts", spec["soundfont"]) is None:
                    errors.append(f"{pid}: soundfont {spec['soundfont']!r} is not in soundfonts/")
                    continue
                found = {"bank": spec["bank"], "program": spec["program"], "drums": False}
                font = spec["soundfont"]
            else:
                found = by_name.get(inst_name)
                if found is None:
                    errors.append(f"{pid}: no instrument called {inst_name!r} in the SoundFont")
                    continue
                font = config.DEFAULT_SOUNDFONT
            built.append({
                "id": f"z{i + 1}", "name": inst_name,
                "lo": spec["lo"], "hi": spec["hi"],
                # Channel 9 is the GM drum channel and 15 is the metronome's.
                "channel": 9 if found["drums"] else (i if i < 9 else i + 1),
                "soundfont": font,
                "bank": found["bank"], "program": found["program"],
                "transpose": spec["transpose"], "gain": spec["gain"], "pan": spec["pan"],
                "reverb": spec["reverb"], "chorus": spec["chorus"],
                "curve": spec["curve"], "fixed_velocity": 100, "enabled": True,
            })
        if len(built) == len(zones):
            out.append({"id": pid, "name": name, "description": desc, "zones": built})
    return out, errors


def main() -> int:
    presets, errors = build()
    for e in errors:
        print(f"  [FAIL] {e}")
    if errors:
        print(f"\n{len(errors)} recipe(s) reference instruments this SoundFont does not have.")
        return 1

    ids = [p["id"] for p in presets]
    if len(set(ids)) != len(ids):
        dupes = {i for i in ids if ids.count(i) > 1}
        print(f"  [FAIL] duplicate preset ids: {sorted(dupes)}")
        return 1

    dead_report = []
    for preset in presets:
        covered = set()
        for zone in preset["zones"]:
            covered |= set(range(zone["lo"], zone["hi"] + 1))
        dead = sorted(set(range(LOW, HIGH + 1)) - covered)
        if dead:
            dead_report.append(f"{preset['id']}: {len(dead)} silent keys ({dead[0]}-{dead[-1]})")
    if dead_report:
        for line in dead_report:
            print(f"  [FAIL] {line}")
        print("\nA preset that leaves keys silent is a preset that looks broken.")
        return 1

    layered = sum(1 for p in presets if len(p["zones"]) > 1)
    print(f"  {len(presets)} presets, {layered} of them layered or split")
    if "--check" in sys.argv:
        print("  --check: nothing written")
        return 0

    out_dir = ROOT / "presets"
    out_dir.mkdir(parents=True, exist_ok=True)
    keep = {f"{p['id']}.json" for p in presets}
    for old in out_dir.glob("*.json"):
        if old.name not in keep:
            old.unlink()
            print(f"  removed {old.name}")
    for p in presets:
        (out_dir / f"{p['id']}.json").write_text(json.dumps(p, indent=2), "utf-8")
    print(f"  wrote {len(presets)} files to presets/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
