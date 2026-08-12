"""FastAPI + WebSocket. Everything the browser talks to.

The split that matters: **control goes over REST, the realtime feed goes over the
websocket, and nothing goes over the MIDI callback thread.** Loading a preset can
afford a round trip and a database write. Showing which key is down cannot.

The drain loop is the heart of this file. Sixty times a second it empties the hub's
deque, updates the authoritative held-note set, runs chord detection and the practice
clock, and pushes one small JSON frame per changed state. If a frame is slow, the
deque drops its oldest events and the next 1 Hz status frame -- which carries the
engine's own held-note list -- puts the UI back in sync. Audio is never involved: it
is rendered in FluidSynth's C thread and never passes through Python.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections import deque
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from . import config, engine as engine_mod, music, theory, timing
from .backing import Backing
from .engine import Engine, Preset, Zone
from .exercises import GenContext, clean_params, load_all
from .exercises.metrics import grade
from .exercises.run import Run
from .hub import BEND, CONTROL, NOTE_OFF, NOTE_ON, Hub
from .looper import LoopStation
from .metronome import Metronome
from .midi_in import MidiInput
from .practice import PracticeClock
from .score import ScoreError
from .scoreplay import ScorePlayer
from .scores import Library
from .sightread import SightReader
from .store import Store
from .updater import UpdateBusy, UpdateError, Updater
from .version import VERSION, check as check_update

FRAME_HZ = 60
STATUS_HZ = 1
SUSTAIN_CC = 64

# How long a chord has to hold still before it counts as played. Rolling into a voicing
# one finger at a time momentarily reads as C, then C5, then Cmaj7 -- logging each of
# those would make the chord analytics mostly noise about how fast you place fingers.
CHORD_SETTLE_SECONDS = 0.14


def _range_state() -> dict[str, Any]:
    """The instrument, as the frontend needs it.

    `low`/`high` are the keys that exist and `octave` is where they are sounding; the
    limits ride along so the settings panel does not have to keep its own copy of
    numbers the backend already enforces.
    """
    low, high = config.instrument_range()
    return {
        "low": low,
        "high": high,
        "keys": high - low + 1,
        "octave": config.master_octave(),
        "min_keys": config.MIN_KEYS,
        "max_octave": config.MAX_OCTAVE,
    }


class App:
    """Everything with a lifetime. One instance, created at startup."""

    def __init__(self) -> None:
        self.settings = config.settings
        self.engine = Engine(self.settings)
        self.hub = Hub()
        self.midi = MidiInput(self.engine, self.hub)
        self.metro = Metronome(self.engine, self.settings)
        self.loop = LoopStation(self.engine, self.metro, self.settings)
        self.backing = Backing(self.settings)
        self.scores = Library()
        self.player = ScorePlayer(self.engine)
        self.store = Store(config.DB_PATH)
        self.updater = Updater()
        self.practice = PracticeClock(self.store, self.settings)
        self.sight = SightReader(self.store, self.settings)
        # The exercise engine. Rebound in one atomic assignment when a run starts, so
        # the drain loop sees the old run or the new one and never a half-built one --
        # the same argument the zone routing table relies on, and the reason there is
        # no lock here despite FastAPI running plain `def` endpoints in a threadpool.
        self.run: Run | None = None
        self.last_exercise_fb: dict[str, Any] | None = None
        self.rng = random.Random()
        self.presets: dict[str, Preset] = {}

        self.clients: set[WebSocket] = set()
        self.held: set[int] = set()
        self.sustain = False
        self.chord: dict[str, Any] | None = None
        self.click_offsets: deque[float] = deque(maxlen=512)
        self.last_feedback: dict[str, Any] | None = None
        self._task: asyncio.Task | None = None
        self._boot_errors: list[str] = []
        # Chord settle tracking -- see CHORD_SETTLE_SECONDS.
        self._chord_candidate: str | None = None
        self._chord_since = 0.0
        self._chord_logged: str | None = None

    # ------------------------------------------------------------- lifecycle
    def startup(self) -> None:
        # First, and before the engine: an update that failed or was interrupted left
        # up to 143 MB beside the application directory, and this start is the earliest
        # moment anything is allowed to delete it. No-op in a source checkout.
        self.updater.sweep()
        self.presets = engine_mod.load_presets()
        try:
            self.engine.start()
        except Exception as exc:  # noqa: BLE001
            self._boot_errors.append(f"audio engine failed to start: {exc}")
            return
        wanted = str(self.settings.get("preset", default="grand-piano"))
        preset = self.presets.get(wanted) or next(iter(self.presets.values()), None)
        if preset is not None:
            self.engine.set_zones(preset.zones, preset.id, preset.name)
            self.practice.preset = preset.id
        else:
            self._boot_errors.append("no presets found in presets/")
            self.engine.set_zones([Zone()], "default", "Acoustic Grand")

        self.engine.load_pedal()

        port = self.settings.get("midi_port")
        if not self.midi.open(port):
            self._boot_errors.append(self.midi.last_error)
        self.midi.start_watcher()

    def shutdown(self) -> None:
        # Order is load-bearing: stop MIDI before deleting the synth, or a note that
        # arrives mid-teardown calls into a freed Synth from the callback thread.
        self.midi.stop_watcher()
        self.midi.close()
        self.loop.shutdown()
        self.metro.shutdown()
        self.practice.end_session()
        self.engine.stop()
        self.store.close()

    def reading_key(self) -> str:
        """The key notes are spelled in. Validated, because music.py rejects a bad key
        by raising and this is read on every frame -- a hand-edited config.local.json
        must not turn into an exception sixty times a second."""
        key = str(self.settings.get("ui", "key_signature", default="C") or "C")
        return key if key in music.KEYS else "C"

    # ---------------------------------------------------------------- feeds
    async def broadcast(self, payload: dict[str, Any]) -> None:
        if not self.clients:
            return
        text = json.dumps(payload, separators=(",", ":"), default=float)
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_text(text)
            except Exception:  # noqa: BLE001 -- a dead socket is not an error here
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    async def drain_loop(self) -> None:
        interval = 1.0 / FRAME_HZ
        last_status = 0.0
        key = self.reading_key()
        while True:
            try:
                await asyncio.sleep(interval)
                now = time.perf_counter()
                events = self.hub.drain()

                on: list[list[int]] = []
                off: list[int] = []
                ccs: list[list[int]] = []
                held_changed = False

                for t, kind, a, b, _service in events:
                    # The loop station records from here rather than from the MIDI
                    # callback: `t` was stamped at callback entry, so a take is timed
                    # exactly as well as it would have been in the hot path, and the
                    # hot path pays nothing for it. Returns instantly when stopped.
                    self.loop.on_event(t, kind, a, b)
                    if kind == NOTE_ON:
                        self.held.add(a)
                        on.append([a, b])
                        held_changed = True
                        self.practice.on_note(t, a, b)
                        # Computed once and passed down. click_offset_ms() runs a least
                        # squares fit over 400 observations and scans up to 4096 queued
                        # clicks; calling it again inside the grader would double that
                        # for every note.
                        offset = self.metro.click_offset_ms(t)
                        if offset is not None:
                            self.click_offsets.append(offset)
                        if self.run is not None and not self.run.done:
                            fb = self.run.on_note(a, b, t, offset)
                            if fb is not None:
                                self.last_exercise_fb = fb
                        fb = self.sight.on_note(a, b, t)
                        if fb is not None:
                            self.last_feedback = fb
                    elif kind == NOTE_OFF:
                        self.held.discard(a)
                        off.append(a)
                        held_changed = True
                    elif kind == CONTROL:
                        ccs.append([a, b])
                        if a == SUSTAIN_CC:
                            self.sustain = b >= 64
                    elif kind == BEND:
                        ccs.append([-1, a])

                # Pedal-held notes whose decay ran out. Off the hot path on purpose --
                # see Engine.decay_tick. Their keys are already up, so this only tells
                # the UI to stop drawing them as ringing.
                faded = self.engine.decay_tick(now)
                if faded:
                    off.extend(faded)

                if held_changed:
                    notes = sorted(self.held)
                    self.chord = music.detect_chord(notes, key) if len(notes) >= 3 else None
                self._settle_chord(now)

                if on or off or ccs:
                    frame: dict[str, Any] = {"t": "f", "held": sorted(self.held)}
                    if on:
                        frame["on"] = on
                    if off:
                        frame["off"] = off
                    if ccs:
                        frame["cc"] = ccs
                    frame["sus"] = self.sustain
                    if held_changed:
                        frame["chord"] = self.chord
                        frame["names"] = [music.note_name(n, key) for n in sorted(self.held)]
                    if self.last_feedback is not None:
                        frame["sight"] = self.last_feedback
                        self.last_feedback = None
                    if self.last_exercise_fb is not None:
                        frame["ex"] = self.last_exercise_fb
                        self.last_exercise_fb = None
                    await self.broadcast(frame)

                self.practice.tick(now)

                if now - last_status >= 1.0 / STATUS_HZ:
                    last_status = now
                    key = self.reading_key()
                    await self.broadcast(self.status_frame(now))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 -- the loop must never die
                await self.broadcast({"t": "err", "where": "drain", "message": str(exc)})
                await asyncio.sleep(0.25)

    def _settle_chord(self, now: float) -> None:
        """Log a chord once it has held still, and only once per press.

        Resetting on release is what lets the same chord count again next time you play
        it -- without that, a two-chord vamp would log two chords for a whole session.
        """
        symbol = self.chord["symbol"] if self.chord else None
        if symbol != self._chord_candidate:
            self._chord_candidate = symbol
            self._chord_since = now
        if symbol is None:
            self._chord_logged = None
            return
        if symbol != self._chord_logged and (now - self._chord_since) >= CHORD_SETTLE_SECONDS:
            self._chord_logged = symbol
            self.practice.on_chord(now, self.chord, len(self.held))

    def status_frame(self, now: float | None = None) -> dict[str, Any]:
        now = time.perf_counter() if now is None else now
        # The engine's own held list is ground truth; a UI that missed a note-off
        # un-sticks itself here rather than staying wrong until the next keypress.
        truth = self.engine.held_notes()
        if set(truth) != self.held:
            self.held = set(truth)
        return {
            "t": "s",
            "held": truth,
            "sustain": self.sustain,
            # On the heartbeat rather than only in /api/state, for the same reason the
            # held list is: a second tab, or this one after the range was changed
            # somewhere else, corrects itself within a second instead of drawing keys
            # that are not there until someone reloads.
            "range": _range_state(),
            "engine": self.engine.status(),
            "midi": self.midi.status(),
            "metronome": self.metro.status(),
            "loop": self.loop.status(),
            "pedal": self.engine.pedal_status(),
            "transport": self.player.status(),
            "practice": self.practice.status(now),
            "sightread": {"active": self.sight.active, "index": self.sight.index},
            "exercise": (
                {"running": True, "exercise": self.run.plan.exercise,
                 "variant": self.run.plan.variant, "index": self.run.index,
                 "steps": len(self.run.plan.steps)}
                if self.run is not None and not self.run.done else {"running": False}
            ),
            "hub": self.hub.stats(),
            "timing": self.timing_snapshot(),
            "errors": self._boot_errors,
        }

    def silence(self) -> list[str]:
        """Stop everything that is making a sound. Returns what was actually stopped.

        On App rather than in the endpoint because it spans four subsystems and is the
        one operation that has to be right when everything else has gone wrong -- which
        means it has to be reachable from a check without an HTTP server in the way.
        """
        stopped: list[str] = []

        # The metronome first: it is the one that carries on when everything else has
        # been stopped, and it is the reason this exists.
        if self.metro.status().get("running"):
            self.metro.stop()
            stopped.append("metronome")
        # An exercise that borrowed the tempo has to give it back, or the next click you
        # start comes up at the exercise's bpm with nothing on screen explaining why.
        self.metro.release()

        # `state`, not a `running` key -- LoopStation.status() computes one internally
        # and does not publish it, and asking for the wrong key here would fail silently
        # and leave the loop going round, which is exactly the bug this is fixing.
        if self.loop.status().get("state") != "stopped":
            self.loop.stop()
            stopped.append("loop")

        if self.player.status().get("state") == "playing":
            self.player.stop()
            stopped.append("score")

        notes = len(self.held)
        self.engine.panic()
        self.held.clear()
        self.sustain = False
        if notes:
            stopped.append(f"{notes} note{'s' if notes != 1 else ''}")
        return stopped

    def timing_snapshot(self) -> dict[str, Any]:
        onsets = self.practice.onsets()[-96:]
        offsets = list(self.click_offsets)[-96:] if self.metro.status()["running"] else None
        return timing.analyze(onsets, offsets)

    def full_state(self) -> dict[str, Any]:
        return {
            "engine": self.engine.status(),
            "midi": self.midi.status(),
            "metronome": self.metro.status(),
            "loop": self.loop.status(),
            "pedal": self.engine.pedal_status(),
            "transport": self.player.status(),
            "saved_loops": self.loop.saved(),
            "practice": self.practice.status(),
            "sightread": self.sight.state(),
            "settings": self.settings.all(),
            "presets": [p.to_dict() for p in self.presets.values()],
            "soundfonts": self.engine.list_soundfonts(),
            "version": VERSION,
            "frozen": config.FROZEN,
            "curves": engine_mod.CURVE_NAMES,
            "keys": music.KEYS,
            "range": _range_state(),
            "hub": self.hub.stats(),
            "errors": self._boot_errors,
        }


app_state = App()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    app_state.startup()
    app_state._task = asyncio.create_task(app_state.drain_loop())  # noqa: SLF001
    try:
        yield
    finally:
        if app_state._task is not None:  # noqa: SLF001
            app_state._task.cancel()  # noqa: SLF001
            try:
                await app_state._task  # noqa: SLF001
            except asyncio.CancelledError:
                pass
        app_state.shutdown()


api = FastAPI(title="Keys", lifespan=lifespan)


# ------------------------------------------------------------------ realtime
@api.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    app_state.clients.add(ws)
    try:
        await ws.send_text(json.dumps({"t": "hello", **app_state.status_frame()}, default=float))
        while True:
            # The browser never needs to send anything -- control is REST. Reading
            # keeps the socket alive and gives us a clean disconnect.
            await ws.receive_text()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        app_state.clients.discard(ws)


# ---------------------------------------------------------------------- state
@api.get("/api/state")
def get_state() -> dict[str, Any]:
    return app_state.full_state()


@api.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": app_state.engine.started,
        "midi": app_state.midi.connected,
        "version": VERSION,
        "errors": app_state._boot_errors,  # noqa: SLF001
    }


# -------------------------------------------------------------------- presets
@api.get("/api/presets")
def list_presets() -> dict[str, Any]:
    return {
        "presets": [p.to_dict() for p in app_state.presets.values()],
        "current": app_state.engine.preset_id,
    }


@api.post("/api/presets/{pid}/load")
def load_preset(pid: str) -> dict[str, Any]:
    preset = app_state.presets.get(pid)
    if preset is None:
        raise HTTPException(404, f"no preset '{pid}'")
    # Loading a preset does not touch the reverb unit. Presets used to carry a room and
    # push it onto the Settings sliders, and the effect of that was that browsing the
    # shelf silently rewrote a reverb you had tuned. Settings is the only writer now.
    warnings = app_state.engine.set_zones(preset.zones, preset.id, preset.name)
    # Deliberately does NOT become the startup sound. Trying a split out of curiosity
    # used to pin it forever, so every launch afterwards came up with the keyboard cut
    # in half and nothing on screen explaining why. Startup is set on purpose, in
    # Settings; loading a preset is just loading a preset.
    app_state.practice.preset = pid
    return {"ok": True, "warnings": warnings, "engine": app_state.engine.status()}


@api.post("/api/presets/{pid}/startup")
def set_startup_preset(pid: str) -> dict[str, Any]:
    """Pin a preset as what Keys opens with."""
    if pid not in app_state.presets:
        raise HTTPException(404, f"no preset '{pid}'")
    app_state.settings.update({"preset": pid})
    return {"ok": True, "preset": pid}


@api.post("/api/presets/save")
def save_preset(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    pid = str(body.get("id") or "").strip().lower().replace(" ", "-")
    if not pid:
        raise HTTPException(400, "id is required")
    preset = Preset(
        id=pid,
        name=str(body.get("name") or pid.replace("-", " ").title()),
        description=str(body.get("description") or ""),
        zones=[Zone.from_dict(z) for z in body.get("zones", [])],
    )
    engine_mod.save_preset(preset)
    app_state.presets = engine_mod.load_presets()
    return {"ok": True, "preset": preset.to_dict()}


@api.delete("/api/presets/{pid}")
def delete_preset(pid: str) -> dict[str, Any]:
    # Only ever deletes your copy. A shipped preset lives inside the application
    # bundle, is reinstated by the next update anyway, and is not ours to remove.
    path = config.PRESET_DIR / f"{pid}.json"
    if not path.exists():
        if config.find_asset("presets", f"{pid}.json") is not None:
            raise HTTPException(400, f"'{pid}' ships with Keys and cannot be deleted")
        raise HTTPException(404, f"no preset '{pid}'")
    path.unlink()
    app_state.presets = engine_mod.load_presets()
    return {"ok": True}


# ---------------------------------------------------------------------- zones
@api.post("/api/zones")
def set_zones(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    zones = [Zone.from_dict(z) for z in body.get("zones", [])]
    if not zones:
        raise HTTPException(400, "at least one zone is required")
    # Empty id on purpose: hand-edited zones are not a saved preset, and the UI renders
    # that state rather than pretending one of the chips is still active.
    warnings = app_state.engine.set_zones(
        zones, body.get("id", ""), body.get("name", "") or "Custom")
    return {"ok": True, "warnings": warnings, "engine": app_state.engine.status()}


@api.get("/api/instruments")
def instruments(soundfont: str | None = None) -> dict[str, Any]:
    return {
        "soundfont": soundfont or config.DEFAULT_SOUNDFONT,
        "instruments": app_state.engine.list_presets(soundfont),
    }


@api.get("/api/soundfonts")
def soundfonts() -> dict[str, Any]:
    return {"soundfonts": app_state.engine.list_soundfonts()}


@api.get("/api/audio/devices")
def audio_devices() -> dict[str, Any]:
    audio = app_state.settings.get("audio", default=config.HARDWARE) or config.HARDWARE
    return {
        "devices": engine_mod.list_audio_devices(),
        "current": str(audio.get("device", "default") or "default"),
        "exclusive": bool(audio.get("exclusive", False)),
    }


@api.post("/api/audio")
def set_audio(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Reopen the audio stream on new settings.

    Rate, buffer, exclusive mode and device are all negotiated when the WASAPI stream
    opens, so every one of them needs the device closed and reopened. The metronome
    holds a client registration on the sequencer, which does not survive that -- so it
    is stopped first and started again afterwards if it was running.
    """
    patch = {k: v for k, v in body.items()
             if k in ("exclusive", "device", "period_size", "sample_rate", "polyphony", "gain")}
    if not patch:
        raise HTTPException(400, "nothing to change")
    if "sample_rate" in patch:
        patch["sample_rate"] = float(patch["sample_rate"])

    was_running = app_state.metro.status()["running"]
    app_state.metro.shutdown()
    app_state.metro = Metronome(app_state.engine, app_state.settings)

    warnings = app_state.engine.restart(patch)
    app_state.held.clear()
    app_state.hub.drain()
    if was_running and app_state.engine.started:
        app_state.metro.start()

    status = app_state.engine.status()
    if not app_state.engine.started:
        warnings.append(
            "the audio device refused those settings and the engine is now stopped -- "
            "try exclusive mode off, or a different output device"
        )
    return {"ok": app_state.engine.started, "warnings": warnings, "engine": status}


