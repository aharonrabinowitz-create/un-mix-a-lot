"""Karaoke mode: take the lead singer out of a song, and (optionally) take the backing singers out too.

Step 1 (default): a karaoke model separates the LEAD singer. What is left is the music with the
backing singers still in it. This is the most reliable split.

Step 2 (optional button): a vocals-vs-music model runs on that leftover and removes the singing
that is still in it. The backing singers are whatever it took out (leftover minus music), so the
three tracks always add back up to the original: lead + backing + music.

Both models are run with the audio-separator package. Models are downloaded by the user's own
computer the first time they are used (they are NOT bundled with the app).
"""
from __future__ import annotations

import importlib.util
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

import numpy as np

from . import audio

PACKAGES = ["audio-separator", "onnxruntime", "audioread"]
LEAD_MODEL = "mel_band_roformer_karaoke_becruily.ckpt"      # lead singer vs everything else
BACKING_MODEL = "vocals_mel_band_roformer.ckpt"             # Kim vocals model: singing vs music
LEAD_NAME, BACKING_NAME = "becruily's karaoke model", "Kimberley Jensen's vocal model"

Progress = Callable[[float, str], None] | None


class RestartNeeded(RuntimeError):
    """The helper packages were just installed; the app must be restarted before they can be used."""


def model_dir() -> Path:
    d = Path(os.environ.get("STEMSPLIT_CACHE", Path.home() / ".cache" / "stemsplit")) / "karaoke"
    d.mkdir(parents=True, exist_ok=True)
    return d


def available() -> bool:
    return all(importlib.util.find_spec(m) is not None for m in ("audio_separator", "onnxruntime", "audioread"))


def ensure_packages(progress: Progress = None):
    """First use: install the helper packages into the app's own Python (a few minutes)."""
    if available():
        return
    if progress:
        progress(0.0, "Installing karaoke tools (one time, a few minutes)…")
    py = Path(sys.executable)
    if py.name.lower() == "pythonw.exe":  # the app runs without a console; pip needs python.exe
        py = py.with_name("python.exe")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    p = subprocess.run([str(py), "-m", "pip", "install", "--disable-pip-version-check", *PACKAGES],
                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=flags)
    if p.returncode != 0:
        tail = p.stdout.decode(errors="replace").strip().splitlines()[-3:]
        raise RuntimeError("Couldn't install the karaoke tools: " + " | ".join(tail))
    importlib.invalidate_caches()
    # Installing replaces libraries (numpy, for one) that this running app has already loaded, so it must restart.
    raise RestartNeeded("The karaoke tools are installed. Close Un-Mix-a-Lot and open it again, then run Karaoke again. "
                        "(This restart is only needed one time.)")


def _fit(x: np.ndarray, n: int) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    if len(x) >= n:
        return x[:n]
    return np.pad(x, (0, n - len(x)))


def _run_model(model_file: str, wav: np.ndarray, progress: Progress, label: str) -> dict[str, np.ndarray]:
    """Run one audio-separator model on mono audio. Returns {'vocals': ..., 'rest': ...} at the app's rate."""
    from audio_separator.separator import Separator  # imported late: heavy, and only installed on first use

    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "in.wav"
        audio.write_audio(src, wav)
        if progress:
            progress(0.0, f"Loading {label} (the first time it downloads about 1 GB)…")
        sep = Separator(output_dir=td, output_format="WAV", model_file_dir=str(model_dir()),
                        log_level=logging.WARNING)
        sep.load_model(model_filename=model_file)
        if progress:
            progress(0.3, f"Separating with {label}…")
        names = sep.separate(str(src))
        outs = {}
        for n in names:
            p = Path(n) if Path(n).is_absolute() else Path(td) / n
            kind = "vocals" if "(vocals)" in p.name.lower() else "rest"
            outs[kind] = _fit(audio.decode_mono(p), len(wav))
        del sep
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
    if "vocals" not in outs or "rest" not in outs:
        raise RuntimeError(f"The {label} did not return the expected two tracks.")
    return outs


def lead_split(wav: np.ndarray, progress: Progress = None, *, mock: bool = False) -> dict[str, np.ndarray]:
    """Lead singer out. Returns {'lead': ..., 'music': ...} where music still holds the backing singers."""
    if mock:
        return {"lead": (wav * 0.5).astype(np.float32), "music": (wav * 0.5).astype(np.float32)}
    o = _run_model(LEAD_MODEL, wav, progress, LEAD_NAME)
    if progress:
        progress(1.0, "Done")
    return {"lead": o["vocals"], "music": o["rest"]}


def backing_split(music_with_backing: np.ndarray, progress: Progress = None, *,
                  mock: bool = False) -> tuple[np.ndarray, np.ndarray]:
    """Singing out of the leftover. Returns (backing singers, music only); they add up to the input."""
    x = music_with_backing
    if mock:
        music = (x * 0.7).astype(np.float32)
    else:
        music = _run_model(BACKING_MODEL, x, progress, BACKING_NAME)["rest"]
    backing = (x - music).astype(np.float32)
    if progress:
        progress(1.0, "Done")
    return backing, music
