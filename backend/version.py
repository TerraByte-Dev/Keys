"""The version, and asking GitHub whether there is a newer one.

**Only when you press the button.** Keys does not phone home on launch, on a timer, or
in the background, and the check sends nothing but an HTTP GET for a public release
list. An app that quietly contacts a server every time you open it is not local-first
regardless of what its README says.

There is deliberately no auto-install here. Applying an update means replacing the
application directory while it is running, which is an installer's job -- Velopack's,
in this case -- and writing half of one that cannot be tested against a real release
would be worse than an honest link. See docs/PACKAGING.md.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

VERSION = "0.5.0"
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
    for asset in data.get("assets") or []:
        name = str(asset.get("name", ""))
        if name.lower().endswith((".exe", ".msi", ".zip")):
            result["download"] = str(asset.get("browser_download_url", ""))
            result["download_name"] = name
            result["download_size"] = int(asset.get("size", 0) or 0)
            break
    return result
