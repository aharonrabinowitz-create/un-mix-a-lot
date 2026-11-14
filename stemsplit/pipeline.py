"""Auto-separation: voices -> music -> effects cascade over residuals, chunked with crossfade."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from . import audio
from .chunking import crossfade_add, plan_chunks
from .separator import Separator

VOICE_KEYS = ["voices", "voices2", "voices3", "voices4"]
STEMS = VOICE_KEYS + ["music", "effects", "other"]
LABELS = {"voices": "Voices", "voices2": "Voice 2", "voices3": "Voice 3", "voices4": "Voice 4",
          "music": "Music", "effects": "Effects", "other": "Other"}
DEFAULT_PROMPTS = {"voices": "speech", "voices2": "", "music": "music", "effects": "sound effects"}


@dataclass
class Options:
    chunk_s: float = 30.0
    overlap_s: float = 2.0
    fast: bool = False             # 2 passes; "effects" = everything that isn't voices/music
    predict_spans: bool = False
    voice_cleanup: bool = False    # extra 'speech' pass on the leftover to catch voice bleed
    candidates: int = 1
    seed: int | None = 0
    prompts: dict = field(default_factory=lambda: dict(DEFAULT_PROMPTS))


def separate_stems(wav: np.ndarray, model: Separator, opts: Options = Options(),
                   progress: Callable[[float, str], None] | None = None) -> dict[str, np.ndarray]:
    """Return {'voices','music','effects','other'} mono arrays that sum back to `wav`.

    Per chunk: pass 1 extracts voices, pass 2 extracts music from pass 1's residual, pass 3
    extracts effects from pass 2's residual; whatever is left is 'other'. In fast mode the
    third pass is skipped and the leftover is reported as 'effects'.
    """
    n = len(wav)
    sr = audio.SR
    spans = plan_chunks(n, sr, opts.chunk_s, opts.overlap_s) if n > opts.chunk_s * sr else [(0, n)]
    overlap = int(round(opts.overlap_s * sr))
    out = {k: np.zeros(n, dtype=np.float32) for k in STEMS}
    # The voices prompt may hold several descriptions separated by ';' (e.g. "man speaking;
    # woman speaking"). Each one is its own pass over what's left, added to the Voices stem.
    vprompts = [p.strip() for p in opts.prompts["voices"].split(";") if p.strip()] or ["speech"]
    passes = [("voices", p) for p in vprompts]
    v2 = (opts.prompts.get("voices2") or "").strip()
    if v2:  # optional second voice on its own track, e.g. "woman speaking"
        passes.append(("voices2", v2))
    passes.append(("music", opts.prompts["music"]))
    if opts.voice_cleanup:
        # Sweep: with the music gone, re-check what's left for voices, one pass per voice prompt.
        passes += [("voices", p) for p in vprompts]
        if v2:
            passes.append(("voices2", v2))
    if not opts.fast:
        passes.append(("effects", opts.prompts["effects"]))
    total = len(spans) * len(passes)
    done = 0
    for ci, (s, e) in enumerate(spans):
        remaining = wav[s:e].astype(np.float32)
        first, last = ci == 0, ci == len(spans) - 1
        for name, prompt in passes:
            if progress:
                progress(done / total, f"Chunk {ci + 1}/{len(spans)}: isolating {name} ({prompt})")
            target, residual = model.separate(
                remaining, prompt, predict_spans=opts.predict_spans,
                candidates=opts.candidates, seed=None if opts.seed is None else opts.seed + ci)
            crossfade_add(out[name], target, s, first=first, last=last, overlap=overlap)  # 'voices' twice = cleanup pass adds to the same stem
            remaining = residual
            done += 1
        leftover_key = "effects" if opts.fast else "other"
        crossfade_add(out[leftover_key], remaining, s, first=first, last=last, overlap=overlap)
    # Guarantee exact additivity: absorb model residual error into the leftover stem.
    leftover_key = "effects" if opts.fast else "other"
    others = sum(out[k] for k in STEMS if k != leftover_key)
    out[leftover_key] = (wav - others).astype(np.float32)
    if progress:
        progress(1.0, "Done")
    return out


def split_voice(stems: dict[str, np.ndarray], model: Separator, prompt: str, opts: Options = Options(),
                progress: Callable[[float, str], None] | None = None) -> dict[str, np.ndarray]:
    """Take the 'voices' stem from a cinematic engine and pull one more voice ('voices2') out of it.

    Voices = what remains, so voices + voices2 still equals the original voices stem exactly.
    """
    v = stems["voices"].astype(np.float32)
    n, sr = len(v), audio.SR
    spans = plan_chunks(n, sr, opts.chunk_s, opts.overlap_s) if n > opts.chunk_s * sr else [(0, n)]
    overlap = int(round(opts.overlap_s * sr))
    v2 = np.zeros(n, dtype=np.float32)
    for ci, (s, e) in enumerate(spans):
        if progress:
            progress(ci / len(spans), f"Splitting voices: chunk {ci + 1}/{len(spans)} ({prompt})")
        target, _ = model.separate(v[s:e], prompt, predict_spans=opts.predict_spans, candidates=opts.candidates,
                                   seed=None if opts.seed is None else opts.seed + ci)
        crossfade_add(v2, target, s, first=ci == 0, last=ci == len(spans) - 1, overlap=overlap)
    out = dict(stems)
    out["voices2"] = v2
    out["voices"] = (v - v2).astype(np.float32)
    return out


# ---------- storage / mixing ----------

def save_session(stems: dict[str, np.ndarray], session_dir: Path, source_video: str | None,
                 source_name: str, labels: dict | None = None, meta: dict | None = None) -> dict:
    """Persist full-quality stems (.npy) plus browser-preview MP3s; return a manifest dict."""
    session_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"source_name": source_name, "source_video": source_video, "stems": []}
    n = len(next(iter(stems.values())))
    manifest["n_samples"] = n
    manifest["meta"] = meta or {}
    for k in STEMS:
        s = stems.get(k)
        peak = float(np.max(np.abs(s))) if s is not None and len(s) else 0.0
        if peak < 1e-4:  # empty stem: don't store it or show it (load_stems restores silence)
            continue
        np.save(session_dir / f"{k}.npy", s)
        preview = session_dir / f"{k}.preview.mp3"
        try:
            audio.write_audio(preview, s, audio.SR, bitrate="160k")
        except Exception:
            preview = session_dir / f"{k}.preview.wav"
            audio.write_audio(preview, s)
        manifest["stems"].append({"key": k, "label": (labels or LABELS).get(k, LABELS[k]), "preview": preview.name,
                                  "silent": False})
    manifest["duration"] = n / audio.SR
    (session_dir / "manifest.json").write_text(json.dumps(manifest))
    return manifest


def load_stems(session_dir: Path) -> dict[str, np.ndarray]:
    n = int(json.loads((session_dir / "manifest.json").read_text()).get("n_samples", 0))
    out = {}
    for k in STEMS:
        f = session_dir / f"{k}.npy"
        out[k] = np.load(f) if f.exists() else np.zeros(n, dtype=np.float32)
    return out


def voices_total(session_dir: Path) -> np.ndarray:
    """All voice tracks added together: the original Voices stem, whether or not it was split."""
    st = load_stems(session_dir)
    return sum(st[k] for k in VOICE_KEYS).astype(np.float32)


def save_voice_split(session_dir: Path, tracks: list[np.ndarray], segments: list | None = None) -> dict:
    """Replace the Voices stem in a saved session by one track per voice. Returns the new manifest."""
    mf = session_dir / "manifest.json"
    man = json.loads(mf.read_text())
    keep = {s["key"]: s for s in man["stems"] if s["key"] not in VOICE_KEYS}
    for k in VOICE_KEYS:  # drop the old voice files
        for f in session_dir.glob(f"{k}.*"):
            if f.name.startswith(f"{k}.") and f.suffix in (".npy", ".mp3", ".wav"):
                f.unlink()
    live_idx = [i for i, t in enumerate(tracks) if len(t) and float(np.max(np.abs(t))) >= 1e-4]
    live = [tracks[i] for i in live_idx]
    remap = {old: new for new, old in enumerate(live_idx)}  # empty voices are dropped, the rest renumbered
    segments = [[float(a), float(b), remap.get(int(v), 0)] for a, b, v in (segments or [])]
    entries = {}
    for i, t in enumerate(live[:len(VOICE_KEYS)]):
        k = VOICE_KEYS[i]
        np.save(session_dir / f"{k}.npy", t)
        preview = session_dir / f"{k}.preview.mp3"
        try:
            audio.write_audio(preview, t, audio.SR, bitrate="160k")
        except Exception:
            preview = session_dir / f"{k}.preview.wav"
            audio.write_audio(preview, t)
        label = f"Voice {i + 1}" if len(live) > 1 else "Voices"
        entries[k] = {"key": k, "label": label, "preview": preview.name, "silent": False}
    man["stems"] = [entries.get(k) or keep[k] for k in STEMS if k in entries or k in keep]
    man.setdefault("meta", {})["voice_split"] = {"voices": len(live), "segments": segments}
    mf.write_text(json.dumps(man))
    return man


def mix(stems: dict[str, np.ndarray], gains: dict[str, float]) -> np.ndarray:
    n = len(next(iter(stems.values())))
    out = np.zeros(n, dtype=np.float32)
    for k, g in gains.items():
        if k in stems and g > 0:
            out += stems[k] * float(g)
    peak = float(np.max(np.abs(out))) if n else 0.0
    if peak > 1.0:  # avoid clipping on export
        out /= peak
    return out


def apply_fx(stems: dict[str, np.ndarray], fxs: dict | None) -> dict[str, np.ndarray]:
    """Run each track's effects (settings from the mixer); tracks without effects are untouched."""
    from . import fx as _fx
    out = dict(stems)
    for k, p in (fxs or {}).items():
        if k in out and isinstance(p, dict) and not _fx.is_default(p):
            out[k] = _fx.apply(out[k], p)
    return out


