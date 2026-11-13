"""Model back ends. `SamAudioSeparator` wraps Meta's SAM Audio; `MockSeparator` is for UI/dev
work without a GPU or gated weights."""
from __future__ import annotations

import os
import threading
from typing import Protocol

import numpy as np

from .audio import SR

MODEL_IDS = {
    "small": "facebook/sam-audio-small",
    "base": "facebook/sam-audio-base",
    "large": "facebook/sam-audio-large",
}


class Separator(Protocol):
    def separate(self, wav: np.ndarray, prompt: str, *, predict_spans: bool = False,
                 candidates: int = 1, seed: int | None = None) -> tuple[np.ndarray, np.ndarray]:
        """Return (target, residual) as mono float32 arrays the same length as `wav`."""


def _to_np(x, length: int) -> np.ndarray:
    """SAM Audio returns a tensor or a list of tensors depending on version; normalise."""
    if isinstance(x, (list, tuple)):
        x = x[0]
    a = x.detach().float().cpu().numpy()
    a = a.reshape(-1) if a.ndim == 1 or a.shape[0] == 1 else a.mean(0)
    if len(a) < length:
        a = np.pad(a, (0, length - len(a)))
    return a[:length].astype(np.float32)


_patched = False
# Which optional parts of SAMAudio to build for the *next* load (set by SamAudioSeparator).
_build = {"rankers": False, "spans": False}


def _patch_sam_audio_hub_compat() -> None:
    """Make sam_audio's BaseModel loader work with huggingface_hub >= 1.0.

    sam_audio's `BaseModel._from_pretrained` requires `proxies` / `resume_download` keyword
    arguments (and forwards them to `snapshot_download`). huggingface_hub 1.x neither passes nor
    accepts them, which fails with "missing 2 required keyword-only arguments". The main model
    AND its internal ranker models (e.g. the Judge) load through this method, so we replace it
    with an equivalent version that doesn't need them.
    """
    global _patched
    if _patched:
        return
    import json
    import os

    import torch
    from huggingface_hub import snapshot_download
    from sam_audio.model.base import BaseModel

    def _from_pretrained(cls, *, model_id, cache_dir=None, force_download=False,
                         local_files_only=False, token=None, map_location="cpu", strict=True,
                         revision=None, **kwargs):
        kwargs.pop("proxies", None)
        kwargs.pop("resume_download", None)
        if os.path.isdir(model_id):
            d = model_id
        else:
            d = snapshot_download(repo_id=model_id, revision=cls.revision, cache_dir=cache_dir,
                                  force_download=force_download, token=token,
                                  local_files_only=local_files_only)
        with open(os.path.join(d, "config.json")) as f:
            config = json.load(f)
        for key, value in kwargs.items():
            if key in config:
                config[key] = value
        if cls.__name__ == "SAMAudio":
            # The text/visual rankers (Judge, CLAP, ImageBind: ~12 GB of extra weights) are only
            # used when reranking_candidates > 1, and the span predictor only for predict_spans.
            # Skipping them saves load time and VRAM.
            if not _build["rankers"]:
                config["text_ranker"] = None
                config["visual_ranker"] = None
            if not _build["spans"]:
                config["span_predictor"] = None
        model = cls(cls.config_cls(**config))
        state = torch.load(os.path.join(d, "checkpoint.pt"), weights_only=True,
                           map_location=map_location)
        model.load_state_dict(state, strict=strict)
        return model

    BaseModel._from_pretrained = classmethod(_from_pretrained)
    _patched = True


def _load_sam_audio(model_id: str):
    from sam_audio import SAMAudio

    _patch_sam_audio_hub_compat()
    return SAMAudio.from_pretrained(model_id)


