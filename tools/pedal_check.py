"""Regression test for the sustain pedal modes.

    .venv\\Scripts\\python.exe tools\\pedal_check.py

Runs against a stub Synth that records every noteon/noteoff instead of making one, so
it needs no audio device, makes no noise, and can run while Keys is open. That is the
point: this is note-level bookkeeping on the hot path, and the questions it has to
answer -- did that note stop, is it still ringing, did re-striking it cut the old tail
-- are exactly the ones you cannot hear the difference on.

The pedal modes exist because a P-71 has one pedal and a grand has three. `zone`
sustains a key range so the left hand rings under a dry right hand; `sostenuto` is the
grand's middle pedal, catching what is already sounding and nothing after; `hold`
latches a momentary pedal; and the decay releases a caught note on a timer.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config  # noqa: E402
from backend.engine import PEDAL_MODES, SUSTAIN_CC, Engine, Zone  # noqa: E402

ok = True


def step(label: str, passed: bool, detail: str = "") -> None:
    global ok
    ok = ok and passed
    print(f"  [{'PASS' if passed else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))


class StubSynth:
    """Records what the engine asked the synth to do. Makes no sound."""

    def __init__(self) -> None:
        self.sounding: set[tuple[int, int]] = set()
        self.ccs: list[tuple[int, int, int]] = []

    def noteon(self, ch: int, key: int, vel: int) -> None:
        self.sounding.add((ch, key))

    def noteoff(self, ch: int, key: int) -> None:
        self.sounding.discard((ch, key))

    def cc(self, ch: int, num: int, val: int) -> None:
        self.ccs.append((ch, num, val))

    def program_select(self, *_a) -> int:
        return 0

    def sfload(self, _path: str) -> int:
        return 1

    def get_active_voice_count(self) -> int:
        return len(self.sounding)


def fresh(mode: str = "", **kw) -> tuple[Engine, StubSynth]:
    eng = Engine(config.Settings(Path(tempfile.mkdtemp(prefix="keys-pedal-")) / "s.json"))
    fs = StubSynth()
    eng.fs = fs
    eng.set_zones([Zone()], "t", "Test")
    fs.ccs.clear()
    eng.set_pedal(mode, **kw)
    return eng, fs


def ringing(fs: StubSynth) -> set[int]:
    return {key for _ch, key in fs.sounding}


def press(eng: Engine, *notes: int) -> None:
    for n in notes:
        eng.note_on(n, 90)


def lift(eng: Engine, *notes: int) -> None:
    for n in notes:
        eng.note_off(n)


def pedal(eng: Engine, down: bool) -> None:
    eng.control(SUSTAIN_CC, 127 if down else 0)


print("1. the default is the damper, and the synth does it")
eng, fs = fresh()
step("mode is empty", eng.pedal_mode == "", "FluidSynth handles CC 64 itself")
pedal(eng, True)
step("CC 64 goes down the wire", (0, SUSTAIN_CC, 127) in fs.ccs, str(fs.ccs[-1:]))
step("python does not arm itself", eng._pedal_down is False)  # noqa: SLF001
press(eng, 60)
lift(eng, 60)
step("note-off is not held back", 60 not in ringing(fs),
     "the synth is sustaining it, not us")
pedal(eng, False)

print("2. zone -- the left hand rings, the right hand stays dry")
eng, fs = fresh("zone", lo=21, hi=59)
step("CC 64 is swallowed", not any(c[1] == SUSTAIN_CC and c[2] for c in fs.ccs),
     "or the synth would sustain the whole channel too")
pedal(eng, True)
press(eng, 40, 72)          # one below the split, one above
lift(eng, 40, 72)
step("a key in the zone keeps ringing", 40 in ringing(fs))
step("a key outside it stops", 72 not in ringing(fs))
step("engine reports what it is holding", eng.pedal_status()["holding"] == [40])
pedal(eng, False)
step("pedal up releases it", 40 not in ringing(fs))
step("and forgets it", eng.pedal_status()["holding"] == [])

print("3. sostenuto -- the middle pedal of a grand")
eng, fs = fresh("sostenuto")
press(eng, 36)              # a bass note, sounding when the pedal goes down
pedal(eng, True)
press(eng, 64, 67)          # played AFTER, so the pedal must not catch these
lift(eng, 36, 64, 67)
step("what was held when you pressed keeps ringing", 36 in ringing(fs))
step("what you played after does not", 64 not in ringing(fs) and 67 not in ringing(fs),
     "this is the whole difference from a damper pedal")
step("holding reports only the caught note", eng.pedal_status()["holding"] == [36])
pedal(eng, False)
step("pedal up clears it", ringing(fs) == set())

print("4. hold -- a latch for a momentary pedal")
eng, fs = fresh("hold")
pedal(eng, True)
pedal(eng, False)           # a real pedal springs back; the latch must not
step("the release is ignored", eng._pedal_down is True)  # noqa: SLF001
press(eng, 60)
lift(eng, 60)
step("still sustaining with your foot off", 60 in ringing(fs))
pedal(eng, True)            # second press
step("a second press unlatches", eng._pedal_down is False)  # noqa: SLF001
step("and releases what was held", 60 not in ringing(fs))
pedal(eng, False)
press(eng, 62)
lift(eng, 62)
step("normal again afterwards", 62 not in ringing(fs))

print("5. re-striking a key the pedal is holding")
eng, fs = fresh("zone", lo=21, hi=108)
pedal(eng, True)
press(eng, 60)
lift(eng, 60)
step("held by the pedal", eng.pedal_status()["holding"] == [60])
press(eng, 60)              # play it again while its tail is ringing
step("the new strike takes ownership", eng.pedal_status()["holding"] == [],
     "or the pedal would release a voice that now belongs to the new note")
step("and it is sounding", 60 in ringing(fs))
lift(eng, 60)
step("the new one is caught in its turn", eng.pedal_status()["holding"] == [60])
pedal(eng, False)

print("6. decay -- a sustain that lets go on its own")
eng, fs = fresh("zone", lo=21, hi=108, decay=2.0)
step("decay stored", eng.pedal_decay == 2.0)
pedal(eng, True)
press(eng, 60)
lift(eng, 60)
step("ringing immediately after release", 60 in ringing(fs))
# decay_tick takes `now` as an argument precisely so this can be tested without
# sleeping: feed it a time relative to when the note was caught.
faded = eng.decay_tick(eng._pedal_at[60] + 1.0)  # noqa: SLF001
step("nothing released at 1.0 s of a 2.0 s decay", faded == [] and 60 in ringing(fs))
faded = eng.decay_tick(eng._pedal_at[60] + 2.5)  # noqa: SLF001
step("released once the decay has run", faded == [60] and 60 not in ringing(fs))
step("and reported so the UI stops drawing it", faded == [60])
step("no decay means hold until the pedal comes up",
     fresh("zone", lo=21, hi=108)[0].decay_tick(1e9) == [])

print("7. switching modes does not strand a note")
eng, fs = fresh("zone", lo=21, hi=108)
pedal(eng, True)
press(eng, 60, 64)
lift(eng, 60, 64)
step("two notes held by the pedal", eng.pedal_status()["holding"] == [60, 64])
eng.set_pedal("sostenuto")
step("switching releases them", ringing(fs) == set(),
     "they are not the new mode's to hold")
step("and clears the list", eng.pedal_status()["holding"] == [])

eng, fs = fresh("zone", lo=21, hi=108)
pedal(eng, True)
press(eng, 60)
lift(eng, 60)
eng.set_pedal("")           # back to the native damper
step("returning to the damper releases too", 60 not in ringing(fs))
step("and tells the synth the pedal is up",
     any(c[1] == SUSTAIN_CC and c[2] == 0 for c in fs.ccs),
     "otherwise the synth inherits a down-edge we swallowed")

print("8. panic and teardown clear the pedal")
eng, fs = fresh("zone", lo=21, hi=108)
pedal(eng, True)
press(eng, 60, 64, 67)
lift(eng, 60, 64, 67)
step("three notes pedalled", len(eng.pedal_status()["holding"]) == 3)
eng.panic()
step("panic clears them", eng.pedal_status()["holding"] == [] and not eng._pedal_down)  # noqa: SLF001

eng, fs = fresh("zone", lo=21, hi=108)
pedal(eng, True)
press(eng, 60)
lift(eng, 60)
eng._suspend_hot_path()  # noqa: SLF001
step("a restart clears them", eng.pedal_status()["holding"] == [],
     "the callback thread must not release into a freed Synth")

print("9. the master octave moves under the pedal without orphaning a voice")
# note_on drops the pedal's claim on a re-struck key, on the theory that the new strike
# steals the same voice. That holds only while the key comes out at the same pitch. The
# octave shift is baked into the routing table, so a key pedalled at OCT 0 and re-struck
# at OCT +1 is a DIFFERENT voice -- and the pedalled one was left ringing with nothing
# holding a reference to it. No note-off could ever reach it: a stuck note for the rest
# of the session, in all three managed pedal modes.
for _mode, _kw in (("zone", {"lo": 21, "hi": 108}), ("hold", {}), ("sostenuto", {})):
    eng, fs = fresh(_mode, **_kw)
    press(eng, 60)
    eng.control(SUSTAIN_CC, 127)          # pedal down (latches in "hold")
    lift(eng, 60)                          # the pedal now owns the voice at 60
    caught = ringing(fs)
    eng.set_master_octave(1)               # 60 now comes out at 72
    press(eng, 60)                         # re-strike: a different voice entirely
    lift(eng, 60)
    # "hold" latches: a release means nothing to it and a second PRESS is what lets go.
    eng.control(SUSTAIN_CC, 127 if _mode == "hold" else 0)
    step(f"{_mode}: nothing is left ringing", ringing(fs) == set(),
         f"pedal caught {sorted(caught)}, left behind {sorted(ringing(fs))}")

eng, fs = fresh("zone", lo=21, hi=108)
press(eng, 60)
eng.control(SUSTAIN_CC, 127)
lift(eng, 60)
press(eng, 60)
step("re-striking at the SAME pitch still steals the voice", ringing(fs) == {60},
     str(ringing(fs)))
lift(eng, 60)
eng.control(SUSTAIN_CC, 0)
step("and it is released once, not twice", ringing(fs) == set(), str(ringing(fs)))

eng, fs = fresh("zone", lo=21, hi=108)
press(eng, 100)
eng.control(SUSTAIN_CC, 127)
lift(eng, 100)
eng.set_master_octave(4)                   # 100 + 48 = 148: no route at all
press(eng, 100)                            # the empty-routes early return used to skip it
eng.control(SUSTAIN_CC, 0)
step("a shift off the end of MIDI does not orphan it either", ringing(fs) == set(),
     str(ringing(fs)))

print("9b. bad input is clamped, not obeyed")
eng, _fs = fresh()
eng.set_pedal("nonsense")
step("an unknown mode falls back to the damper", eng.pedal_mode == "")
eng.set_pedal("zone", lo=200, hi=-5)
step("range clamped to MIDI", eng.pedal_lo == 0 and eng.pedal_hi == 127,
     f"{eng.pedal_lo}-{eng.pedal_hi}")
# What is STORED is what you asked for, clamped only to notes that exist at all. What
# APPLIES is that met with the keyboard you have. Keeping them apart is what makes
# declaring a smaller keyboard non-destructive -- see the note in set_pedal.
step("but what applies is the keyboard you have",
     eng._pedal_span() == config.instrument_range(eng.settings),  # noqa: SLF001
     f"{eng._pedal_span()}")  # noqa: SLF001

print("   narrowing the instrument does not eat a saved pedal zone")
eng, _fs = fresh()
eng.set_pedal("zone", lo=21, hi=108)
eng.settings.update({"instrument": {"low": 48, "high": 72}})
eng.apply_instrument()
step("the pedal still applies only where keys exist", eng._pedal_span() == (48, 72),  # noqa: SLF001
     f"{eng._pedal_span()}")  # noqa: SLF001
step("and the saved zone is untouched", eng.pedal_lo == 21 and eng.pedal_hi == 108,
     f"{eng.pedal_lo}-{eng.pedal_hi}")
eng.settings.update({"instrument": {"low": 21, "high": 108}})
eng.apply_instrument()
step("so widening back restores it exactly", eng._pedal_span() == (21, 108),  # noqa: SLF001
     f"{eng._pedal_span()}")  # noqa: SLF001

eng, _fs = fresh()
eng.set_pedal("zone", lo=200, hi=-5)
eng.set_pedal("zone", lo=80, hi=40)
step("a backwards range is swapped, not empty",
     eng.pedal_lo == 40 and eng.pedal_hi == 80)
eng.set_pedal("zone", decay=999)
step("decay clamped", eng.pedal_decay == 30.0)
eng.set_pedal("zone", decay=-3)
step("and never negative", eng.pedal_decay == 0.0)
step("every mode is reachable", set(eng.pedal_status()["modes"]) == {"", *PEDAL_MODES})

print()
print("ALL CHECKS PASSED" if ok else "FAILURES ABOVE")
sys.exit(0 if ok else 1)
