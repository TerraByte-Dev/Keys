"""MIDI input. The callback in here is the most performance-sensitive code in the app.

The P-71B transmits MIDI Clock (0xF8) roughly 24 times a second forever, plus Active
Sensing (0xFE) every ~300 ms, whether or not anyone is playing. Measured over one
session: 5171 messages, 5096 of them noise -- 98.5%. So the first branch is
`if status >= 0xF8: return`, before the clock is even read. rtmidi is *also* told to
filter them; both belts are cheap and the failure mode of neither is acceptable.

Everything after that: route to the synth first, tell the UI second. Sound wins.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

import rtmidi

from . import hub as hub_mod
from .engine import Engine
from .hub import Hub

WATCH_INTERVAL = 2.0
SUSTAIN_CC = 64
# How close two identical messages from two different ports have to be to count as one
# device mirroring itself rather than two things being played. A human cannot strike the
# same key twice in 8 ms; USB MIDI mirrors land within one poll interval of each other.
DEDUPE_S = 0.008


def _make_callback(engine: Engine, hub: Hub, seen: dict, counts: dict, name: str, slot: int) -> Callable:
    """Build the hot callback with every lookup it needs bound as a local.

    `name` is the port this callback belongs to and `counts` is the shared dict it bumps,
    keyed by NAME rather than by index -- a release whose whole thesis is that indices are
    unreliable must not key its own diagnostic by one, or a count follows the slot across
    a hotplug and strands itself on a port that never sent anything. `slot` is still
    carried for the de-dupe, where it only ever has to distinguish two live callbacks
    from each other. `seen` is the de-dupe window -- see DEDUPE_S below.
    """
    perf = time.perf_counter
    note_on = engine.note_on
    note_off = engine.note_off
    control = engine.control
    bend = engine.bend
    push = hub.push
    NOTE_ON, NOTE_OFF, CONTROL, BEND = (
        hub_mod.NOTE_ON, hub_mod.NOTE_OFF, hub_mod.CONTROL, hub_mod.BEND,
    )

    def on_midi(event: tuple, _data: Any = None) -> None:
        # HOT PATH. No print, no logging, no await, no lock, no I/O.
        msg, _delta = event
        status = msg[0]
        if status >= 0xF8:          # clock, active sensing, reset -- 98.5% of traffic
            return
        t0 = perf()
        counts[name] = counts.get(name, 0) + 1
        # Cross-port de-dupe. Listening to every input is what makes a two-port
        # controller work without anyone choosing a port, and the price is that a device
        # which MIRRORS its keys onto both ports would sound every note twice. One dict
        # write and one compare on the hot path buys that back: an identical status+data1
        # from a DIFFERENT port inside the window is the mirror, and is dropped. The same
        # port repeating is never dropped -- that is a real repeated note.
        key = (status, msg[1] if len(msg) > 1 else 0)
        prev = seen.get(key)
        if prev is not None and prev[0] != slot and t0 - prev[1] < DEDUPE_S:
            return
        seen[key] = (slot, t0)
        kind = status & 0xF0
        if kind == 0x90:
            note = msg[1]
            vel = msg[2]
            if vel:
                note_on(note, vel)
                push(t0, NOTE_ON, note, vel, perf() - t0)
                return
            note_off(note)          # note-on with velocity 0 is a note-off
            push(t0, NOTE_OFF, note, 0, perf() - t0)
        elif kind == 0x80:
            note = msg[1]
            note_off(note)
            push(t0, NOTE_OFF, note, 0, perf() - t0)
        elif kind == 0xB0:
            cc = msg[1]
            val = msg[2]
            control(cc, val)
            push(t0, CONTROL, cc, val, perf() - t0)
        elif kind == 0xE0:
            value = ((msg[2] << 7) | msg[1]) - 8192
            bend(value)
            push(t0, BEND, value, 0, perf() - t0)

    return on_midi


class MidiInput:
    """Owns the rtmidi ports, and reopens them when the piano is unplugged and back.

    Listens to EVERY input port by default, which is the whole point. A single-port
    piano like the P-71B never made the old "open port 0" behaviour wrong, so it stood
    for months -- but a controller that exposes two inputs (an Alesis V49 shows up as
    "V49" and "MIDIIN2 (V49)") had a one-in-two chance of having its keys on the port
    nobody opened, and the failure looked exactly like a broken app: ports listed, no
    error, no sound, nothing on the on-screen keyboard.

    Pinning one port is still possible and is what `midi_port` means when set. It is
    stored as a NAME rather than an index because Windows hands out indices by
    enumeration order, and that order changes when anything else MIDI is plugged in --
    so a saved index silently came to mean a different device.
    """

    def __init__(self, engine: Engine, hub: Hub) -> None:
        self.engine = engine
        self.hub = hub
        self._probe = rtmidi.MidiIn()
        self._lock = threading.Lock()
        # slot -> (rtmidi.MidiIn, port name). Slots are indices into self.counts.
        self._open: dict[int, tuple] = {}
        self._names: list[str] = []
        self.counts: dict[str, int] = {}
        self._seen: dict = {}
        self.pinned: str = ""          # "" = listen to everything
        # Set when a pinned name matches no present port -- we are listening to
        # everything instead, and the UI needs to be able to say why.
        self.unresolved_pin: str = ""
        self.last_error: str = ""
        self._watch_stop = threading.Event()
        self._watcher: threading.Thread | None = None

    # ------------------------------------------------------------------ ports
    def list_ports(self) -> list[str]:
        try:
            return list(self._probe.get_ports())
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            return []

    def open(self, index: int | None = None) -> bool:
        """Pin one port by index, or listen to everything when index is None."""
        with self._lock:
            ports = self.list_ports()
            if index is None:
                self.pinned = ""
            else:
                idx = int(index)
                if idx < 0 or idx >= len(ports):
                    self.last_error = f"no MIDI port {idx} (found {len(ports)})"
                    return False
                self.pinned = ports[idx]
            return self._sync_locked(ports)

    def open_named(self, name: object) -> bool:
        """Pin by port name, or listen to everything when name is empty.

        Accepts a legacy value and throws it away. `midi_port` used to hold an INDEX,
        and reading an old one as a name pins to a device called "1" -- which matches
        nothing, opens nothing, and reports "could not open any MIDI input" while every
        port sits there listed and silent. Index 0 survived only because 0 is falsy, so
        this bit exactly the people who had gone looking for a working port.

        Discarded rather than resolved: an index means whatever the enumeration order
        means today, which is the reason it stopped being what this field stores. The
        new default -- listen to everything -- is what they wanted from the choice anyway.
        """
        with self._lock:
            if isinstance(name, bool) or isinstance(name, int):
                name = ""
            elif isinstance(name, str) and name.strip().isdigit():
                name = ""
            self.pinned = str(name or "").strip()
            return self._sync_locked()

    def _wanted(self, ports: list[str]) -> list[int]:
        """Which port indices we should be listening to right now."""
        if not self.pinned:
            # Cleared here too. It used to be reset only on the branch where a pin
            # resolves, so unpinning left the old "X is not here" line on screen forever.
            self.unresolved_pin = ""
            return list(range(len(ports)))
        match = [i for i, n in enumerate(ports) if n == self.pinned]
        # A pin that resolves to nothing falls back to everything, and says so. The
        # first version of this listened to NOTHING, on the reasoning that adopting
        # whatever took the missing device's index is the bug indices caused. That is
        # true and it is not a reason to go deaf: listening to every port is not
        # adopting one impostor, it is the default this release exists to make safe.
        # Silence is never the right answer to "the keyboard you chose is not here".
        if not match:
            self.unresolved_pin = self.pinned
            return list(range(len(ports)))
        self.unresolved_pin = ""
        return match

    def _sync_locked(self, ports: list[str] | None = None) -> bool:
        """Make the set of open ports match what _wanted() says it should be.

        Opens before it shuts, and never shuts its way to zero. Windows MIDI inputs are
        exclusive, so pinning a port a DAW is holding fails -- and the first version of
        this shut first, which meant that failure closed the port that WAS working and
        left nothing open. The 2 s watcher then retried the same losing move forever.
        Deaf, one click away, in the release whose entire point is that "ports listed,
        no error, no sound" must never happen again.
        """
        ports = self.list_ports() if ports is None else ports
        self._names = ports
        wanted = set(self._wanted(ports))
        refused: list[str] = []

        for slot in sorted(wanted - set(self._open)):
            try:
                port = rtmidi.MidiIn()
                port.open_port(slot)
                # Drop clock / active sensing / sysex in the C layer so the Python
                # callback is never even entered for them.
                port.ignore_types(sysex=True, timing=True, active_sense=True)
                port.set_callback(_make_callback(self.engine, self.hub, self._seen,
                                                 self.counts, ports[slot], slot))
            except Exception as exc:  # noqa: BLE001
                # One port refusing is normal, not fatal: an input another application
                # already holds throws here while every other port stays usable.
                refused.append(f"{ports[slot]} ({exc})")
                continue
            self._open[slot] = (port, ports[slot])

        stale = [s for s in self._open if s not in wanted or self._open[s][1] != ports[s]]
        # Hold the line rather than go silent: if dropping the stale ports would leave
        # nothing listening, the newcomers must have refused, and keeping a working
        # input the user did not ask for beats keeping none at all. last_error still
        # says the pin did not take.
        if set(stale) != set(self._open) or not wanted:
            for slot in stale:
                self._shut(slot)

        if not self._open:
            self.last_error = ("no MIDI inputs -- is the keyboard on and plugged into USB?"
                               if not ports else
                               "could not open any MIDI input: " + "; ".join(refused))
            return False
        if refused:
            self.last_error = ("still listening to "
                               + ", ".join(sorted(n for _, n in self._open.values()))
                               + " -- could not open " + "; ".join(refused))
            # A PIN that refused is a failure the caller has to report: the user asked
            # for that one input and did not get it. One port being unavailable while we
            # listen to everything is not a failure -- we are listening, which was the
            # whole request -- so it rides out on last_error as a note instead.
            return not self.pinned
        self.last_error = ""
        return True

    def _shut(self, slot: int) -> None:
        entry = self._open.pop(slot, None)
        if entry is None:
            return
        try:
            entry[0].cancel_callback()
            entry[0].close_port()
        except Exception:  # noqa: BLE001 -- shutdown must not raise
            pass

    def close(self) -> None:
        with self._lock:
            for slot in list(self._open):
                self._shut(slot)

    @property
    def connected(self) -> bool:
        return bool(self._open)

    # --------------------------------------------------------------- hotplug
    def start_watcher(self) -> None:
        """Reopen ports when a keyboard comes back after an unplug.

        Polling, because WinMM has no device-arrival notification we can reach from
        python-rtmidi. Two seconds is imperceptible for a plug event and costs nothing.
        """
        if self._watcher is not None:
            return
        self._watch_stop.clear()
        self._watcher = threading.Thread(target=self._watch, name="midi-hotplug", daemon=True)
        self._watcher.start()

    def _watch(self) -> None:
        while not self._watch_stop.wait(WATCH_INTERVAL):
            try:
                ports = self.list_ports()
                with self._lock:
                    # Names, not indices. The list shifting under us -- a device
                    # unplugged, or something added ahead of it -- renumbers every port
                    # after the change, and _sync_locked compares each open slot against
                    # the name it was opened with, so a shift closes and reopens the
                    # right things rather than leaving a callback bound to a device the
                    # user did not touch.
                    if ports != self._names:
                        # Held notes belong to the keyboard that is going away.
                        self.engine.panic()
                    self._sync_locked(ports)
            except Exception as exc:  # noqa: BLE001 -- a watcher must never die
                self.last_error = str(exc)

    def stop_watcher(self) -> None:
        self._watch_stop.set()
        if self._watcher is not None:
            self._watcher.join(timeout=WATCH_INTERVAL + 0.5)
            self._watcher = None

    # ---------------------------------------------------------------- status
    def status(self) -> dict[str, Any]:
        ports = self.list_ports()
        open_names = {v[1] for v in self._open.values()}
        return {
            "connected": self.connected,
            # What the UI needs to answer "is my keyboard even talking?" without a
            # terminal: every port, whether we are listening to it, and how many
            # messages it has actually delivered. A port sitting at 0 while you play is
            # the whole diagnosis.
            "ports": [
                {
                    "index": i,
                    "name": n,
                    "listening": n in open_names,
                    "messages": self.counts.get(n, 0),
                }
                for i, n in enumerate(ports)
            ],
            "pinned": self.pinned,
            "unresolved_pin": self.unresolved_pin,
            "listening_to_all": not self.pinned or bool(self.unresolved_pin),
            "messages": sum(self.counts.values()),
            "error": self.last_error,
        }
