"""Jam's backing band: drums, bass and a comping keyboard, playing the chords you solo over.

The frontend composes the progression (frontend/harmony.js) and hands it here a bar at a
time, ahead of when it is heard. This plays it as a groove, on the same FluidSynth
sequencer clock the metronome uses -- so it cannot drift against the audio, and a Python
hiccup costs nothing as long as it is shorter than the lookahead.

**Bars, not seconds.** The chart is a list indexed by absolute bar number. The frontend
can replace everything from a bar onward (a new progression, a new key) but never what
is already queued: scheduled events can only be cancelled wholesale, so a bar inside the
lookahead is played as written. replace() says which bar its change starts on, and the
frontend lines its own copy up to that. A chart that runs dry repeats its last bar
rather than stopping the band.

**Where we are.** position() is the bar, with its fraction, that you are HEARING, from
the sequencer tick now. It goes out on the status heartbeat and the page interpolates
between frames, which is plenty for lighting keys: nobody sees 10 ms.

**A seat for you.** Bass and comp can each be switched off, like the drums, so you can
sit in as the band's bass player or its keyboard player. Like every other change, a
part switched on or off lands on the first bar that is not queued yet.

**Its own channels.** Three of 1-8, picked fresh at start from the ones no zone uses
(0-9 zones, 10-14 loop station, 15 metronome). Band notes go from the sequencer straight
to the synth, so they never reach the held-key list, the keyboard lamps or your stats.
"""

from __future__ import annotations

import random
import threading
from typing import Any

from . import config
from .engine import DRUM_BANK
from .metronome import LEAD_IN_MS, WAKE_TIMEOUT, _remove_events

SPARE_CHANNELS = range(1, 9)
STEPS = 16                       # sixteenths in a 4/4 bar
LOOKAHEAD_MS = 600.0
# The dead man's switch. The page keeps the chart topped up well ahead; a chart this many
# bars past its end means the page has gone (closed, reloaded, crashed), so the band stops
# instead of vamping on its last chord forever.
DRY_BARS = 8

# GM drum keys.
KICK, STICK, SNARE, HAT, OPEN_HAT, CRASH, RIDE = 36, 37, 38, 42, 46, 49, 51

# A style is a bar of sixteenths. Drum hits are (step, key, velocity). Bass and comp
# are (step, length-in-steps, what): bass plays "root", "fifth", "octave" or "approach"
# (a step toward the next bar's root); comp plays "chord", or "arp:N" for the Nth
# chord tone counting up from the bottom of the voicing.
STYLES: dict[str, dict[str, Any]] = {
    "ballad": {
        "label": "Ballad", "bpm": 72, "swing": 0.0,
        "comp_program": 0, "bass_program": 32, "kit": 0,
        "drums": [(0, KICK, 70), (4, STICK, 58), (8, KICK, 56), (12, STICK, 62)]
        + [(s, HAT, 34 if s % 4 else 44) for s in range(0, 16, 4)],
        "bass": [(0, 7, "root"), (8, 6, "fifth"), (14, 2, "approach")],
        "comp": [(0, 2, "arp:0"), (2, 2, "arp:1"), (4, 2, "arp:2"), (6, 2, "arp:3"),
                 (8, 2, "arp:2"), (10, 2, "arp:1"), (12, 4, "arp:2")],
    },
    "pop": {
        "label": "Pop", "bpm": 96, "swing": 0.0,
        "comp_program": 0, "bass_program": 33, "kit": 0,
        "drums": [(0, KICK, 96), (8, KICK, 88), (10, KICK, 70), (4, SNARE, 92), (12, SNARE, 96)]
        + [(s, HAT, 64 if s % 4 == 0 else 44) for s in range(0, 16, 2)],
        "bass": [(0, 5, "root"), (6, 2, "root"), (8, 4, "fifth"), (12, 2, "octave"),
                 (14, 2, "approach")],
        "comp": [(0, 5, "chord"), (6, 2, "chord"), (8, 6, "chord")],
    },
    "lofi": {
        "label": "Lo-fi", "bpm": 80, "swing": 0.55,
        "comp_program": 4, "bass_program": 33, "kit": 0,
        "drums": [(0, KICK, 88), (7, KICK, 60), (10, KICK, 76), (4, SNARE, 74), (12, SNARE, 80)]
        + [(s, HAT, 52 if s % 4 == 0 else 34) for s in range(0, 16, 2)],
        "bass": [(0, 6, "root"), (7, 2, "fifth"), (10, 5, "root"), (15, 1, "approach")],
        "comp": [(0, 7, "chord"), (10, 5, "chord")],
    },
    "rock": {
        "label": "Rock", "bpm": 112, "swing": 0.0,
        "comp_program": 29, "bass_program": 34, "kit": 0,
        "drums": [(0, KICK, 104), (6, KICK, 84), (8, KICK, 100), (4, SNARE, 108), (12, SNARE, 110)]
        + [(s, HAT, 76 if s % 4 == 0 else 58) for s in range(0, 16, 2)],
        "bass": [(s, 2, "root") for s in range(0, 14, 2)] + [(14, 2, "approach")],
        "comp": [(0, 3, "power"), (4, 3, "power"), (8, 3, "power"), (12, 3, "power")],
    },
}