class SamAudioSeparator:
    """Thin wrapper over `sam_audio.SAMAudio`. Weights are loaded on CPU first; `to(device)`
    moves them, which is what ZeroGPU needs (CUDA only exists inside @spaces.GPU)."""

    def __init__(self, size: str = "small", dtype: str | None = None, *, rankers: bool = False,
                 spans: bool = False):
        import torch
        from sam_audio import SAMAudio, SAMAudioProcessor

        self.rankers, self.spans = rankers, spans
        _build.update(rankers=rankers, spans=spans)

        model_id = MODEL_IDS[size]
        try:
            self.model = _load_sam_audio(model_id)
            self.processor = SAMAudioProcessor.from_pretrained(model_id)
        except Exception as e:
            msg = str(e)
            if any(k in msg for k in ("401", "403", "gated", "restricted", "Access to model")):
                raise RuntimeError(
                    f"Hugging Face denied access to {model_id}. In Settings, Model tab, request access and save "
                    "your token, then wait for Meta's approval."
                ) from e
            raise
        self.model.eval()
        if dtype in ("bfloat16", "float16"):  # experimental, off by default
            self.model.to(getattr(torch, dtype))
        self.size = size
        self.device = "cpu"
        self.sr = int(getattr(self.processor, "audio_sampling_rate", SR))
        assert self.sr == SR, f"model expects {self.sr} Hz, app assumes {SR}"

    def to(self, device: str) -> "SamAudioSeparator":
        if device != self.device:
            self.model.to(device)
            self.device = device
        return self

    def separate(self, wav, prompt, *, predict_spans=False, candidates=1, seed=None):
        import torch

        if seed is not None:
            torch.manual_seed(seed)
        audio = torch.from_numpy(wav).unsqueeze(0)  # (channels=1, time)
        batch = self.processor(audios=[audio], descriptions=[prompt]).to(self.device)
        with torch.inference_mode():
            res = self.model.separate(batch, predict_spans=predict_spans,
                                      reranking_candidates=candidates)
        return _to_np(res.target, len(wav)), _to_np(res.residual, len(wav))


class MockSeparator:
    """Fake model: splits by frequency band so stems are audibly different. Dev/test only."""

    BANDS = {"speech": (200, 3400), "music": (20, 200), "sound": (3400, 24000)}

    def to(self, device):  # noqa: D401
        return self

    def separate(self, wav, prompt, *, predict_spans=False, candidates=1, seed=None):
        import time
        time.sleep(float(os.environ.get("STEMSPLIT_MOCK_DELAY", "0")))
        lo, hi = next((b for k, b in self.BANDS.items() if k in prompt.lower()), (0, 100))
        spec = np.fft.rfft(wav)
        f = np.fft.rfftfreq(len(wav), 1 / SR)
        mask = (f >= lo) & (f < hi)
        target = np.fft.irfft(spec * mask, n=len(wav)).astype(np.float32)
        return target, (wav - target).astype(np.float32)


_cache: dict[str, Separator] = {}
_lock = threading.Lock()


def get_separator(size: str, *, rankers: bool = False, spans: bool = False,
                  evict: bool = True) -> Separator:
    """Load (once) and return the separator for a model size.

    `rankers` / `spans` request the optional parts needed for reranking / span prediction. A model
    loaded without them is rebuilt when they're needed later. With `evict`, other sizes are freed
    so only one model sits in memory at a time.
    """
    if os.environ.get("STEMSPLIT_MOCK") == "1":
        return MockSeparator()
    with _lock:
        cur = _cache.get(size)
        rankers = rankers or bool(cur and getattr(cur, "rankers", False))
        spans = spans or bool(cur and getattr(cur, "spans", False))
        stale = cur is not None and ((rankers and not cur.rankers) or (spans and not cur.spans))
        drop = [k for k in _cache if (evict and k != size) or (stale and k == size)]
        for k in drop:
            _cache.pop(k, None)
        if drop:
            _free_gpu()
        if size not in _cache:
            _cache[size] = SamAudioSeparator(size, dtype=os.environ.get("STEMSPLIT_DTYPE"),
                                             rankers=rankers, spans=spans)
        return _cache[size]


def _free_gpu() -> None:
    import gc

    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass
