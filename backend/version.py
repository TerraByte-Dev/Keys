"""The version, and asking GitHub whether there is a newer one.

**Once when Keys opens, and whenever you press the button.** The launch check is what
puts the dot on the gear -- you cannot show a badge for news you refused to hear -- and
`ui.update_check_on_launch` turns it off, after which this runs only on a button press.
Either way it is one HTTP GET for a public release list, it sends nothing about you,
and there is no timer and nothing in the background.

There is deliberately no auto-install here either. This module only answers "is there
a newer one, and where are its bytes"; downloading and installing them is
`backend/updater.py`, and it too moves only when a button is pressed.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

VERSION = "0.8.2"
REPO = "TerraByte-Dev/Keys"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
TIMEOUT = 8.0

_NUM = re.compile(r"\d+")


def parse(version: str) -> tuple[int, ...]:
    """A tag to a comparable tuple. Lenient, because tags are written by hand.

    'v1.2.3' and '1.2.3' and 'Keys 1.2' all compare sensibly; anything with no digits
    at all sorts below everything, which is the safe direction -- an unparseable remote
    tag should never look like an upgrade.
    """
    parts = tuple(int(n) for n in _NUM.findall(version or ""))
    return parts or (0,)


def is_newer(remote: str, local: str = VERSION) -> bool:
    a, b = parse(remote), parse(local)
    # Pad so 1.2 and 1.2.0 compare equal rather than by length.
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def check() -> dict[str, Any]:
    """Ask GitHub for the latest release. Never raises."""
    result: dict[str, Any] = {
        "current": VERSION, "latest": "", "newer": False,
        "url": RELEASES_PAGE, "notes": "", "error": "",
    }
    try:
        req = urllib.request.Request(
            RELEASES_API,
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": f"Keys/{VERSION}"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
            data = json.loads(res.read())
    except urllib.error.HTTPError as exc:
        # 404 is the normal answer for a repo that has never cut a release, and saying
        # "not found" would read as a bug rather than as "you are on the newest one".
        result["error"] = ("no releases published yet" if exc.code == 404
                           else f"GitHub said {exc.code}")
        return result
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        result["error"] = f"could not reach GitHub: {exc}"
        return result
    except (json.JSONDecodeError, ValueError):
        result["error"] = "GitHub returned something unreadable"
        return result

    tag = str(data.get("tag_name") or data.get("name") or "")
    result["latest"] = tag
    result["newer"] = bool(tag) and is_newer(tag)
    result["url"] = str(data.get("html_url") or RELEASES_PAGE)
    result["notes"] = str(data.get("body") or "")[:2000]
    assets = data.get("assets") or []
    for asset in assets:
        name = str(asset.get("name", ""))
        if name.lower().endswith((".exe", ".msi", ".zip")):
            result["download"] = str(asset.get("browser_download_url", ""))
            result["download_name"] = name
            result["download_size"] = int(asset.get("size", 0) or 0)
            # "sha256:<hex>", computed by GitHub itself -- so it is there on releases
            # cut long before Keys published a checksum of its own.
            result["download_digest"] = str(asset.get("digest") or "")
            # And the sidecar tools/build_exe.py publishes for new releases. There is
            # only ever one payload asset, so the first .sha256 is unambiguous.
            # Absent on everything up to 0.5.1; updater.py verifies when present.
            result["download_sha256_url"] = next(
                (str(a.get("browser_download_url", "")) for a in assets
                 if str(a.get("name", "")).lower().endswith(".sha256")), "")
            break
    return result