BASS_RANGE = (36, 50)            # C2..D3
COMP_LOW = 52                    # E3: the comp lives in E3..D#4, under the melody


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def voice_comp(pcs: list[int], root: int) -> list[int]:
    """Every chord tone once, inside the one octave from COMP_LOW up. Whatever inversion
    that makes, consecutive chords stay in the same window, which is most of what voice
    leading sounds like -- and the window sits under where a melody goes."""
    return sorted(COMP_LOW + ((pc - COMP_LOW) % 12) for pc in set(pcs)) or [COMP_LOW + root % 12]


def bass_note(pc: int) -> int:
    lo, hi = BASS_RANGE
    n = lo + ((pc - lo) % 12)
    return n if n <= hi else n - 12


class Band:
    def __init__(self, engine: Any) -> None:
        self.engine = engine
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._client: int | None = None
        self._running = False
        self._rng = random.Random()

        self.style = "pop"
        self.bpm = 96.0
        self.volume = 0.7
        self.drums_on = True
        self.bass_on = True
        self.comp_on = True
        self._channels: dict[str, int] = {}

        # bar index -> {"root": pc, "pcs": [pc...], "bass": pc}
        self._chart: list[dict[str, Any]] = []
        self._next_bar = 0                 # first bar not yet scheduled
        self._anchor_tick = 0.0            # tick where bar `_anchor_bar` starts
        self._anchor_bar = 0
        # The tempo before the last change, (anchor bar, anchor tick, bar ms). A change
        # lands on the first bar not yet queued, so until the new anchor is reached
        # you are still hearing bars laid out at the old tempo.
        self._before: tuple[int, float, float] | None = None

    # ----------------------------------------------------------------- timing
    def bar_ms(self) -> float:
        return 4 * 60000.0 / self.bpm

    def _bar_tick(self, bar: int) -> float:
        return self._anchor_tick + (bar - self._anchor_bar) * self.bar_ms()

    def position(self) -> float | None:
        """The bar you are hearing now, with its fraction. None when stopped or before
        the first downbeat."""
        seq = self.engine.sequencer
        if not self._running or seq is None:
            return None
        tick = seq.get_tick()
        if self._before is not None and tick < self._anchor_tick:
            bar, at, ms = self._before
        else:
            bar, at, ms = self._anchor_bar, self._anchor_tick, self.bar_ms()
        pos = bar + (tick - at) / ms
        return pos if pos >= 0 else None

    # ---------------------------------------------------------------- control
    def start(self, chart: list[dict[str, Any]], style: str = "pop", bpm: float | None = None,
              volume: float = 0.7, drums: bool = True, bass: bool = True,
              comp: bool = True) -> dict[str, Any]:
        seq = self.engine.sequencer
        if self.engine.fs is None or seq is None:
            raise RuntimeError("engine not started")
        with self._lock:
            if self._running:
                self._flush()
            self.style = style if style in STYLES else "pop"
            self.bpm = _clamp(float(bpm or STYLES[self.style]["bpm"]), 40, 200)
            self.volume = _clamp(float(volume), 0.0, 1.0)
            self.drums_on = bool(drums)
            self.bass_on = bool(bass)
            self.comp_on = bool(comp)
            self._chart = [self._clean(c) for c in chart][:256]
            self._prepare()
            if self._client is None:
                self._client = seq.register_client("band", self._on_tick)
            self._anchor_bar = 0
            self._anchor_tick = seq.get_tick() + LEAD_IN_MS + 80
            self._before = None
            self._next_bar = 0
            self._running = True
            self._stop.clear()
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name="band", daemon=True)
                self._thread.start()
        self._wake.set()
        return self.status()

    def replace(self, from_bar: int, chart: list[dict[str, Any]]) -> int:
        """Rewrite the chart from `from_bar` on. Bars already queued are kept, so the
        change lands on the first bar that is not; that bar is returned."""
        with self._lock:
            at = max(int(from_bar), self._next_bar)
            if not self._chart:
                at = max(at, 0)
            last = self._chart[-1] if self._chart else None
            while len(self._chart) < at:            # pad a gap with the last chord
                self._chart.append(last or self._clean({}))
            self._chart[at:] = [self._clean(c) for c in chart][:256]
            return at

    def configure(self, bpm: float | None = None, volume: float | None = None,
                  style: str | None = None, drums: bool | None = None,
                  bass: bool | None = None, comp: bool | None = None) -> dict[str, Any]:
        """Takes effect from the next bar that is not queued yet. A tempo change re-anchors
        the timeline there, so position() never jumps."""
        with self._lock:
            if style in STYLES and style != self.style:
                self.style = style
                if bpm is None:
                    bpm = STYLES[style]["bpm"]
            if bpm is not None:
                new = _clamp(float(bpm), 40, 200)
                if self._running and new != self.bpm:
                    tick = self._bar_tick(self._next_bar)
                    self._before = (self._anchor_bar, self._anchor_tick, self.bar_ms())
                    self._anchor_bar, self._anchor_tick = self._next_bar, tick
                self.bpm = new
            if volume is not None:
                self.volume = _clamp(float(volume), 0.0, 1.0)
            if drums is not None:
                self.drums_on = bool(drums)
            if bass is not None:
                self.bass_on = bool(bass)
            if comp is not None:
                self.comp_on = bool(comp)
            if self._running:
                self._prepare()
        self._wake.set()
        return self.status()

    def stop(self) -> bool:
        """Silence the band. Returns whether it was playing."""
        with self._lock:
            was = self._running
            self._running = False
            self._flush()
        fs = self.engine.fs
        if fs is not None and was:
            for ch in self._channels.values():
                fs.cc(ch, 123, 0)
                fs.cc(ch, 120, 0)
        return was

    def shutdown(self) -> None:
        self.stop()
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    def status(self) -> dict[str, Any]:
        pos = self.position()
        return {
            "running": self._running,
            "style": self.style,
            "bpm": self.bpm,
            "volume": self.volume,
            "drums": self.drums_on,
            "bass": self.bass_on,
            "comp": self.comp_on,
            "pos": pos,
            "bar_ms": self.bar_ms(),
            "queued_to": self._next_bar,
            "chart_len": len(self._chart),
        }

    # ------------------------------------------------------------- internals
    @staticmethod
    def _clean(c: dict[str, Any]) -> dict[str, Any]:
        pcs = [int(p) % 12 for p in (c.get("pcs") or [0, 4, 7])][:6]
        root = int(c.get("root", pcs[0])) % 12
        bass = int(c.get("bass", root)) % 12
        return {"root": root, "pcs": pcs, "bass": bass}

    def _prepare(self) -> None:
        fs = self.engine.fs
        if fs is None:
            return
        busy = set(self.engine.active_channels)
        free = [c for c in SPARE_CHANNELS if c not in busy] or list(SPARE_CHANNELS)
        while len(free) < 3:
            free.append(free[-1])
        self._channels = {"drums": free[0], "bass": free[1], "comp": free[2]}
        sfid = self.engine.load_soundfont(config.DEFAULT_SOUNDFONT)
        st = STYLES[self.style]
        if sfid != -1:
            if fs.program_select(self._channels["drums"], sfid, DRUM_BANK, st["kit"]) == -1:
                fs.program_select(self._channels["drums"], sfid, DRUM_BANK, 0)
            fs.program_select(self._channels["bass"], sfid, 0, st["bass_program"])
            fs.program_select(self._channels["comp"], sfid, 0, st["comp_program"])
        vol = int(round(self.volume * 127))
        mix = {"drums": 0.85, "bass": 0.95, "comp": 0.7}
        for part, ch in self._channels.items():
            fs.cc(ch, 7, int(vol * mix[part]))
            fs.cc(ch, 91, 30 if part == "comp" else 18)

    def _flush(self) -> None:
        seq = self.engine.sequencer
        if seq is not None and _remove_events is not None and self._client is not None:
            _remove_events(seq.sequencer, self._client, -1, -1)

    def _on_tick(self, tick, event, seq, data) -> None:  # noqa: ANN001
        # AUDIO THREAD: ring the doorbell, nothing else.
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self._running:
                try:
                    with self._lock:
                        self._fill()
                except Exception:  # noqa: BLE001 -- the band must never take the app down
                    pass
            self._wake.wait(timeout=WAKE_TIMEOUT)
            self._wake.clear()

    def _chord(self, bar: int) -> dict[str, Any]:
        if not self._chart:
            return self._clean({})
        return self._chart[min(bar, len(self._chart) - 1)]

    def _fill(self) -> None:
        seq = self.engine.sequencer
        if seq is None or not self._running:
            return
        now = seq.get_tick()
        # A hitch long enough to put the next bar in the past: skip ahead rather than
        # spray a burst of catch-up notes.
        while self._bar_tick(self._next_bar) < now - 50:
            self._next_bar += 1
        while self._bar_tick(self._next_bar) < now + LOOKAHEAD_MS:
            if self._next_bar >= len(self._chart) + DRY_BARS:
                self._running = False
                return
            self._schedule_bar(self._next_bar)
            self._next_bar += 1

    def _schedule_bar(self, bar: int) -> None:
        seq = self.engine.sequencer
        dest = self.engine.seq_dest
        src = self._client if self._client is not None else -1
        st = STYLES[self.style]
        chord, nxt = self._chord(bar), self._chord(bar + 1)
        t0 = self._bar_tick(bar)
        sixteenth = self.bar_ms() / STEPS
        rnd = self._rng

        def at(step: int) -> int:
            swing = st["swing"] * sixteenth * 0.66 if step % 4 == 2 else 0.0
            return int(round(t0 + step * sixteenth + swing))

        def vel(v: int) -> int:
            return int(_clamp(v + rnd.randint(-6, 6), 1, 127))

        def note(ch: int, key: int, v: int, step: int, steps: float) -> None:
            dur = max(30, int(steps * sixteenth) - 12)
            seq.note(at(step), ch, key, vel(v), dur, source=src, dest=dest)

        if self.drums_on:
            ch = self._channels["drums"]
            hits = list(st["drums"])
            if bar % 8 == 0:
                hits.append((0, CRASH, 84))
            if bar % 4 == 3 and self.style in ("pop", "rock"):     # a small fill into the next phrase
                hits = [h for h in hits if h[0] < 12] + [(12, SNARE, 70), (13, SNARE, 78),
                                                          (14, SNARE, 88), (15, SNARE, 98)]
            for step, key, v in hits:
                note(ch, key, v, step, 1)

        if self.bass_on:
            ch = self._channels["bass"]
            root = bass_note(chord["bass"])
            for step, steps, what in st["bass"]:
                if what == "root":
                    key = root
                elif what == "fifth":
                    five = (chord["root"] + 7) % 12
                    key = root + ((five - root) % 12) if (five in chord["pcs"]) else root
                elif what == "octave":
                    key = root + 12
                else:                                   # approach the next bar's root
                    target = bass_note(nxt["bass"])
                    if target == root:
                        key = root + 7 if root + 7 <= BASS_RANGE[1] + 5 else root - 5
                    else:
                        key = target - 1 if target > root else target + 2
                note(ch, key, 92, step, steps)

        if self.comp_on:
            ch = self._channels["comp"]
            voicing = voice_comp(chord["pcs"], chord["root"])
            for step, steps, what in st["comp"]:
                if what == "chord":
                    for i, key in enumerate(voicing):
                        note(ch, key, 66 if i else 72, step, steps)
                elif what == "power":
                    r = COMP_LOW - 12 + ((chord["root"] - COMP_LOW) % 12)   # an octave down: power chords are low
                    for key in (r, r + 7, r + 12):
                        note(ch, key, 78, step, steps)
                else:
                    i = int(what.split(":")[1])
                    ladder = voicing + [n + 12 for n in voicing]
                    note(ch, ladder[min(i, len(ladder) - 1)], 62, step, steps)

        # One callback per bar keeps the scheduler fed even if a doorbell is missed.
        if self._client is not None:
            seq.timer(int(t0), dest=self._client)
