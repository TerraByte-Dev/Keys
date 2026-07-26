"""Backing tracks -- a shelf of YouTube links you play along to.

The honest framing: this is a **bookmark list with a player attached**, and that is
deliberate. YouTube's terms allow embedding their player and driving it through the
IFrame API; they do not allow separating the audio, overlaying the video, or caching
it locally. So the two things a musician actually wants from a backing track -- pitch
shift and tempo change -- are off the table except for the one YouTube itself
provides, `setPlaybackRate`, which is a documented player control and changes pitch
with speed like a tape machine.

What is left is still worth having: a named shelf, per-track loop points so you can
grind eight bars of a solo without hunting the scrubber, a speed you set once and it
remembers, and the key and tempo you worked out written down next to the link.

Everything here is URL bookkeeping. The player is entirely in the browser -- nothing
about a backing track ever touches the audio engine, which is exactly why this module
has no reference to Engine anywhere in it.

**The one thing that will confuse people, and the reason this file knows about audio
settings at all:** in WASAPI exclusive mode Keys owns the output device, so the
browser gets silence and a backing track appears broken. The UI is told, so it can
say so rather than let you conclude the feature does not work.
"""

from __future__ import annotations

import re
import uuid
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import config

MAX_TRACKS = 100

# youtu.be/<id>, /embed/<id>, /shorts/<id>, /live/<id>, /v/<id>. The watch?v= form is
# handled by the query parser instead, because a watch URL can carry the id anywhere in
# the query string.
_PATH_FORMS = re.compile(r"^/(?:embed|shorts|live|v)/([A-Za-z0-9_-]{11})")
# 11 chars of base64url is the ID format and has been since 2007.
_BARE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def video_id(url: str) -> str | None:
    """Pull the video id out of anything a person might paste. None if there isn't one.

    Deliberately permissive about the host and strict about the id: people paste
    music.youtube.com links, m.youtube.com links, links with a playlist and a
    timestamp and three tracking parameters, and bare ids out of a previous session.
    """
    text = (url or "").strip()
    if not text:
        return None
    if _BARE_ID.match(text):
        return text
    if "//" not in text:
        text = "https://" + text        # "youtu.be/x" pastes without a scheme

    try:
        parsed = urlparse(text)
    except ValueError:
        return None

    host = (parsed.hostname or "").lower().removeprefix("www.")
    if host not in {"youtube.com", "youtu.be", "m.youtube.com", "music.youtube.com",
                    "youtube-nocookie.com"}:
        return None

    if host == "youtu.be":
        candidate = parsed.path.lstrip("/").split("/")[0]
        return candidate if _BARE_ID.match(candidate) else None

    found = _PATH_FORMS.match(parsed.path)
    if found:
        return found.group(1)

    for value in parse_qs(parsed.query).get("v", []):
        if _BARE_ID.match(value):
            return value
    return None


def start_seconds(url: str) -> float:
    """The ?t= / &start= timestamp, if the link carries one. 0 otherwise.

    Someone sharing "the solo starts here" pastes a link with a timestamp on it, and
    throwing that away loses the only interesting thing about that particular URL.
    """
    try:
        query = parse_qs(urlparse((url or "").strip()).query)
    except ValueError:
        return 0.0
    for key in ("t", "start"):
        for raw in query.get(key, []):
            found = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s?)?", raw.strip())
            if found and any(found.groups()):
                hrs, mins, secs = (int(g or 0) for g in found.groups())
                return float(hrs * 3600 + mins * 60 + secs)
    return 0.0


class Backing:
    """The shelf. Persisted in config.local.json alongside every other preference."""

    def __init__(self, settings: config.Settings | None = None) -> None:
        self.settings = settings or config.settings

    def all(self) -> list[dict[str, Any]]:
        raw = self.settings.get("backing", "tracks", default=[]) or []
        return [t for t in raw if isinstance(t, dict) and t.get("video")]

    def _save(self, tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # Settings deep-merges dicts but replaces lists wholesale, which is what we
        # want: the client never sends a patch to one track, it sends the shelf.
        self.settings.update({"backing": {"tracks": tracks[:MAX_TRACKS]}})
        return tracks[:MAX_TRACKS]

    def add(self, url: str, title: str = "") -> tuple[list[dict[str, Any]], str]:
        vid = video_id(url)
        if vid is None:
            return self.all(), "that does not look like a YouTube link"
        tracks = self.all()
        if any(t["video"] == vid for t in tracks):
            return tracks, "that track is already on the shelf"
        if len(tracks) >= MAX_TRACKS:
            return tracks, f"the shelf holds {MAX_TRACKS} tracks"
        tracks.append({
            "id": uuid.uuid4().hex[:8],
            "video": vid,
            "title": (title or "").strip()[:120] or vid,
            "url": url.strip()[:400],
            "key": "",
            "bpm": 0,
            "notes": "",
            "rate": 1.0,
            # Loop points in seconds. b <= a means "no loop", which is why they start
            # equal rather than at some sentinel.
            "loop_a": start_seconds(url),
            "loop_b": start_seconds(url),
        })
        return self._save(tracks), ""

    def update(self, track_id: str, patch: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
        tracks = self.all()
        found = next((t for t in tracks if t.get("id") == track_id), None)
        if found is None:
            return tracks, "no such track"
        if "title" in patch:
            found["title"] = str(patch["title"]).strip()[:120] or found["video"]
        if "key" in patch:
            found["key"] = str(patch["key"]).strip()[:12]
        if "notes" in patch:
            found["notes"] = str(patch["notes"]).strip()[:300]
        if "bpm" in patch:
            try:
                found["bpm"] = max(0, min(400, int(float(patch["bpm"]))))
            except (TypeError, ValueError):
                found["bpm"] = 0
        if "rate" in patch:
            # YouTube's own range. Anything outside it is silently ignored by the
            # player, which would read as the control being broken.
            found["rate"] = max(0.25, min(2.0, float(patch["rate"])))
        for key in ("loop_a", "loop_b"):
            if key in patch:
                found[key] = max(0.0, float(patch[key]))
        return self._save(tracks), ""

    def remove(self, track_id: str) -> list[dict[str, Any]]:
        return self._save([t for t in self.all() if t.get("id") != track_id])
