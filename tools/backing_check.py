"""Regression test for the backing-track shelf: URL parsing and the shelf itself.

    .venv\\Scripts\\python.exe tools\\backing_check.py

No audio device, no browser, no network -- everything here is string handling and a
settings file, which is exactly the part that breaks. A YouTube link arrives from a
share sheet with a playlist, a timestamp, three tracking parameters and sometimes no
scheme at all, and the id has to survive all of it.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config  # noqa: E402
from backend.backing import MAX_TRACKS, Backing, start_seconds, video_id  # noqa: E402

SCRATCH = config.Settings(Path(tempfile.mkdtemp(prefix="keys-backing-")) / "settings.json")

ok = True


def step(label: str, passed: bool, detail: str = "") -> None:
    global ok
    ok = ok and passed
    print(f"  [{'PASS' if passed else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))


ID = "dQw4w9WgXcQ"

print("1. every shape a YouTube link is pasted in")
GOOD = [
    (f"https://www.youtube.com/watch?v={ID}", "the canonical desktop link"),
    (f"https://youtube.com/watch?v={ID}", "no www"),
    (f"http://www.youtube.com/watch?v={ID}", "http"),
    (f"https://m.youtube.com/watch?v={ID}", "mobile"),
    (f"https://music.youtube.com/watch?v={ID}&list=RDAMVM{ID}", "YouTube Music"),
    (f"https://youtu.be/{ID}", "the share-sheet short link"),
    (f"youtu.be/{ID}", "pasted without a scheme"),
    (f"https://youtu.be/{ID}?t=42", "short link with a timestamp"),
    (f"https://www.youtube.com/embed/{ID}", "an embed URL"),
    (f"https://www.youtube.com/shorts/{ID}", "a short"),
    (f"https://www.youtube.com/live/{ID}", "a livestream"),
    (f"https://www.youtube.com/v/{ID}", "the ancient /v/ form"),
    (f"https://www.youtube-nocookie.com/embed/{ID}", "the privacy domain"),
    (f"https://www.youtube.com/watch?v={ID}&list=PLxyz&index=4&t=90s", "playlist + index + time"),
    (f"https://www.youtube.com/watch?feature=share&v={ID}", "id is not the first param"),
    (ID, "a bare id from a previous session"),
    (f"  https://youtu.be/{ID}  ", "with the whitespace a paste brings"),
]
for url, why in GOOD:
    step(why, video_id(url) == ID, url[:52])

print("2. things that are not a YouTube video")
BAD = [
    ("", "empty"),
    ("   ", "whitespace"),
    ("not a url at all", "prose"),
    ("https://vimeo.com/123456789", "a different host"),
    ("https://youtube.com/", "the home page"),
    ("https://www.youtube.com/watch?v=short", "id too short"),
    ("https://www.youtube.com/@somechannel", "a channel"),
    ("https://www.youtube.com/playlist?list=PLxyz", "a playlist with no video"),
    ("https://evil.com/youtube.com/watch?v=" + ID, "host that merely contains youtube.com"),
    ("javascript:alert(1)", "not a link"),
]
for url, why in BAD:
    step(f"rejects {why}", video_id(url) is None, repr(url[:44]))

print("3. the timestamp on a shared link is kept")
step("?t=90 seconds", start_seconds(f"https://youtu.be/{ID}?t=90") == 90.0)
step("?t=90s with the unit", start_seconds(f"https://youtu.be/{ID}?t=90s") == 90.0)
step("?t=1m30s", start_seconds(f"https://youtu.be/{ID}?t=1m30s") == 90.0)
step("?t=1h2m3s", start_seconds(f"https://youtu.be/{ID}?t=1h2m3s") == 3723.0)
step("&start= as well", start_seconds(f"https://www.youtube.com/watch?v={ID}&start=45") == 45.0)
step("no timestamp is 0", start_seconds(f"https://youtu.be/{ID}") == 0.0)
step("garbage timestamp is 0", start_seconds(f"https://youtu.be/{ID}?t=soon") == 0.0)

print("4. the shelf")
shelf = Backing(SCRATCH)
step("starts empty", shelf.all() == [])
tracks, err = shelf.add(f"https://youtu.be/{ID}?t=30", "Autumn Leaves - Bb")
step("adds a track", len(tracks) == 1 and not err, err or tracks[0]["title"])
step("keeps the timestamp as the loop start", tracks[0]["loop_a"] == 30.0)
step("starts with no loop", tracks[0]["loop_b"] <= tracks[0]["loop_a"] + 0.5,
     "b <= a means no loop, which is why they start equal")
step("defaults to normal speed", tracks[0]["rate"] == 1.0)

_t, err = shelf.add(f"https://www.youtube.com/watch?v={ID}")
step("the same video twice is refused", "already" in err, err)
step("even in a different URL form", len(shelf.all()) == 1)
_t, err = shelf.add("https://example.com/song.mp3")
step("a non-YouTube link is refused with a reason", "YouTube" in err, err)

tid = shelf.all()[0]["id"]
shelf.update(tid, {"key": "Bb", "bpm": "112", "notes": "ii-V-I in the bridge", "rate": 0.75})
got = shelf.all()[0]
step("metadata saved", got["key"] == "Bb" and got["bpm"] == 112 and got["rate"] == 0.75,
     f"{got['key']} {got['bpm']}bpm {got['rate']}x")
shelf.update(tid, {"rate": 9})
step("speed clamps to what YouTube accepts", shelf.all()[0]["rate"] == 2.0,
     "0.25-2.0; outside it the player silently ignores you")
shelf.update(tid, {"bpm": "not a number"})
step("a bad bpm is 0, not a crash", shelf.all()[0]["bpm"] == 0)
shelf.update(tid, {"loop_a": 12.5, "loop_b": 48.25})
step("loop points saved", shelf.all()[0]["loop_a"] == 12.5 and shelf.all()[0]["loop_b"] == 48.25)
_t, err = shelf.update("nope", {"key": "C"})
step("unknown track is an error, not a crash", err == "no such track")

print("5. persistence is the settings file, not a new store")
reopened = Backing(config.Settings(SCRATCH._path))  # noqa: SLF001
step("survives a reload", len(reopened.all()) == 1 and reopened.all()[0]["key"] == "Bb",
     "config.local.json, same as every other preference")

shelf.remove(tid)
step("removed", shelf.all() == [])
step("removing something absent is harmless", shelf.remove("nope") == [])

print("6. the shelf has a ceiling")
for i in range(MAX_TRACKS + 3):
    # Distinct 11-character ids, so each one is a different video.
    shelf.add(f"https://youtu.be/{str(i).zfill(11)}")
step(f"stops at {MAX_TRACKS}", len(shelf.all()) == MAX_TRACKS, str(len(shelf.all())))

print()
print("ALL CHECKS PASSED" if ok else "FAILURES ABOVE")
sys.exit(0 if ok else 1)
