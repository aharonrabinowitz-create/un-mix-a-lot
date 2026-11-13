"""Split the Voices stem into one track per speaker, without touching the audio quality.

How it works (the "voice matte"):
  1. Find where people are speaking (Silero VAD).
  2. Give every short window of speech a voice fingerprint (SpeechBrain ECAPA, 16 kHz) and group
     similar fingerprints (hierarchical clustering).
  3. Turn the groups into mattes and apply them to the ORIGINAL full-quality voices stem.
     The 16 kHz analysis is never heard; it only decides who is talking when.

The mattes partition the timeline (every sample belongs to exactly one voice, with 20 ms
crossfades), so the voice tracks always add back up to the original voices stem.

Limits: people talking at the same time end up on one track; very similar voices can be merged
or mixed up; the number of voices must be chosen.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

import numpy as np

from . import audio

ANALYSIS_SR = 16_000
WIN_S, HOP_S, MIN_WIN_S = 0.8, 0.2, 0.5
FADE_S = 0.02
MAX_VOICES = 4
PACKAGES = ["speechbrain", "silero-vad", "scipy"]

Progress = Callable[[float, str], None] | None


def cache_dir() -> Path:
    d = Path(os.environ.get("STEMSPLIT_CACHE", Path.home() / ".cache" / "stemsplit")) / "ecapa"
    d.mkdir(parents=True, exist_ok=True)
    return d


def available() -> bool:
    try:
        import scipy  # noqa: F401
        import silero_vad  # noqa: F401
        import speechbrain  # noqa: F401
        return True
    except Exception:
        return False


def ensure_packages(progress: Progress = None):
    """First use: install the small helper packages into the app's own Python (about a minute)."""
    if available():
        return
    if progress:
        progress(0.0, "Installing voice tools (one time, about a minute)…")
    py = Path(sys.executable)
    if py.name.lower() == "pythonw.exe":  # the app runs without a console; pip needs python.exe
        py = py.with_name("python.exe")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    p = subprocess.run([str(py), "-m", "pip", "install", "--disable-pip-version-check", *PACKAGES],
                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=flags)
    if p.returncode != 0:
        tail = p.stdout.decode(errors="replace").strip().splitlines()[-3:]
        raise RuntimeError("Couldn't install the voice tools: " + " | ".join(tail))
    import importlib
    importlib.invalidate_caches()
    if not available():
        raise RuntimeError("The voice tools were installed but can't be loaded. Restart the app and try again.")


def _amp_shim():
    """Older PyTorch (Intel Macs stop at 2.2) lacks torch.amp.custom_fwd/custom_bwd, which SpeechBrain
    1.1 uses. Provide them from torch.cuda.amp so the voice tools load."""
    import torch
    if hasattr(torch.amp, "custom_fwd"):
        return

    def wrap(fn):
        def deco(f=None, **kw):
            kw.pop("device_type", None)
            return fn(f, **kw) if f is not None else fn(**kw)
        return deco
    torch.amp.custom_fwd = wrap(torch.cuda.amp.custom_fwd)
    torch.amp.custom_bwd = wrap(torch.cuda.amp.custom_bwd)


_enc = {}


def _encoder(progress: Progress = None):
    if "m" not in _enc:
        if progress:
            progress(0.05, "Loading the voice fingerprint model (first use downloads about 80 MB)…")
        _amp_shim()
        from speechbrain.inference.speaker import EncoderClassifier
        from speechbrain.utils.fetching import LocalStrategy
        d = cache_dir()
        _enc["m"] = EncoderClassifier.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb", savedir=str(d),
            run_opts={"device": "cpu"}, local_strategy=LocalStrategy.COPY)
    return _enc["m"]