# --------------------------------------------------------------------- sound
@api.post("/api/panic")
def panic() -> dict[str, Any]:
    """Silence. The button you press when you do not know what is wrong.

    It used to send all-notes-off and nothing else, which left the metronome clicking,
    a loop going round and a score still playing -- the three things most likely to be
    the noise you wanted stopped. "All notes off" is a MIDI operation; this is meant to
    be the answer to "make it stop", and those are not the same request.

    It STOPS things and destroys nothing. Every transport here can be started again
    from where you left it, the loop keeps its layers, and a take that was recording is
    kept rather than thrown away -- the same call LoopStation.stop() already makes,
    because a take you played is a take you played. Nothing here clears your work.

    Reports what it actually silenced, so the button can say so. Pressing it when the
    room is already quiet is a no-op that says nothing was running, which is itself
    worth knowing when you cannot tell where a sound is coming from.

    The work is App.silence(); this is the door onto it.
    """
    return {"ok": True, "stopped": app_state.silence()}


@api.post("/api/octave")
def set_octave(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Shift the whole instrument by whole octaves.

    Its own endpoint rather than a settings patch because it is a control you reach for
    mid-phrase -- the same case /api/fx/send is: one number, applied live, no zone id.
    Takes `octave` for an absolute value or `by` for a step, so the two dock buttons do
    not have to know what the current value is before they can change it.
    """
    # Total, like every other clamp on this surface: a control you reach for mid-phrase
    # must not be able to answer with a 500. Anything unreadable means "no change".
    try:
        want = (config.master_octave() + int(body["by"])) if "by" in body \
            else int(body.get("octave", config.master_octave()))
    except (TypeError, ValueError):
        want = config.master_octave()
    applied = app_state.engine.set_master_octave(want)
    return {"ok": True, "octave": applied, "range": _range_state()}


@api.post("/api/preview")
def preview(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Audition notes from the UI. The note-off is scheduled on the sequencer, not
    on a Python timer -- same rule as the metronome."""
    notes = [int(n) for n in body.get("notes", [])][:36]
    velocity = max(1, min(127, int(body.get("velocity", 90))))
    ms = max(50, min(4000, int(body.get("ms", 700))))
    # Zero plays them together, which is a chord; anything else spreads them into a
    # run, which is a scale. One endpoint, because the difference between hearing a
    # chord and hearing a scale is entirely when the notes start.
    stagger = max(0, min(600, int(body.get("stagger", 0))))
    eng = app_state.engine
    if eng.fs is None:
        raise HTTPException(503, "engine not started")
    channel = int(body.get("channel", eng.active_channels[0]))
    if eng.sequencer is not None:
        at = eng.sequencer.get_tick() + 5
        for i, n in enumerate(notes):
            eng.sequencer.note(at + i * stagger, channel, n, velocity, ms,
                               dest=eng.seq_dest)
    elif stagger:
        raise HTTPException(503, "a run needs the sequencer")
    else:
        for n in notes:
            eng.fs.noteon(channel, n, velocity)
    return {"ok": True, "notes": notes}


@api.post("/api/fx/send")
def fx_send(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """How much of the sound under your hands goes to the reverb and chorus units.

    Not the units themselves -- those are global and are set through /api/settings. This
    takes no zone id on purpose: it is the whole instrument you are playing, which is the
    thing a knob you reach for mid-phrase has to be. Layers still edits a single zone's
    send behind Apply, and this writes through to those same fields so the two agree.
    """
    if app_state.engine.fs is None:
        raise HTTPException(503, "engine not started")
    for kind in ("reverb", "chorus"):
        if kind in body:
            app_state.engine.set_send(kind, float(body[kind]))
    return {"ok": True, "engine": app_state.engine.status()}


# ----------------------------------------------------------------- your data
@api.get("/api/data")
def data_inventory() -> dict[str, Any]:
    return {"ok": True, **app_state.store.inventory()}


@api.post("/api/data/reset")
def data_reset(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Delete one category of your own data.

    `confirm` must repeat the category. It is not security -- anything that can
    reach this port can send both fields -- it is a guard against the UI firing
    this from a mis-wired click handler, which is the failure that actually
    happens and the one that cannot be undone.
    """
    what = str(body.get("what", ""))
    if str(body.get("confirm", "")) != what:
        raise HTTPException(400, "reset needs the category repeated in 'confirm'")
    try:
        result = app_state.store.wipe(what, app_state.settings)
    except ValueError as err:
        raise HTTPException(400, str(err)) from None
    # Resetting settings puts the master octave back to 0 -- but the shift is baked into
    # the routing table, so without this the table keeps the old one and every key goes
    # on sounding transposed while the whole UI truthfully reports 0. A silent shift
    # nobody can see the cause of is the worst version of this bug.
    if what in ("settings", "everything"):
        app_state.engine.apply_instrument()
    # printed, not logged: this module has no logger, and keys.py routes stdout to
    # the data directory's log when there is no console. A reset is worth a trace.
    print(f"[data] reset {what}: {result['removed']}", flush=True)
    return {"ok": True, **result, **app_state.store.inventory()}


# -------------------------------------------------------------------- theory
@api.get("/api/theory")
def theory_vocabulary() -> dict[str, Any]:
    """What the pickers can offer. Sent once, so the UI never hardcodes a mode
    list that drifts from music.py's."""
    return {
        # Chromatic from C, so index == pitch class. theory.js relies on that to
        # light the right chip for a root it was not given by name (a chord in C#
        # major rooted on E# is spelled E# and sounds like F).
        "roots": ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"],
        "modes": [{"id": m, "label": theory.MODE_LABELS.get(m, m),
                   "fingered": m in theory.FINGERED_FORMS} for m in music.MODES],
        "qualities": [{"id": q["suffix"], "label": q["suffix"] or "major",
                       "name": q["name"], "size": len(q["intervals"])}
                      for q in theory.QUALITIES],
    }


@api.get("/api/theory/scale")
def theory_scale(key: str = "C", mode: str = "major", octaves: int = 1,
                 hand: str = "R") -> dict[str, Any]:
    try:
        return theory.scale_plan(key, mode, octaves=octaves, hand=hand)
    except (ValueError, KeyError) as err:
        raise HTTPException(400, str(err)) from None


@api.get("/api/theory/chord")
def theory_chord(root: str = "C", quality: str = "", inversion: int = 0) -> dict[str, Any]:
    try:
        return theory.chord_plan(root, quality, inversion=inversion)
    except (ValueError, KeyError) as err:
        raise HTTPException(400, str(err)) from None


# ------------------------------------------------------------------ metronome
@api.post("/api/metronome/config")
def metronome_config(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return {"ok": True, "metronome": app_state.metro.configure(body)}


@api.post("/api/metronome/{action}")
def metronome_action(action: str) -> dict[str, Any]:
    if action == "start":
        app_state.metro.start()
    elif action == "stop":
        app_state.metro.stop()
    elif action == "toggle":
        app_state.metro.toggle()
    elif action == "setback":
        app_state.metro.ramp_setback()
    else:
        raise HTTPException(404, f"unknown action '{action}'")
    app_state.click_offsets.clear()
    return {"ok": True, "metronome": app_state.metro.status()}


# ---------------------------------------------------------------------- pedal
@api.post("/api/pedal")
def set_pedal(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return {"ok": True, "pedal": app_state.engine.set_pedal(
        mode=str(body.get("mode", "") or ""),
        lo=body.get("lo"), hi=body.get("hi"), decay=body.get("decay"),
    )}


# ---------------------------------------------------------------- loop station
def _loop_reply() -> dict[str, Any]:
    return {"ok": True, "loop": app_state.loop.status(), "saved": app_state.loop.saved()}


@api.get("/api/loop")
def loop_state() -> dict[str, Any]:
    return _loop_reply()


@api.post("/api/loop/config")
def loop_config(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    app_state.loop.configure(body)
    return _loop_reply()


@api.post("/api/loop/save")
def loop_save(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    app_state.loop.save(str(body.get("name", "")))
    return _loop_reply()


@api.post("/api/loop/load")
def loop_load(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    app_state.loop.load(str(body.get("name", "")))
    return _loop_reply()


@api.post("/api/loop/layer/{layer_id}")
def loop_layer(layer_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    app_state.loop.update_layer(layer_id, body)
    return _loop_reply()


@api.delete("/api/loop/layer/{layer_id}")
def loop_layer_delete(layer_id: str) -> dict[str, Any]:
    app_state.loop.delete_layer(layer_id)
    return _loop_reply()


# ------------------------------------------------------------- backing tracks
def _backing_reply(error: str = "") -> dict[str, Any]:
    # exclusive is the whole reason this endpoint reports engine state: in exclusive
    # mode Keys owns the output device and the browser gets silence, so a backing track
    # looks broken rather than blocked. The UI says so instead.
    audio = app_state.settings.get("audio", default=config.HARDWARE) or config.HARDWARE
    return {
        "ok": not error, "error": error,
        "tracks": app_state.backing.all(),
        "exclusive": bool(audio.get("exclusive", False)),
    }


@api.get("/api/backing")
def backing_list() -> dict[str, Any]:
    return _backing_reply()


# ------------------------------------------------------------------- scores
@api.get("/api/scores")
def list_scores() -> dict[str, Any]:
    return {"ok": True, "scores": app_state.scores.all()}


@api.post("/api/scores")
async def import_score(request: Request) -> dict[str, Any]:
    """Import a MusicXML file. The body is the raw bytes; the name is a header.

    Raw bytes rather than multipart because there is exactly one file and no other
    field, and multipart would mean a parser dependency to carry one filename.
    """
    name = request.headers.get("x-filename", "score.musicxml")
    raw = await request.body()
    meta = app_state.scores.add(name, raw)
    if meta is None:
        raise HTTPException(400, app_state.scores.last_error or "could not import that")
    return {"ok": True, "score": meta, "scores": app_state.scores.all()}


@api.get("/api/scores/{score_id}/file")
def score_file(score_id: str) -> Response:
    """The original bytes, for Verovio to render in the browser.

    Untouched: Verovio reads MusicXML and .mxl directly, and re-serialising someone's
    file on the way past is how an importer silently loses what made their copy theirs.
    """
    raw = app_state.scores.data(score_id)
    if raw is None:
        raise HTTPException(404, "no such score")
    compressed = raw[:2] == b"PK"
    return Response(
        content=raw,
        media_type="application/vnd.recordare.musicxml" + ("" if compressed else "+xml"),
    )


@api.get("/api/scores/{score_id}/notes")
def score_notes(score_id: str) -> dict[str, Any]:
    """The note timeline: what playback and, later, following are driven from."""
    score = app_state.scores.parsed(score_id)
    if score is None:
        raise HTTPException(404, "no such score, or it can no longer be read")
    return {"ok": True, **score.to_dict()}


@api.post("/api/scores/{score_id}/transport/{action}")
def score_transport(score_id: str, action: str,
                    body: dict[str, Any] = Body(default=None)) -> dict[str, Any]:
    """The score transport. play / pause / stop / seek / tempo.

    One endpoint because they are one state machine, and splitting it would let the UI
    call two of them in an order the player does not expect.
    """
    player = app_state.player
    if app_state.engine.fs is None:
        raise HTTPException(503, "audio engine is not running")

    # Loading is idempotent and cheap after the first time, but it MUST happen before
    # a transport command that assumes a loaded score -- pressing play on a score the
    # player has never seen would otherwise start whatever was loaded last.
    if player.score_id != score_id:
        score = app_state.scores.parsed(score_id)
        if score is None:
            raise HTTPException(404, "no such score, or it can no longer be read")
        meta = app_state.scores.get(score_id) or {}
        player.load(score_id, score, str(meta.get("title", "")))

    at = (body or {}).get("at")
    bpm = (body or {}).get("bpm")
    if action == "play":
        return {"ok": True, "transport": player.play(at=at, bpm=bpm)}
    if action == "pause":
        return {"ok": True, "transport": player.pause()}
    if action == "stop":
        return {"ok": True, "transport": player.stop()}
    if action == "seek":
        return {"ok": True, "transport": player.seek(float(at or 0.0))}
    if action == "tempo":
        return {"ok": True, "transport": player.set_bpm(float(bpm or 100.0))}
    raise HTTPException(404, f"unknown transport action '{action}'")


@api.post("/api/scores/{score_id}")
def rename_score(score_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    meta = app_state.scores.rename(score_id, str(body.get("title", "")))
    if meta is None:
        raise HTTPException(404, app_state.scores.last_error or "no such score")
    return {"ok": True, "score": meta, "scores": app_state.scores.all()}


@api.delete("/api/scores/{score_id}")
def delete_score(score_id: str) -> dict[str, Any]:
    if not app_state.scores.remove(score_id):
        raise HTTPException(404, "no such score")
    return {"ok": True, "scores": app_state.scores.all()}


# ------------------------------------------------------------------- updates
# Only ever on request. Keys does not check on launch, on a timer, or in the
# background -- an app that quietly contacts a server every time you open it is not
# local-first no matter what its README says.
@api.post("/api/update/check")
def update_check() -> dict[str, Any]:
    return {"ok": True, **check_update()}


# Three presses, not one: check, then download, then restart. The download is the only
# one that takes a while, so it starts a worker thread and answers immediately -- the
# same contract as metro.start() and loop.arm(). Status is polled rather than pushed on
# the heartbeat because the About panel lives in the settings overlay, which receives no
# status frames at all.
@api.get("/api/update/status")
def update_status() -> dict[str, Any]:
    return app_state.updater.status()


@api.post("/api/update/download")
def update_download() -> dict[str, Any]:
    try:
        return app_state.updater.start()
    except UpdateBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    except UpdateError as exc:
        raise HTTPException(400, str(exc)) from exc


@api.post("/api/update/cancel")
def update_cancel() -> dict[str, Any]:
    return app_state.updater.cancel()


@api.post("/api/update/apply")
def update_apply() -> dict[str, Any]:
    # Answers, then closes Keys a moment later. Consent to download is not consent to
    # restart, which is why this is its own press and not the tail of the last one.
    try:
        return app_state.updater.apply()
    except UpdateError as exc:
        raise HTTPException(400, str(exc)) from exc


@api.post("/api/backing")
def backing_add(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    _tracks, error = app_state.backing.add(str(body.get("url", "")), str(body.get("title", "")))
    return _backing_reply(error)


@api.post("/api/backing/{track_id}")
def backing_update(track_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    _tracks, error = app_state.backing.update(track_id, body)
    return _backing_reply(error)


@api.delete("/api/backing/{track_id}")
def backing_delete(track_id: str) -> dict[str, Any]:
    app_state.backing.remove(track_id)
    return _backing_reply()


# Declared last so the fixed paths above win -- FastAPI matches in declaration order,
# and "/api/loop/{action}" would otherwise eat "/api/loop/config".
@api.post("/api/loop/{action}")
def loop_action(action: str) -> dict[str, Any]:
    fn = {
        "start": app_state.loop.start,
        "stop": app_state.loop.stop,
        "record": app_state.loop.arm,
        "cancel": app_state.loop.cancel,
        "clear": app_state.loop.clear,
    }.get(action)
    if fn is None:
        raise HTTPException(404, f"unknown action '{action}'")
    fn()
    return _loop_reply()


# ------------------------------------------------------------------- practice
@api.get("/api/stats")
def stats(days: int = 30) -> dict[str, Any]:
    days = max(1, min(365, days))
    store = app_state.store
    return {
        "today": store.today(),
        "streak": store.streak(),
        "history": store.history(days),
        "heatmap": store.note_heatmap(days),
        "velocity_histogram": store.velocity_histogram(days),
        "velocity_distinct_today": store.velocity_distinct(1),
        "sessions": store.recent_sessions(20),
        "sightread": store.sightread_summary(days),
        "weak_notes": store.weak_notes(12),
        "practice": app_state.practice.status(),
    }


@api.get("/api/timing")
def get_timing() -> dict[str, Any]:
    return app_state.timing_snapshot()


@api.get("/api/calendar")
def calendar(year: int = 0) -> dict[str, Any]:
    """One calendar year of daily practice, for the Activity chart.

    Its own endpoint rather than a field on /api/analytics, because paging back a year
    must not also change the chord counts, the key inference and every other panel on
    the page -- those answer over a rolling window and have nothing to do with which
    year you are looking at.
    """
    import datetime as _dt
    years = app_state.store.years()
    y = int(year) or _dt.date.today().year
    y = max(1970, min(9999, y))
    return {"ok": True, "years": years, **app_state.store.year(y)}


@api.get("/api/analytics")
def analytics(days: int = 365) -> dict[str, Any]:
    """Everything the Analytics view draws, in one request.

    Assembled server-side rather than as a dozen endpoints: it is read on a page load,
    not in a loop, and one round trip keeps the view's empty-data handling in one place.
    """
    days = max(1, min(1825, days))
    store = app_state.store
    pcs = store.pitch_class_histogram(days)
    return {
        "range_days": days,
        "calendar": store.history(days),
        "streak": store.streak(),
        "totals": store.totals(days),
        "note_heatmap": store.note_heatmap(days),
        "octaves": store.octave_histogram(days),
        "range": store.range_used(days),
        "top_chords": store.top_chords(days, limit=20),
        "chord_qualities": store.chord_qualities(days),
        "pitch_classes": pcs,
        # Inferred from the pitch classes rather than stored -- what key you played in is a
        # property of the notes, so deriving it means old sessions get it retroactively.
        "keys": music.infer_key(pcs, top=5),
        "intervals": store.interval_histogram(days),
        "hours": store.hour_histogram(days),
        "weekdays": store.weekday_histogram(days),
        "velocity_by_day": store.velocity_by_day(min(days, 180)),
        "notes_per_minute": store.notes_per_minute(min(days, 180)),
        "session_lengths": store.session_lengths(days),
        "presets": store.preset_usage(days),
        "sightread": store.sightread_summary(days),
    }


@api.post("/api/practice/end")
def end_practice() -> dict[str, Any]:
    app_state.practice.end_session()
    return {"ok": True, "practice": app_state.practice.status()}


# ------------------------------------------------------------------ exercises
def _namer(app: App):
    """How a step's notes get spelled for the staff -- by the reading key, so an
    E flat exercise says Eb and not D#."""
    key = app.reading_key()
    return lambda n: {"midi": n, "name": music.note_name(n, key),
                      "staff": "treble" if n >= 60 else "bass"}


@api.get("/api/exercises")
def list_exercises() -> dict[str, Any]:
    types = load_all()
    return {
        "exercises": [t.to_dict() for t in types.values()],
        "recent": app_state.store.recent_variants(limit=8),
        "running": app_state.run is not None and not app_state.run.done,
    }


@api.get("/api/exercises/state")
def exercise_state() -> dict[str, Any]:
    if app_state.run is None:
        return {"running": False}
    return app_state.run.state(_namer(app_state))


@api.post("/api/exercises/{exercise_id}/start")
def exercise_start(exercise_id: str, body: dict[str, Any] = Body(default=None)) -> dict[str, Any]:
    types = load_all()
    ex = types.get(exercise_id)
    if ex is None:
        raise HTTPException(404, f"no exercise '{exercise_id}'")

    # Whatever was running is abandoned rather than graded: you chose to leave it.
    _release_metronome()
    params = clean_params(ex, body)
    ctx = GenContext(store=app_state.store, rng=app_state.rng,
                     display_key=app_state.reading_key())
    try:
        plan = ex.generate(params, ctx)
    except ValueError as err:
        # A generator that cannot place this exercise on this keyboard says so, and the
        # message is written to be read by the person holding it. 400 rather than 500:
        # nothing is broken, the request just does not fit the instrument.
        raise HTTPException(400, str(err)) from None

    if plan.timed:
        # override(), not configure(): the exercise borrows the tempo for the length of
        # the run and must not rewrite the one saved in Tools.
        app_state.metro.override({"bpm": plan.bpm, "beats_per_bar": plan.beats_per_bar})
        app_state.metro.start()

    run = Run(plan, session_id=app_state.practice.session_id)
    app_state.last_exercise_fb = None
    app_state.run = run                       # <- the atomic rebind
    return run.state(_namer(app_state))


@api.post("/api/exercises/stop")
def exercise_stop() -> dict[str, Any]:
    run = app_state.run
    if run is None:
        return {"running": False, "result": None}
    run.stop()
    _release_metronome()
    result = grade(run.plan, run.records)
    result["params"] = dict(run.plan.params)
    result["title"] = run.plan.title
    result["duration_ms"] = int((time.perf_counter() - run.started_at) * 1000)
    # Read the session id at the END, not the start: practice sessions open on the first
    # note, so a run begun before you played anything would otherwise log against 0.
    app_state.store.log_exercise(app_state.practice.session_id, result, run.records)
    app_state.run = None
    return {"running": False, "result": result}


def _release_metronome() -> None:
    """Hand the tempo back and stop the click, but only if an exercise took it."""
    if app_state.run is not None and app_state.run.plan.timed:
        app_state.metro.stop()
        app_state.metro.release()


@api.get("/api/exercises/{exercise_id}/history")
def exercise_history(exercise_id: str, variant: str = "", days: int = 90) -> dict[str, Any]:
    return {
        "history": app_state.store.exercise_history(exercise_id, variant, days),
        "best_clean": app_state.store.best_clean_tempo(exercise_id, variant),
    }


# ------------------------------------------------------------------ sightread
@api.get("/api/sightread")
def sightread_state() -> dict[str, Any]:
    return app_state.sight.state()


@api.post("/api/sightread/new")
def sightread_new() -> dict[str, Any]:
    app_state.sight.session_id = app_state.practice.session_id
    return app_state.sight.new_exercise()


@api.post("/api/sightread/stop")
def sightread_stop() -> dict[str, Any]:
    app_state.sight.stop()
    return app_state.sight.state()


@api.post("/api/sightread/config")
def sightread_config(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    app_state.sight.configure(body)
    return app_state.sight.state()


# ----------------------------------------------------------------------- midi
@api.get("/api/midi")
def midi_status() -> dict[str, Any]:
    return app_state.midi.status()


@api.post("/api/midi/open/{index}")
def midi_open(index: int) -> dict[str, Any]:
    ok = app_state.midi.open(index)
    if ok:
        app_state.settings.update({"midi_port": index})
    return {"ok": ok, "midi": app_state.midi.status()}


# ------------------------------------------------------------------- settings
@api.get("/api/settings")
def get_settings() -> dict[str, Any]:
    return app_state.settings.all()


@api.post("/api/settings")
def post_settings(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    # Written back clamped rather than validated-and-rejected: a range is a pair, and
    # the two halves arrive from two sliders that can each be dragged past the other.
    # Storing the sane pair means every later reader gets one answer, and the panel
    # sees what it actually got.
    if "instrument" in body:
        # A non-dict would be stored verbatim and then poison every later write, because
        # _deep_merge only recurses when BOTH sides are dicts -- one bad POST and the
        # keyboard picker is broken until someone edits the file by hand.
        patch = dict(body["instrument"]) if isinstance(body["instrument"], dict) else {}
        if "low" in patch or "high" in patch:
            current = app_state.settings.get("instrument", default={})
            if not isinstance(current, dict):
                current = {}
            lo, hi = config.clamp_range(patch.get("low", current.get("low", config.LOW_KEY)),
                                        patch.get("high", current.get("high", config.HIGH_KEY)))
            patch["low"], patch["high"] = lo, hi
        if "octave" in patch:
            try:
                patch["octave"] = max(-config.MAX_OCTAVE,
                                      min(config.MAX_OCTAVE, int(patch["octave"] or 0)))
            except (TypeError, ValueError):
                patch["octave"] = 0
        body = {**body, "instrument": patch}

    updated = app_state.settings.update(body)
    if "instrument" in body:
        app_state.engine.apply_instrument()
    if "reverb" in body:
        app_state.engine.apply_reverb(updated.get("reverb", {}))
    if "chorus" in body:
        app_state.engine.apply_chorus(updated.get("chorus", {}))
    if "audio" in body and app_state.engine.fs is not None:
        # Only gain is safe to change live. Sample rate and buffer need a restart,
        # because WASAPI exclusive mode negotiates them when the stream opens.
        gain = body["audio"].get("gain")
        if gain is not None:
            app_state.engine.fs.setting("synth.gain", float(gain))
    return updated


# ------------------------------------------------------------------ frontend
# Mounted last on purpose: Starlette matches routes in order, so every /api path
# above wins and this only picks up what is left.
if config.FRONTEND_DIR.exists():
    api.mount("/", StaticFiles(directory=str(config.FRONTEND_DIR), html=True), name="frontend")
