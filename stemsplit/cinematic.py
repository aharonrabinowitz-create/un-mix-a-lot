"""Local 'cinematic' separation: dialogue / music / effects with BandIt v2.

BandIt v2 (Watcharasupat et al.) splits film-style audio into speech, music and sound effects without
any prompt. Code: github.com/kwatcharasupat/bandit-v2 (Apache-2.0, vendored in stemsplit/bandit_v2).
Weights: Zenodo record 12701995 (CC BY-SA 4.0). They are downloaded on first use, not bundled.
"""
from __future__ import annotations

import os
import time
import urllib.request
from pathlib import Path
from typing import Callable

import numpy as np

from . import audio
from .chunking import crossfade_add, plan_chunks

CKPT_URL = "https://zenodo.org/records/12701995/files/checkpoint-multi.ckpt"
CKPT_BYTES = 446_680_129
MODEL_KWARGS = dict(
    in_channels=1, band_type="musical", n_bands=64, normalize_channel_independently=False,
    treat_channel_as_feature=True, n_sqm_modules=8, emb_dim=128, rnn_dim=256, bidirectional=True,
    rnn_type="GRU", mlp_dim=512, hidden_activation="Tanh", hidden_activation_kwargs=None,
    complex_mask=True, use_freq_weights=True, n_fft=2048, win_length=2048, hop_length=512,
    window_fn="hann_window", wkwargs=None, power=None, center=True, normalized=True,
    pad_mode="reflect", onesided=True, fs=audio.SR)
SRC_TO_STEM = {"speech": "voices", "music": "music", "sfx": "effects"}
CHUNK_S, OVERLAP_S, BATCH = 8.0, 1.0, 4


def cache_dir() -> Path:
    d = Path(os.environ.get("STEMSPLIT_CACHE", Path.home() / ".cache" / "stemsplit"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def ckpt_path() -> Path:
    return cache_dir() / "bandit-v2-multi.ckpt"


def is_downloaded() -> bool:
    p = ckpt_path()
    return p.exists() and p.stat().st_size == CKPT_BYTES


def download(progress: Callable[[float, str], None] | None = None) -> Path:
    """Fetch the weights once (about 447 MB) with progress; resumes nothing, retries by restarting."""
    p = ckpt_path()
    if is_downloaded():
        return p
    tmp = p.with_suffix(".part")
    with urllib.request.urlopen(CKPT_URL, timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or CKPT_BYTES)
        done, t0 = 0, time.time()
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)
            done += len(b)
            if progress:
                progress(done / total, f"Downloading BandIt v2 (one time): {done / 1e6:.0f} of {total / 1e6:.0f} MB")
    if tmp.stat().st_size != CKPT_BYTES:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("BandIt v2 download was incomplete. Please try again.")
    tmp.replace(p)
    return p


class CinematicSeparator:
    def __init__(self, device: str):
        import torch
        from .bandit_v2.bandit import Bandit

        self.torch, self.device = torch, device
        model = Bandit(stems=list(SRC_TO_STEM), **MODEL_KWARGS)
        # weights_only=True: the file is a downloaded pickle, so never allow arbitrary code to run.
        ck = torch.load(ckpt_path(), map_location="cpu", weights_only=True)
        sd = {k[len("model."):]: v for k, v in ck["state_dict"].items() if k.startswith("model.")}
        missing, unexpected = model.load_state_dict(sd, strict=False)
        if missing:
            raise RuntimeError(f"BandIt v2 weights don't match the model ({len(missing)} missing)")
        self.model = model.eval().to(device)

    def _run(self, batch: np.ndarray) -> dict[str, np.ndarray]:
        torch = self.torch
        x = torch.from_numpy(batch).float().to(self.device)[:, None, :]  # (B, 1, T)
        with torch.inference_mode():
            out = self.model({"mixture": {"audio": x}})["estimates"]
        return {k: v["audio"][:, 0, :].float().cpu().numpy() for k, v in out.items()}

    def separate(self, wav: np.ndarray, progress: Callable[[float, str], None] | None = None) -> dict[str, np.ndarray]:
        n = len(wav)
        spans = plan_chunks(n, audio.SR, CHUNK_S, OVERLAP_S) if n > CHUNK_S * audio.SR else [(0, n)]
        ov = int(OVERLAP_S * audio.SR)
        acc = {s: np.zeros(n, np.float32) for s in SRC_TO_STEM}
        for i in range(0, len(spans), BATCH):
            group = spans[i:i + BATCH]
            width = max(e - s for s, e in group)
            batch = np.zeros((len(group), width), np.float32)
            for j, (s, e) in enumerate(group):
                batch[j, :e - s] = wav[s:e]
            est = self._run(batch)
            for j, (s, e) in enumerate(group):
                for k in SRC_TO_STEM:
                    crossfade_add(acc[k], est[k][j, :e - s], s, first=(i + j == 0),
                                  last=(i + j == len(spans) - 1), overlap=ov)
            if progress:
                progress(min(1.0, (i + len(group)) / len(spans)), f"Separating chunk {min(i + len(group), len(spans))} of {len(spans)}")
        stems = {SRC_TO_STEM[k]: v for k, v in acc.items()}
        return stems


class MockCinematic:
    """Stand-in used by tests (no model needed)."""

    def separate(self, wav, progress=None):
        from .separator import MockSeparator  # frequency-band split
        m = MockSeparator()
        out = {}
        resid = wav
        for key, prompt in (("voices", "speech"), ("music", "music")):
            t, resid = m.separate(resid, prompt)
            out[key] = np.asarray(t, np.float32)
        out["effects"] = np.asarray(resid, np.float32)
        if progress:
            progress(1.0, "Mock separation")
        return out
