"""Check GitHub for a newer Un-Mix-a-Lot release, and (Windows installed copy only) update in place.

Checking only ever tells the user. Updating happens only when the user clicks "Update now": it downloads the small
setup file from the latest GitHub release and runs it silently over the existing install."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

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


# ----------------------------------------------------------------------------- in-place update (Windows installed copy)
INSTALLER_URL = f"https://github.com/{REPO}/releases/latest/download/Un-Mix-a-Lot-Setup.exe"
APP_DIR = Path(__file__).resolve().parent.parent


def install_dir() -> Path | None:
    """The folder of an installed copy (made by Un-Mix-a-Lot-Setup.exe), else None (dev copy, Mac, Linux)."""
    fake = os.environ.get("STEMSPLIT_FAKE_INSTALL")  # tests only
    if fake:
        return Path(fake)
    if sys.platform == "win32" and (APP_DIR / "Uninstall.exe").is_file() and (APP_DIR / ".venv").is_dir():
        return APP_DIR
    return None


def can_self_update() -> bool:
    return install_dir() is not None


def download_installer(url: str | None = None, timeout: float = 60.0) -> Path:
    """Download the setup file to a temp folder and sanity-check it. Raises RuntimeError with a plain message."""
    url = url or os.environ.get("STEMSPLIT_UPDATE_URL") or INSTALLER_URL
    dest = Path(tempfile.mkdtemp(prefix="unmix-update-")) / "Un-Mix-a-Lot-Setup.exe"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Un-Mix-a-Lot"})
        with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "wb") as f:
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
    except Exception as e:
        raise RuntimeError(f"Couldn't download the update ({e}). Check your internet and try again.") from e
    size = dest.stat().st_size
    with open(dest, "rb") as f:
        head = f.read(2)
    if size < 100_000 or head != b"MZ":
        raise RuntimeError("The downloaded update doesn't look right, so it was not run. Nothing was changed.")
    return dest


def installer_command(setup: Path, target: Path) -> str:
    """Update over the existing folder: shows only a progress window. /D= must be last and must not be quoted."""
    return f'"{setup}" /UPDATE /D={target}'


def run_installer(setup: Path, target: Path) -> str:
    """Start the setup file detached from this process, so it can close the app and replace its files."""
    cmd = installer_command(setup, target)
    dry = os.environ.get("STEMSPLIT_UPDATE_DRYRUN")  # tests only: record the command instead of running it
    if dry:
        Path(dry).write_text(cmd, encoding="utf-8")
        return cmd
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(cmd, creationflags=flags, close_fds=True)
    return cmd
