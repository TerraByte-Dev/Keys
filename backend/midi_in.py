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


def _make_callback(engine: Engine, hub: Hub) -> Callable:
    """Build the hot callback with every lookup it needs bound as a local."""
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
        # HOT PATH. No print, no logging, no await, no dict, no lock, no I/O.
        msg, _delta = event
        status = msg[0]
        if status >= 0xF8:          # clock, active sensing, reset -- 98.5% of traffic
            return
        t0 = perf()
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
    """Owns the rtmidi port, and reopens it when the piano is unplugged and back."""

    def __init__(self, engine: Engine, hub: Hub) -> None:
        self.engine = engine
        self.hub = hub
        self._callback = _make_callback(engine, hub)
        self._in: rtmidi.MidiIn | None = None
        self._probe = rtmidi.MidiIn()
        self._lock = threading.Lock()
        self.port_index: int | None = None
        self.port_name: str = ""
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
        with self._lock:
            return self._open_locked(index)

    def _open_locked(self, index: int | None) -> bool:
        self._close_locked()
        ports = self.list_ports()
        if not ports:
            self.last_error = "no MIDI inputs -- is the piano on and plugged into USB TO HOST?"
            return False
        idx = 0 if index is None else int(index)
        if idx >= len(ports):
            self.last_error = f"no MIDI port {idx} (found {len(ports)})"
            return False
        try:
            midi_in = rtmidi.MidiIn()
            midi_in.open_port(idx)
            # Drop clock / active sensing / sysex in the C layer so the Python
            # callback is never even entered for them.
            midi_in.ignore_types(sysex=True, timing=True, active_sense=True)
            midi_in.set_callback(self._callback)
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"could not open port {idx}: {exc}"
            return False
        self._in = midi_in
        self.port_index = idx
        self.port_name = ports[idx]
        self.last_error = ""
        return True

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    def _close_locked(self) -> None:
        if self._in is not None:
            try:
                self._in.cancel_callback()
                self._in.close_port()
            except Exception:  # noqa: BLE001 -- shutdown must not raise
                pass
            self._in = None
        self.port_index = None
        self.port_name = ""

    @property
    def connected(self) -> bool:
        return self._in is not None

    # --------------------------------------------------------------- hotplug
    def start_watcher(self) -> None:
        """Reopen the port when the piano comes back after an unplug.

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
                    if self._in is None:
                        if ports:
                            self._open_locked(None)
                    elif self.port_index is None or self.port_index >= len(ports) or \
                            ports[self.port_index] != self.port_name:
                        # The port list shifted under us -- the piano was unplugged,
                        # or something else was added ahead of it. Rebind by name.
                        self.engine.panic()
                        if self.port_name in ports:
                            self._open_locked(ports.index(self.port_name))
                        else:
                            self._close_locked()
            except Exception as exc:  # noqa: BLE001 -- a watcher must never die
                self.last_error = str(exc)

    def stop_watcher(self) -> None:
        self._watch_stop.set()
        if self._watcher is not None:
            self._watcher.join(timeout=WATCH_INTERVAL + 0.5)
            self._watcher = None

    # ---------------------------------------------------------------- status
    def status(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "port_index": self.port_index,
            "port_name": self.port_name,
            "ports": self.list_ports(),
            "error": self.last_error,
        }