def _to_16k(x: np.ndarray) -> np.ndarray:
    from scipy.signal import resample_poly
    g = np.gcd(audio.SR, ANALYSIS_SR)
    return resample_poly(x, ANALYSIS_SR // g, audio.SR // g).astype(np.float32)


def analyse(x: np.ndarray, n_voices: int, progress: Progress = None, embed=None, vad=None):
    """Return (starts, labels): change points in seconds and the voice index after each one.

    `embed` and `vad` can be injected for testing; by default the real models are used.
    """
    import torch
    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.ndimage import median_filter

    x16 = _to_16k(x)
    if vad is None:
        from silero_vad import get_speech_timestamps, load_silero_vad
        model = load_silero_vad()
        spans = [(t["start"] / ANALYSIS_SR, t["end"] / ANALYSIS_SR) for t in get_speech_timestamps(
            torch.from_numpy(x16), model, sampling_rate=ANALYSIS_SR, min_silence_duration_ms=150)]
    else:
        spans = vad(x16)
    if progress:
        progress(0.15, "Finding who is speaking…")
    if embed is None:
        enc = _encoder(progress)

        def embed(seg):  # noqa: ANN001
            with torch.no_grad():
                return enc.encode_batch(torch.from_numpy(seg)[None])[0, 0].numpy()

    centres, embs = [], []
    total = max(1, sum(max(1, int((b - a) / HOP_S)) for a, b in spans))
    done = 0
    for a, b in spans:
        s = a
        while True:
            e = min(s + WIN_S, b)
            if e - s >= MIN_WIN_S:
                seg = x16[int(s * ANALYSIS_SR):int(e * ANALYSIS_SR)]
                v = np.asarray(embed(seg), dtype=np.float64)
                embs.append(v / (np.linalg.norm(v) + 1e-9))
                centres.append((s + e) / 2)
            done += 1
            if progress and done % 10 == 0:
                progress(0.15 + 0.7 * min(1.0, done / total), "Listening to the voices…")
            if s + WIN_S >= b:
                break
            s += HOP_S
    if len(embs) < 2:
        return [0.0], [0]
    E = np.array(embs)
    k = max(1, min(n_voices, len(E)))
    labs = fcluster(linkage(E, method="ward"), k, criterion="maxclust") - 1  # ward: balanced groups; "average" let a small voice merge into another
    labs = median_filter(labs, size=5, mode="nearest")
    # Number the voices in the order they first speak, so "Voice 1" is whoever talks first.
    first = {}
    for v in labs:
        first.setdefault(int(v), len(first))
    labs = np.array([first[int(v)] for v in labs])
    centres = np.array(centres)
    starts, out = [0.0], [int(labs[0])]
    for i in range(1, len(labs)):
        if labs[i] != labs[i - 1]:
            starts.append(float((centres[i - 1] + centres[i]) / 2))
            out.append(int(labs[i]))
    return starts, out


def _snap(x: np.ndarray, t: float, lo: float = 0.7, hi: float = 0.3) -> float:
    """Move a change point to the quietest moment around it so words aren't cut."""
    sr = audio.SR
    a, b = max(0, int((t - lo) * sr)), min(len(x), int((t + hi) * sr))
    if b - a < sr // 50:
        return t
    hop = sr // 100
    seg = x[a:b]
    n = len(seg) // hop
    if n < 2:
        return t
    rms = np.sqrt(np.mean(seg[:n * hop].reshape(n, hop) ** 2, axis=1))
    return (a + (int(np.argmin(rms)) + 0.5) * hop) / sr


def apply_segments(x: np.ndarray, segments, n_tracks: int = 2):
    """Cut `x` into one track per voice from [(start_s, end_s, voice_index)] segments.

    The segments are forced into a clean partition of the timeline (each starts where the last
    ended), so the tracks always add back up to `x` exactly. Returns (tracks, clean_segments).
    """
    from scipy.ndimage import uniform_filter1d
    x = np.asarray(x, dtype=np.float32)
    dur = len(x) / audio.SR
    segs = sorted((float(a), float(b), int(v)) for a, b, v in segments)
    if not segs:
        segs = [(0.0, dur, 0)]
    n_tracks = max(1, min(MAX_VOICES, max(int(n_tracks), max(v for _, _, v in segs) + 1)))
    segs = [(a, b, min(max(v, 0), n_tracks - 1)) for a, b, v in segs]
    starts = np.maximum.accumulate(np.clip([0.0] + [a for a, _, _ in segs[1:]], 0.0, dur))
    edges = list(starts) + [dur]
    clean = [(float(edges[i]), float(edges[i + 1]), segs[i][2]) for i in range(len(segs))]
    fade = max(1, int(FADE_S * audio.SR))
    tracks = []
    for c in range(n_tracks):
        m = np.zeros(len(x), dtype=np.float32)
        for a, b, v in clean:
            if v == c:
                m[int(a * audio.SR):int(b * audio.SR)] = 1.0
        m = uniform_filter1d(m, size=fade, mode="nearest")  # the masks still sum to 1 after smoothing
        tracks.append((x * m).astype(np.float32))
    tracks[-1] = (tracks[-1] + (x - sum(tracks))).astype(np.float32)  # exact additivity
    return tracks, clean


def split(x: np.ndarray, n_voices: int, progress: Progress = None, **inject):
    """Return ([voice arrays], segments) where the arrays sum exactly to `x`.

    segments = [(start_s, end_s, voice_index)] so they can be edited and re-applied.
    """
    n_voices = max(2, min(MAX_VOICES, int(n_voices)))
    x = np.asarray(x, dtype=np.float32)
    starts, labs = analyse(x, n_voices, progress, **inject)
    dur = len(x) / audio.SR
    if progress:
        progress(0.9, "Cutting the original audio…")
    cuts = [0.0] + [min(max(_snap(x, t), 0.0), dur) for t in starts[1:]]
    cuts = list(np.maximum.accumulate(np.array(cuts))) + [dur]
    segs = [(cuts[i], cuts[i + 1], int(lab)) for i, lab in enumerate(labs)]
    tracks, clean = apply_segments(x, segs, n_voices)
    if progress:
        progress(1.0, "Done")
    return tracks, clean


# ---- stand-ins for tests and the offline demo (no models needed): voices = tones of different pitch
def mock_vad(x16: np.ndarray, thresh: float = 0.01):
    hop = ANALYSIS_SR // 20
    n = len(x16) // hop
    act = np.sqrt(np.mean(x16[:n * hop].reshape(n, hop) ** 2, axis=1)) > thresh
    spans, start = [], None
    for i, a in enumerate(act):
        if a and start is None:
            start = i
        if not a and start is not None:
            spans.append((start / 20, i / 20)); start = None
    if start is not None:
        spans.append((start / 20, n / 20))
    return spans


def mock_embed(seg: np.ndarray):
    mag = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
    freqs = np.fft.rfftfreq(len(seg), 1 / ANALYSIS_SR)
    bins = np.geomspace(80, 4000, 17)
    h = np.array([mag[(freqs >= a) & (freqs < b)].sum() for a, b in zip(bins[:-1], bins[1:])])
    return h / (np.linalg.norm(h) + 1e-9)
