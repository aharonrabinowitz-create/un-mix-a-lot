"""Cloud engine: MVSEP's DnR v3 or BandIt v2 (speech / music / effects) through their web API.

The user supplies their own MVSEP API token (kept in settings.json on their computer). The audio is
uploaded to mvsep.com, so the app tells the user before using it.
API docs: https://mvsep.com/full_api   (sep_type 56 = MVSep DnR v3, add_opt1 2 = Mel + SCNet ensemble)
"""
from __future__ import annotations

import re
import tempfile
import time
from pathlib import Path
from typing import Callable

import httpx
import numpy as np

from . import audio

BASE = "https://mvsep.com"
# Cloud models: name -> (sep_type, extra form fields)
MODELS = {
    "dnr": (56, {"add_opt1": "2"}),  # MVSep DnR v3, Mel + SCNet ensemble (best scores on MVSEP's own benchmark)
    "bandit": (45, {}),              # BandIt v2 (speech, music, effects)
}
SEP_TYPE = 56
TIMEOUT_S = 30 * 60
KEYS = {"speech": "voices", "dialog": "voices", "dialogue": "voices", "voice": "voices",
        "music": "music", "sfx": "effects", "effect": "effects"}


class MvsepError(RuntimeError):
    pass


class Cancelled(RuntimeError):
    """The user pressed Cancel."""


def login(email: str, password: str) -> str:
    """Exchange MVSEP email + password for an API token (the password is never stored)."""
    r = httpx.post(f"{BASE}/api/app/login", data={"email": email, "password": password}, timeout=30)
    try:
        j = r.json()
    except Exception:
        raise MvsepError(f"MVSEP login failed (HTTP {r.status_code}).")
    tok = (j.get("data") or {}).get("api_token")
    if not (j.get("success") and tok):
        raise MvsepError((j.get("data") or {}).get("message") or "MVSEP login failed. Check your email and password.")
    return tok


def _cancel(c: httpx.Client, token: str, hash_: str) -> bool:
    """Ask MVSEP to cancel the job. True only if MVSEP confirms (it refunds credits if not started)."""
    try:
        r = c.post(f"{BASE}/api/separation/cancel", data={"api_token": token, "hash": hash_}, timeout=30)
        return bool(r.json().get("success"))
    except Exception:
        return False


def _name_to_key(name: str) -> str | None:
    n = name.lower()
    for word, key in KEYS.items():
        if re.search(rf"(^|[^a-z]){word}", n):
            return key
    return None


def _files(data: dict) -> list[tuple[str, str]]:
    """Return [(name, url)]. The API docs don't spell out the file entries, so be tolerant."""
    out = []
    for f in data.get("files") or []:
        if isinstance(f, str):
            out.append((f, f))
        elif isinstance(f, dict):
            url = f.get("url") or f.get("download") or f.get("link") or f.get("path") or ""
            name = f.get("type") or f.get("name") or f.get("label") or f.get("filename") or url
            out.append((str(name) + " " + str(url), url))
    return [(n, u) for n, u in out if u]


def separate(wav: np.ndarray, token: str, progress: Callable[[float, str], None] | None = None,
             *, client: httpx.Client | None = None, poll_s: float = 3.0,
             cancelled: Callable[[], bool] | None = None, model: str = "dnr") -> dict[str, np.ndarray]:
    """Upload `wav`, wait for MVSEP, download the 3 stems, return voices/music/effects/other."""
    if not token:
        raise MvsepError("Add your MVSEP API token in Settings first.")
    sep_type, extra = MODELS.get(model, MODELS["dnr"])
    own = client is None
    c = client or httpx.Client(timeout=httpx.Timeout(120, connect=30), follow_redirects=True)
    try:
        with tempfile.TemporaryDirectory() as td:
            src = audio.write_audio(Path(td) / "input.flac", wav)
            if progress:
                progress(0.02, "Uploading to MVSEP…")
            hash_ = None
            note = ""
            for fmt in (1, 0):  # 1 = WAV (needs a registered account), 0 = MP3 320
                with open(src, "rb") as fh:
                    r = c.post(f"{BASE}/api/separation/create",
                               data={"api_token": token, "sep_type": sep_type, **extra,
                                     "output_format": fmt, "is_demo": "false"},
                               files={"audiofile": ("input.flac", fh, "audio/flac")})
                if r.status_code == 401:
                    raise MvsepError("MVSEP didn't accept that token. Re-enter it in Settings.")
                try:
                    j = r.json()
                except Exception:
                    raise MvsepError(f"MVSEP returned an unexpected reply (HTTP {r.status_code}).")
                if j.get("success"):
                    hash_ = j["data"]["hash"]
                    note = "" if fmt == 1 else " (MP3 output; WAV needs a registered MVSEP account)"
                    break
                if fmt == 0:
                    raise MvsepError((j.get("data") or {}).get("message") or "MVSEP rejected the job.")
            t0 = time.time()
            while True:
                if cancelled and cancelled():
                    if _cancel(c, token, hash_):
                        raise Cancelled("Cancelled, and MVSEP confirmed it removed your job.")
                    raise Cancelled("Stopped here, but MVSEP did not confirm the cancel. Check your MVSEP account page; "
                                    "the job may still run there.")
                if time.time() - t0 > TIMEOUT_S:
                    raise MvsepError("MVSEP took too long. Try again later.")
                waited_until = time.time() + poll_s
                while time.time() < waited_until:  # short naps so Cancel reacts at once
                    if cancelled and cancelled():
                        break
                    time.sleep(min(0.25, max(0.0, waited_until - time.time())))
                if cancelled and cancelled():
                    continue
                g = c.get(f"{BASE}/api/separation/get", params={"hash": hash_}).json()
                status = g.get("status")
                data = g.get("data") or {}
                if status == "done":
                    break
                if status in ("failed", "not_found"):
                    raise MvsepError(data.get("message") or "MVSEP could not process this file.")
                if progress:
                    if status == "waiting":
                        where = data.get("current_order")
                        waited = int(time.time() - t0)
                        progress(0.1, "Waiting in MVSEP's queue" + (f", position {where}" if where else "")
                                 + f" (waited {waited // 60}:{waited % 60:02d})" + note)
                    elif status in ("distributing", "merging") and data.get("all_chunks"):
                        progress(0.3 + 0.5 * (data.get("finished_chunks", 0) / max(1, data["all_chunks"])),
                                 f"MVSEP is separating (part {data.get('finished_chunks', 0)} of {data['all_chunks']})")
                    else:
                        progress(0.4, "MVSEP is separating…" + note)
            found: dict[str, str] = {}
            for name, url in _files(data):
                key = _name_to_key(name)
                if key and key not in found:
                    found[key] = url
            if not {"voices", "music", "effects"} <= set(found):
                raise MvsepError("MVSEP finished, but I couldn't find the speech / music / effects files in its reply.")
            stems = {}
            for i, (key, url) in enumerate(found.items()):
                if progress:
                    progress(0.85 + 0.12 * i / 3, f"Downloading {key} stem…")
                suffix = Path(url.split("?")[0]).suffix or ".wav"
                dst = Path(td) / f"{key}{suffix}"
                with c.stream("GET", url) as resp:
                    resp.raise_for_status()
                    with open(dst, "wb") as f:
                        for chunk in resp.iter_bytes(1 << 20):
                            f.write(chunk)
                s = audio.decode_mono(str(dst))
                s = s[:len(wav)] if len(s) >= len(wav) else np.pad(s, (0, len(wav) - len(s)))
                stems[key] = s.astype(np.float32)
    finally:
        if own:
            c.close()
    return stems