def unique_path(p: Path) -> Path:
    """p, or 'name (2).ext', 'name (3).ext'... so an existing file is never replaced."""
    if not p.exists():
        return p
    i = 2
    while p.with_name(f"{p.stem} ({i}){p.suffix}").exists():
        i += 1
    return p.with_name(f"{p.stem} ({i}){p.suffix}")


def export(session_dir: Path, gains: dict[str, float], fmt: str, *, as_video: bool,
           out_dir: Path, fx: dict | None = None, unique: bool = False) -> Path:
    """Mix with the given gains and write a file (audio, or the source video re-muxed).

    unique=True never replaces an existing file: it picks 'name (2).ext' instead."""
    import tempfile

    manifest = json.loads((session_dir / "manifest.json").read_text())
    stems = apply_fx(load_stems(session_dir), fx)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = Path(manifest["source_name"]).stem or "mix"
    ext, cargs = audio.resolve_quality(fmt)
    pick = unique_path if unique else (lambda q: q)
    if not as_video:
        return audio.write_audio(pick(out_dir / f"{base}_mix.{ext}"), mix(stems, gains), codec_args=cargs)
    if not (manifest.get("source_video") and Path(manifest["source_video"]).exists()):
        raise FileNotFoundError("The original video for this result is no longer available.")
    src = Path(manifest["source_video"])
    with tempfile.TemporaryDirectory() as td:  # the lossless mix is only a stepping stone, so keep it out of the user's folder
        audio_path = audio.write_audio(Path(td) / f"{base}_mix.{ext}", mix(stems, gains), codec_args=cargs)
        return audio.remux_video(src, audio_path, pick(out_dir / f"{base}_mix{src.suffix or '.mp4'}"))


def export_stems_zip(session_dir: Path, out_dir: Path, fmt: str = "wav", fx: dict | None = None) -> tuple[Path, list[Path]]:
    import zipfile

    manifest = json.loads((session_dir / "manifest.json").read_text())
    stems = apply_fx(load_stems(session_dir), fx)
    base = Path(manifest["source_name"]).stem or "stems"
    out_dir.mkdir(parents=True, exist_ok=True)
    ext, cargs = audio.resolve_quality(fmt)
    names = {x["key"]: x["label"] for x in manifest["stems"]}
    files = [audio.write_audio(out_dir / f"{base}_{names.get(k, k).lower().replace(' ', '_')}.{ext}", stems[k],
                               codec_args=cargs) for k in STEMS if k in stems and np.max(np.abs(stems[k])) >= 1e-4]
    zpath = out_dir / f"{base}_stems.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_STORED) as z:
        for f in files:
            z.write(f, f.name)
    return zpath, files
