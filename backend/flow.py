"""Chord Flow's backing pad: the chord you are meant to be playing over, held quietly.

The frontend composes the progression (frontend/harmony.js) and sends each chord
here as it starts. This holds it until the next one arrives -- an empty list means
silence -- so the pad is always exactly the chord on screen, however long you take.

**Held, not scheduled.** /api/preview schedules its note-offs on the sequencer, which
is right for an audition and wrong here. In "wait for me" a chord lasts as long as you
take, and a scheduled note-off from the PREVIOUS chord fires into the next one: C to
Am keeps C and E, so the old C's note-off cuts the new C off mid-chord. Releasing the
old chord ourselves, at the moment the new one starts, has no such case.

**Its own channel.** Not the one you play on: a pad note and your note on the same key
of the same channel are one voice to FluidSynth, and either note-off would end both.
0-9 belong to zones, 10-14 to the loop station, 15 to the metronome -- so it takes the
first of 1-8 no zone is using, asked fresh each time because zones change.

**A dead man's switch.** If the page goes away mid-chord (a reload, a crash) nothing
would ever send the empty list, and a pad holds forever. So every chord carries a
deadline, renewed by the next call; past it the pad lets go by itself. A Python timer
is the wrong tool for musical timing and the right one here -- this is not a note
ending on a beat, it is a safety net, and nobody hears its jitter.
"""

from __future__ import annotations

import threading
from typing import Any

from . import config

# GM programs in the shipped SoundFont, bank 0. Sounds that sit under a melody rather
# than compete with it.
SOUNDS = {"pad": 89, "strings": 49, "epiano": 4, "choir": 52}
SPARE_CHANNELS = range(1, 9)
DEFAULT_HOLD_S = 30.0


class FlowBacking:
    def __init__(self, engine: Any) -> None:
        self.engine = engine
        self._lock = threading.Lock()
        self._channel: int | None = None
        self._notes: list[int] = []
        self._program: int | None = None
        self._gen = 0
        self._timer: threading.Timer | None = None

    def _pick_channel(self) -> int:
        busy = set(self.engine.active_channels)
        return next((c for c in SPARE_CHANNELS if c not in busy), SPARE_CHANNELS[-1])

    def _release(self) -> None:
        fs = self.engine.fs
        if fs is not None and self._channel is not None:
            for n in self._notes:
                fs.noteoff(self._channel, n)
        self._notes = []

    def play(self, notes: list[int], sound: str = "pad", volume: float = 0.6,
             velocity: int = 60, hold_s: float = DEFAULT_HOLD_S) -> dict[str, Any]:
        """Replace whatever is sounding with `notes`. The same notes again only renew
        the deadline -- re-striking a held pad every refresh would pulse. A new sound
        does re-strike, or picking one mid-chord would do nothing until the chord
        changed -- which in "wait for me" may be never."""
        fs = self.engine.fs
        if fs is None:
            raise RuntimeError("engine not started")
        want = sorted({n for n in notes if 0 <= n <= 127})[:12]
        program = SOUNDS.get(sound, SOUNDS["pad"])
        with self._lock:
            self._gen += 1
            gen = self._gen
            if want != self._notes or program != self._program or not want:
                self._release()
                if want:
                    self._channel = self._pick_channel()
                    sfid = self.engine.load_soundfont(config.DEFAULT_SOUNDFONT)
                    if sfid != -1:
                        fs.program_select(self._channel, sfid, 0, program)
                    self._program = program
                    for n in want:
                        fs.noteon(self._channel, n, max(1, min(127, int(velocity))))
                    self._notes = want
            if self._channel is not None:
                fs.cc(self._channel, 7, int(round(max(0.0, min(1.0, volume)) * 127)))
            if self._timer is not None:
                self._timer.cancel()
            self._timer = None
            if self._notes:
                self._timer = threading.Timer(max(1.0, min(120.0, hold_s)),
                                              self._expire, args=(gen,))
                self._timer.daemon = True
                self._timer.start()
            return {"channel": self._channel, "notes": list(self._notes)}

    def _expire(self, gen: int) -> None:
        with self._lock:
            if gen == self._gen:          # nothing newer has arrived since
                self._release()

    def stop(self) -> bool:
        """Silence the pad. Returns whether it was sounding."""
        with self._lock:
            was = bool(self._notes)
            self._gen += 1
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._release()
            return was
