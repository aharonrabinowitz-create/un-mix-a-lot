"""One-click connect: add or remove the Un-Mix-a-Lot entry in Claude Desktop's settings file.

Rules: never touch a file we can't parse; only ever add/replace/remove our own entry; make a dated backup
before the first write; write via a temporary file so a crash can't leave a half-written file.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

KEY = "un-mix-a-lot"


def candidates() -> list[Path]:
    """Where Claude Desktop keeps claude_desktop_config.json (installer type matters on Windows)."""
    h = Path.home()
    if sys.platform == "win32":
        out = [Path(os.environ.get("APPDATA", h / "AppData" / "Roaming")) / "Claude" / "claude_desktop_config.json"]
        local = Path(os.environ.get("LOCALAPPDATA", h / "AppData" / "Local")) / "Packages"
        out += sorted(local.glob("Claude_*/LocalCache/Roaming/Claude/claude_desktop_config.json"))  # Store version
        return out
    if sys.platform == "darwin":
        return [h / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"]
    return [h / ".config" / "Claude" / "claude_desktop_config.json"]


def find_config() -> Path | None:
    """The config file to use: an existing one, else the standard location if Claude's folder exists."""
    c = candidates()
    for p in c:
        if p.exists():
            return p
    for p in c:
        if p.parent.is_dir():
            return p  # Claude is installed but hasn't made the file yet
    return None


def _read(p: Path) -> dict:
    if not p.exists():
        return {}
    text = p.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    data = json.loads(text)  # raises ValueError if it isn't valid JSON: we then refuse to touch it
    if not isinstance(data, dict):
        raise ValueError("unexpected format")
    return data


def _write(p: Path, data: dict) -> None:
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def _backup(p: Path) -> Path | None:
    if not p.exists():
        return None
    b = p.with_name(f"{p.stem}.backup-{time.strftime('%Y%m%d-%H%M%S')}{p.suffix}")
    shutil.copy2(p, b)
    return b


def connect(command: str, script: str) -> tuple[bool, str]:
    """Returns (ok, plain message)."""
    p = find_config()
    if p is None:
        return False, "I couldn't find Claude Desktop on this PC. Use the copy-and-paste text below instead."
    try:
        data = _read(p)
        servers = data.setdefault("mcpServers", {})
        if not isinstance(servers, dict):
            raise ValueError("unexpected format")
    except Exception:
        return False, ("Claude's settings file isn't in a format I'm willing to change automatically, so I left it alone. "
                       "Use the copy-and-paste text below instead.")
    entry = {"command": command, "args": [script]}
    if servers.get(KEY) == entry:
        return True, "Already connected. If Claude doesn't show it, quit Claude Desktop completely and reopen it."
    try:
        b = _backup(p)
        servers[KEY] = entry
        p.parent.mkdir(parents=True, exist_ok=True)
        _write(p, data)
    except OSError as e:
        return False, f"I couldn't save Claude's settings file ({e}). Use the copy-and-paste text below instead."
    note = f" A backup of the old file was saved as {b.name}." if b else ""
    return True, "Connected." + note + " Now quit Claude Desktop completely and open it again."


def disconnect() -> tuple[bool, str]:
    """Remove only our entry, from the file as it is now. Never restores a backup."""
    for p in candidates():
        if not p.exists():
            continue
        try:
            data = _read(p)
        except Exception:
            return False, "Claude's settings file isn't in a format I'm willing to change automatically. I left it alone."
        servers = data.get("mcpServers")
        if isinstance(servers, dict) and KEY in servers:
            _backup(p)
            del servers[KEY]
            _write(p, data)
            return True, "Disconnected. Quit Claude Desktop completely and reopen it. Your other settings were not touched."
    return True, "Un-Mix-a-Lot wasn't connected, so there was nothing to remove."
