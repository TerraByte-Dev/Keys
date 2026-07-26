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
import time
from collections import deque
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from . import config, engine as engine_mod, music, timing
from .engine import Engine, Preset, Zone
from .hub import BEND, CONTROL, NOTE_OFF, NOTE_ON, Hub
from .metronome import Metronome
from .midi_in import MidiInput
from .practice import PracticeClock
from .sightread import SightReader
from .store import Store

FRAME_HZ = 60
STATUS_HZ = 1
SUSTAIN_CC = 64


class App:
    """Everything with a lifetime. One instance, created at startup."""

    def __init__(self) -> None:
        self.settings = config.settings
        self.engine = Engine(self.settings)
        self.hub = Hub()
        self.midi = MidiInput(self.engine, self.hub)
        self.metro = Metronome(self.engine, self.settings)
        self.store = Store(config.DB_PATH)
        self.practice = PracticeClock(self.store, self.settings)
        self.sight = SightReader(self.store, self.settings)
        self.presets: dict[str, Preset] = {}

        self.clients: set[WebSocket] = set()
        self.held: set[int] = set()
        self.sustain = False
        self.chord: dict[str, Any] | None = None
        self.click_offsets: deque[float] = deque(maxlen=512)
        self.last_feedback: dict[str, Any] | None = None
        self._task: asyncio.Task | None = None
        self._boot_errors: list[str] = []

    # ------------------------------------------------------------- lifecycle
    def startup(self) -> None:
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

        port = self.settings.get("midi_port")
        if not self.midi.open(port):
            self._boot_errors.append(self.midi.last_error)
        self.midi.start_watcher()

    def shutdown(self) -> None:
        # Order is load-bearing: stop MIDI before deleting the synth, or a note that
        # arrives mid-teardown calls into a freed Synth from the callback thread.
        self.midi.stop_watcher()
        self.midi.close()
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
                    if kind == NOTE_ON:
                        self.held.add(a)
                        on.append([a, b])
                        held_changed = True
                        self.practice.on_note(t, a, b)
                        fb = self.sight.on_note(a, b, t)
                        if fb is not None:
                            self.last_feedback = fb
                        offset = self.metro.click_offset_ms(t)
                        if offset is not None:
                            self.click_offsets.append(offset)
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

                if held_changed:
                    notes = sorted(self.held)
                    self.chord = music.detect_chord(notes, key) if len(notes) >= 3 else None

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
            "engine": self.engine.status(),
            "midi": self.midi.status(),
            "metronome": self.metro.status(),
            "practice": self.practice.status(now),
            "sightread": {"active": self.sight.active, "index": self.sight.index},
            "hub": self.hub.stats(),
            "timing": self.timing_snapshot(),
            "errors": self._boot_errors,
        }

    def timing_snapshot(self) -> dict[str, Any]:
        onsets = self.practice.onsets()[-96:]
        offsets = list(self.click_offsets)[-96:] if self.metro.status()["running"] else None
        return timing.analyze(onsets, offsets)

    def full_state(self) -> dict[str, Any]:
        return {
            "engine": self.engine.status(),
            "midi": self.midi.status(),
            "metronome": self.metro.status(),
            "practice": self.practice.status(),
            "sightread": self.sight.state(),
            "settings": self.settings.all(),
            "presets": [p.to_dict() for p in self.presets.values()],
            "soundfonts": self.engine.list_soundfonts(),
            "curves": engine_mod.CURVE_NAMES,
            "keys": music.KEYS,
            "range": {"low": config.LOW_KEY, "high": config.HIGH_KEY},
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
    warnings = app_state.engine.set_zones(preset.zones, preset.id, preset.name)
    app_state.settings.update({"preset": pid})
    app_state.practice.preset = pid
    return {"ok": True, "warnings": warnings, "engine": app_state.engine.status()}


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
    path = config.PRESET_DIR / f"{pid}.json"
    if not path.exists():
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
    warnings = app_state.engine.set_zones(zones, body.get("id", ""), body.get("name", ""))
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
        "exclusive": bool(audio.get("exclusive", True)),
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
    app_state.engine.panic()
    app_state.held.clear()
    app_state.sustain = False
    return {"ok": True}


@api.post("/api/preview")
def preview(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Audition notes from the UI. The note-off is scheduled on the sequencer, not
    on a Python timer -- same rule as the metronome."""
    notes = [int(n) for n in body.get("notes", [])][:16]
    velocity = max(1, min(127, int(body.get("velocity", 90))))
    ms = max(50, min(4000, int(body.get("ms", 700))))
    eng = app_state.engine
    if eng.fs is None:
        raise HTTPException(503, "engine not started")
    channel = int(body.get("channel", eng.active_channels[0]))
    if eng.sequencer is not None:
        at = eng.sequencer.get_tick() + 5
        for n in notes:
            eng.sequencer.note(at, channel, n, velocity, ms, dest=eng.seq_dest)
    else:
        for n in notes:
            eng.fs.noteon(channel, n, velocity)
    return {"ok": True, "notes": notes}


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


@api.post("/api/practice/end")
def end_practice() -> dict[str, Any]:
    app_state.practice.end_session()
    return {"ok": True, "practice": app_state.practice.status()}


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
    updated = app_state.settings.update(body)
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
