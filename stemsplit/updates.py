"""Check GitHub for a newer Un-Mix-a-Lot release. Only ever tells the user; never installs anything."""
from __future__ import annotations

import json
import os
import re
import urllib.request

from . import __version__

REPO = "aharonrabinowitz-create/un-mix-a-lot"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
PAGE = f"https://github.com/{REPO}/releases/latest"


def _parse(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v.split("-")[0])[:3]) or (0,)


def is_newer(latest: str, current: str = __version__) -> bool:
    a, b = _parse(latest), _parse(current)
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def latest(timeout: float = 6.0) -> tuple[str, str] | None:
    """(version, page) of the newest release, or None when it can't be reached."""
    mock = os.environ.get("STEMSPLIT_MOCK_UPDATE")  # tests only
    if mock:
        return (mock, PAGE) if mock != "offline" else None
    try:
        req = urllib.request.Request(API, headers={"Accept": "application/vnd.github+json", "User-Agent": "Un-Mix-a-Lot"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.load(r)
        return str(d["tag_name"]).lstrip("vV"), str(d.get("html_url") or PAGE)
    except Exception:
        return None


def check() -> dict:
    """{'state': 'new'|'current'|'offline', 'version': latest, 'url': page}"""
    got = latest()
    if got is None:
        return {"state": "offline", "version": "", "url": PAGE}
    v, url = got
    return {"state": "new" if is_newer(v) else "current", "version": v, "url": url}
