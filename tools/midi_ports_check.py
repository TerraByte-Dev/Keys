"""MIDI port binding, against a simulated two-port controller. No hardware needed.

The bug this exists to prevent: Keys used to open exactly one MIDI input -- index 0, or
a saved index -- and a controller that exposes two inputs had a one-in-two chance of
having its keys on the port nobody opened. An Alesis V49 enumerates as "V49" and
"MIDIIN2 (V49)", and when the wrong one was open the app listed both ports, reported no
error, lit no keys and made no sound. Indistinguishable from broken.

It listens to every input at once now, so there is nothing to get wrong. This file holds
the cases that has to keep satisfying, including the two that make listening-to-all safe:
a device that mirrors its keys onto both ports must not sound every note twice, and one
port being held by another application must not take the rest down with it.

    .venv\\Scripts\\python tools\\midi_ports_check.py
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ok = True


def step(label: str, passed: bool, detail: str = "") -> None:
    global ok
    ok = ok and passed
    print(f"  [{'PASS' if passed else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))


# --- a fake rtmidi, installed before backend.midi_in imports it ----------------
PORTS = ["V49", "MIDIIN2 (V49)"]
opened: dict[int, "FakeMidiIn"] = {}


class FakeMidiIn:
    def __init__(self) -> None:
        self.cb = None
        self.idx: int | None = None

    def get_ports(self) -> list[str]:
        return list(PORTS)

    def open_port(self, i: int) -> None:
        # Windows MIDI inputs are exclusive; a port another app holds raises here.
        if i in opened:
            raise RuntimeError("port already in use")
        self.idx = i
        opened[i] = self

    def ignore_types(self, **_kw: object) -> None:
        pass

    def set_callback(self, cb) -> None:
        self.cb = cb

    def cancel_callback(self) -> None:
        self.cb = None

    def close_port(self) -> None:
        opened.pop(self.idx, None)
        self.idx = None


sys.modules.setdefault("rtmidi", types.ModuleType("rtmidi"))
import rtmidi  # noqa: E402

rtmidi.MidiIn = FakeMidiIn

from backend import midi_in as mi  # noqa: E402


class FakeEngine:
    def __init__(self) -> None:
        self.notes: list[tuple] = []

    def note_on(self, n: int, v: int) -> None:
        self.notes.append(("on", n, v))

    def note_off(self, n: int) -> None:
        self.notes.append(("off", n))

    def control(self, c: int, v: int) -> None:
        pass

    def bend(self, v: int) -> None:
        pass

    def panic(self) -> None:
        pass


class FakeHub:
    def push(self, *_a: object) -> None:
        pass


def fresh() -> "mi.MidiInput":
    opened.clear()
    return mi.MidiInput(FakeEngine(), FakeHub())


print("MIDI port binding -- simulated Alesis V49 (two input ports)")
print()

print("1. the default is to listen to every input")
m = fresh()
opened_ok = m.open_named(None)
st = m.status()
step("opened", opened_ok)
step("not pinned to one port", st["listening_to_all"])
step("every port is live", all(p["listening"] for p in st["ports"]),
     ", ".join(p["name"] for p in st["ports"]))

print("2. keys on the SECOND port still reach the engine")
opened[1].cb(([0x90, 60, 100], 0.0))
step("note routed", m.engine.notes == [("on", 60, 100)], str(m.engine.notes))
counts = {p["name"]: p["messages"] for p in m.status()["ports"]}
step("per-port counters name the live port", counts == {"V49": 0, "MIDIIN2 (V49)": 1}, str(counts))

print("3. a device mirroring one key onto both ports sounds it once")
m2 = fresh()
m2.open_named(None)
opened[0].cb(([0x90, 64, 90], 0.0))
opened[1].cb(([0x90, 64, 90], 0.0))
step("de-duped across ports", m2.engine.notes == [("on", 64, 90)], str(m2.engine.notes))

print("4. the SAME port repeating is never de-duped -- that is real playing")
m3 = fresh()
m3.open_named(None)
opened[0].cb(([0x90, 67, 80], 0.0))
opened[0].cb(([0x90, 67, 80], 0.0))
step("both strikes sounded", len(m3.engine.notes) == 2, str(m3.engine.notes))

print("5. pinning by NAME survives the ports being renumbered")
m4 = fresh()
m4.open_named("MIDIIN2 (V49)")
PORTS.insert(0, "loopMIDI Port")        # something else plugged in ahead of it
m4._sync_locked()
live = [p["name"] for p in m4.status()["ports"] if p["listening"]]
step("still on the device that was chosen", live == ["MIDIIN2 (V49)"], str(live))
PORTS.pop(0)

print("6. one port held by another app does not take the rest down")
m5 = fresh()
blocker = FakeMidiIn()
blocker.open_port(0)
opened_ok = m5.open_named(None)
live = [p["name"] for p in m5.status()["ports"] if p["listening"]]
step("open still succeeds", opened_ok)
step("the free port is listening", live == ["MIDIIN2 (V49)"], str(live))

print("7. a LEGACY midi_port index does not brick MIDI")
# The regression that shipped in 0.8.0. midi_port used to hold an index; the new build
# reads it as a name, so a saved 1 pinned to a device called "1", matched nothing, and
# opened nothing -- "could not open any MIDI input" with every port listed and silent.
# Index 0 survived only because 0 is falsy, so this hit exactly the people who had gone
# looking for a working port.
for legacy in (0, 1, 5, "1", True):
    m6 = fresh()
    opened_ok = m6.open_named(legacy)
    live = [p["name"] for p in m6.status()["ports"] if p["listening"]]
    step(f"midi_port={legacy!r} still hears the keyboard", opened_ok and len(live) == 2,
         f"pinned={m6.pinned!r} listening={live}")

print("8. a pin naming a device that is not plugged in falls back, loudly")
m7 = fresh()
m7.open_named("Some Piano That Is Not Here")
st7 = m7.status()
live = [p["name"] for p in st7["ports"] if p["listening"]]
step("still listening to everything", len(live) == 2, str(live))
step("and says which device is missing",
     st7["unresolved_pin"] == "Some Piano That Is Not Here", st7["unresolved_pin"])
step("reported as listening-to-all", st7["listening_to_all"])

print("9. a pin that DOES resolve is still honoured")
m8 = fresh()
m8.open_named("V49")
st8 = m8.status()
live = [p["name"] for p in st8["ports"] if p["listening"]]
step("only the chosen port", live == ["V49"], str(live))
step("no false unresolved flag", st8["unresolved_pin"] == "")

print("10. pinning a port another app holds does NOT close the one that works")
# _sync_locked used to shut before it opened, so this click closed the working input,
# failed to open the held one, and left zero ports open -- which the 2 s watcher then
# retried forever. Deaf, one click away, in the release built to prevent exactly that.
m9 = fresh()
daw = FakeMidiIn()
daw.open_port(0)                          # a DAW already holds "V49"
m9.open_named(None)                       # so Keys comes up on MIDIIN2 only
step("came up on the free port", sorted(n for _, n in m9._open.values()) == ["MIDIIN2 (V49)"],
     str(sorted(n for _, n in m9._open.values())))
ok9 = m9.open(0)                          # user now pins the port the DAW is holding
live = sorted(n for _, n in m9._open.values())
step("the pin is reported as failed", not ok9)
step("but the working input is STILL open", live == ["MIDIIN2 (V49)"], str(live))
step("and the error says what is still live", "still listening to" in m9.last_error,
     m9.last_error[:80])
m9._sync_locked()                         # what the 2 s watcher does, repeatedly
step("the watcher does not make it worse", bool(m9._open),
     str(sorted(n for _, n in m9._open.values())))

print("11. the message counter follows the DEVICE, not the slot")
# A release whose thesis is "indices are unreliable" must not key its own diagnostic by
# one, or a count strands itself on a port that never sent anything after a hotplug.
m10 = fresh()
m10.open_named(None)
opened[1].cb(([0x90, 60, 100], 0.0))      # MIDIIN2 sends one
before = {p["name"]: p["messages"] for p in m10.status()["ports"]}
PORTS.insert(0, "loopMIDI Port")          # everything renumbers
m10._sync_locked()
after = {p["name"]: p["messages"] for p in m10.status()["ports"]}
step("count stayed with MIDIIN2 (V49)", after.get("MIDIIN2 (V49)") == 1, str(after))
step("no count invented for the newcomer", after.get("loopMIDI Port", 0) == 0, str(after))
PORTS.pop(0)

print()
print("ALL CHECKS PASSED" if ok else "FAILURES ABOVE")
sys.exit(0 if ok else 1)
