"""The score library: the sheet music you have imported.

Files live in the data directory, not the bundle, so an update cannot delete them --
the same rule the practice database follows. The original bytes are kept exactly as
imported and never rewritten: it is your file, Verovio renders it directly, and a
"helpful" normalising pass is how an importer silently loses the thing that made your
copy yours.

A .mid is the one exception, and it is handled by keeping BOTH. The library stores the
MusicXML it was converted into, because everything downstream speaks MusicXML, and the
original .mid beside it, because the conversion is lossy in ways that only matter later
-- velocity is dropped outright, a tempo map collapses to one number, and a single-track
file has its hands guessed. Nothing reads the original yet. It is there so that the
decision to convert stays reversible.

Alongside each file is a small JSON sidecar with what backend/score.py read out of it
-- title, composer, bar count, note count. That is what a library list needs, and
re-parsing every score to draw a list of five would be absurd.

Nothing here is shipped with Keys. Every score in this directory is one you put there,
which is also the whole legal argument: an app with a File-Open dialog is a PDF viewer,
and a repository with someone's sheet music in it is something else entirely.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from . import config, midi_import
from .midi_import import MidiError
from .score import Score, ScoreError, parse, summarise

# MusicXML is XML or a zip of it. A 40 MB score does not exist; a 40 MB upload does.
MAX_BYTES = 40 * 1024 * 1024
SUFFIXES = (".musicxml", ".mxl", ".xml")
# Converted on the way in rather than stored as-is: everything downstream -- the
# engraver, the transport, the follower -- speaks MusicXML, and a second internal
# format would have to be handled in every one of them.
MIDI_SUFFIXES = (".mid", ".midi")


def _dir() -> Path:
    return config.DATA_DIR / "scores"


def _slug(text: str) -> str:
    out = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return out[:60] or "score"


class Library:
    def __init__(self) -> None:
        self.last_error = ""

    # ------------------------------------------------------------------ read
    def all(self) -> list[dict[str, Any]]:
        """Every imported score, newest first. Never raises."""
        root = _dir()
        if not root.exists():
            return []
        out: list[dict[str, Any]] = []
        for meta_path in root.glob("*.json"):
            try:
                meta = json.loads(meta_path.read_text("utf-8"))
            except (OSError, json.JSONDecodeError):
                continue        # a corrupt sidecar hides one score, not the library
            if not (root / str(meta.get("file", ""))).exists():
                continue
            out.append(meta)
        out.sort(key=lambda m: m.get("imported_at", 0), reverse=True)
        return out

    def get(self, score_id: str) -> dict[str, Any] | None:
        return next((m for m in self.all() if m.get("id") == score_id), None)

    def data(self, score_id: str) -> bytes | None:
        """The original file, byte for byte."""
        meta = self.get(score_id)
        if meta is None:
            return None
        path = _dir() / str(meta["file"])
        try:
            return path.read_bytes()
        except OSError:
            return None

    def parsed(self, score_id: str) -> Score | None:
        raw = self.data(score_id)
        if raw is None:
            return None
        meta = self.get(score_id) or {}
        try:
            return parse(raw, str(meta.get("name", "")))
        except ScoreError:
            return None

    # ----------------------------------------------------------------- write
    def add(self, filename: str, raw: bytes) -> dict[str, Any] | None:
        """Import a file. Returns its metadata, or None with last_error set."""
        self.last_error = ""
        name = Path(filename or "score").name
        lower = name.lower()
        if not lower.endswith(SUFFIXES + MIDI_SUFFIXES):
            self.last_error = (
                f"{name} is not a score Keys can read. It takes .musicxml and .mxl, "
                "and will convert a .mid that came out of a notation program.")
            return None
        if not raw:
            self.last_error = f"{name} is empty"
            return None
        if len(raw) > MAX_BYTES:
            self.last_error = f"{name} is {len(raw) // 1048576} MB; the limit is {MAX_BYTES // 1048576} MB"
            return None

        # MIDI becomes MusicXML here, before anything else looks at it, so the rest of
        # this method cannot tell the difference. The report rides along in the
        # metadata: how much had to be approximated is the one thing a converted score
        # knows that an authored one does not.
        converted: dict[str, Any] | None = None
        original = raw                  # kept, because converting cannot be undone
        suffix = Path(name).suffix.lower()
        if lower.endswith(MIDI_SUFFIXES):
            try:
                raw, converted = midi_import.convert(raw, Path(name).stem)
            except MidiError as exc:
                self.last_error = f"{name}: {exc}"
                return None
            suffix = ".musicxml"

        # Parsed before it is stored, so a file that cannot be read never enters the
        # library and the error names the reason rather than appearing later as a
        # score that will not open.
        try:
            score = parse(raw, name)
        except ScoreError as exc:
            self.last_error = str(exc)
            return None

        root = _dir()
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.last_error = f"could not create {root}: {exc}"
            return None

        score_id = uuid.uuid4().hex[:10]
        slug = _slug(score.title or Path(name).stem)
        stored = f"{slug}-{score_id}{suffix}"
        try:
            (root / stored).write_bytes(raw)
        except OSError as exc:
            self.last_error = f"could not save it: {exc}"
            return None

        # The .mid you actually imported, kept beside the conversion.
        #
        # Converting is a ONE-WAY DOOR, and a quiet one: the MusicXML that comes out
        # carries no velocity at all, one tempo where the file may have had a map, and
        # staves that were guessed when the file had a single track. None of that
        # matters to an engraver and all of it matters to anything that ever wants to
        # follow a performance. Throwing the source away costs nothing today and cannot
        # be undone tomorrow, so it is kept. Nothing reads it yet; that is the point.
        source = ""
        if converted is not None:
            source = f"{slug}-{score_id}{Path(name).suffix.lower()}"
            try:
                (root / source).write_bytes(original)
            except OSError:
                # Take the partial file with it. A half-written .mid that no sidecar
                # names is one remove() can never reach, and it would sit in the data
                # directory forever.
                (root / source).unlink(missing_ok=True)
                source = ""        # the score still imported; only the insurance failed

        meta = {
            "id": score_id,
            "file": stored,
            "name": name,
            "bytes": len(raw),
            "imported_at": time.time(),
            **summarise(score),
        }
        if converted is not None:
            meta["from_midi"] = True
            meta["snapped"] = converted["snapped"]
            if source:
                meta["source"] = source
        try:
            (root / f"{score_id}.json").write_text(json.dumps(meta, indent=1), "utf-8")
        except OSError as exc:
            (root / stored).unlink(missing_ok=True)
            self.last_error = f"could not save it: {exc}"
            return None
        return meta

    def remove(self, score_id: str) -> bool:
        meta = self.get(score_id)
        if meta is None:
            return False
        root = _dir()
        (root / str(meta["file"])).unlink(missing_ok=True)
        if meta.get("source"):
            (root / str(meta["source"])).unlink(missing_ok=True)
        (root / f"{score_id}.json").unlink(missing_ok=True)
        return True

    def rename(self, score_id: str, title: str) -> dict[str, Any] | None:
        meta = self.get(score_id)
        if meta is None:
            self.last_error = "no such score"
            return None
        meta["title"] = str(title).strip()[:120] or meta.get("title") or "Untitled"
        try:
            (_dir() / f"{score_id}.json").write_text(json.dumps(meta, indent=1), "utf-8")
        except OSError as exc:
            self.last_error = str(exc)
            return None
        return meta
