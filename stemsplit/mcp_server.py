"""MCP server for Un-Mix-a-Lot: lets Claude (or any MCP app) split, fix and mix audio on this PC.

Runs locally over stdio, so nothing leaves the computer. It uses the same engine (BandIt v2) and the same
saved-result format as the app, so everything it makes also shows up in the app's History.

Start it with run_mcp.py (the Settings tab in the app shows the exact line to paste into Claude Desktop).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path

import numpy as np
from mcp.server.fastmcp import Context, FastMCP

from . import audio, cinematic, pipeline, voices

ROOT = Path(__file__).resolve().parent.parent
MOCK = os.environ.get("STEMSPLIT_MOCK") == "1"
SECONDS_LIMIT_NOTE = "Long files are fine; they are processed in short chunks."

mcp = FastMCP(
    "un-mix-a-lot",
    instructions=(
        "Un-Mix-a-Lot splits audio or video into voices, music and sound effects on the user's own PC. "
        "Typical flow: separate_audio on a file path, then export_mix (for example with remove=['music']) "
        "to get a new audio file or the video with new audio. split_voices separates different speakers. "
        "Every result has a session_id; reuse it for later steps. Paths must be full paths on this computer."))


# ----------------------------------------------------------------------------- locations
def _settings() -> dict:
    try:
        return json.loads((ROOT / "settings.json").read_text())
    except Exception:
        return {}


def _docs_dir() -> Path:
    docs = Path.home() / "Documents"
    old, new = docs / "Stem Split", docs / "Un-Mix-a-Lot"
    return Path(os.environ.get("STEMSPLIT_EXPORT_DIR") or (old if old.exists() and not new.exists() else new))


def history_dir() -> Path:
    return Path(_settings().get("history_dir") or (_docs_dir() / "History"))


def exports_dir() -> Path:
    d = _docs_dir() / "From Claude"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _device() -> str:
    forced = os.environ.get("STEMSPLIT_DEVICE", "").strip().lower()
    if forced:
        return forced
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def _find_session(session_id: str) -> Path:
    sid = (session_id or "").strip()
    if not re.fullmatch(r"[0-9a-f]{8,32}", sid):
        raise ValueError("That isn't a valid session_id. Use one returned by separate_audio or list_results.")
    hits = sorted(history_dir().glob(f"*-{sid}")) if history_dir().exists() else []
    for h in hits:
        if (h / "manifest.json").exists():
            return h
    raise FileNotFoundError("No saved result with that session_id. Call list_results to see what exists.")


def _sid_of(folder: Path) -> str:
    return folder.name.split("-", 2)[-1]


def _db(x: np.ndarray) -> float:
    r = float(np.sqrt(np.mean(np.square(x)))) if len(x) else 0.0
    return round(20 * np.log10(r + 1e-9), 1)


def _describe(folder: Path) -> dict:
    man = json.loads((folder / "manifest.json").read_text())
    stems = pipeline.load_stems(folder)
    return {
        "session_id": _sid_of(folder),
        "source": man.get("source_name"),
        "seconds": round(man.get("duration", 0), 1),
        "has_video": bool(man.get("source_video") and Path(man["source_video"]).exists()),
        "tracks": {s["label"]: {"key": s["key"], "loudness_db": _db(stems[s["key"]])} for s in man["stems"]},
        "folder": str(folder),
    }


# ----------------------------------------------------------------------------- tools
_model: dict = {}


def _separator(progress):
    if "m" not in _model:
        if MOCK:
            _model["m"] = cinematic.MockCinematic()
        else:
            cinematic.download(progress)
            progress(0.0, "Loading the separation model")
            _model["m"] = cinematic.CinematicSeparator(_device())
    return _model["m"]


@mcp.tool()
async def separate_audio(file_path: str, ctx: Context) -> str:
    """Split an audio or video file into Voices, Music and Effects (BandIt v2, runs on this PC).

    file_path: full path to an audio or video file. Returns a session_id plus how loud each track is
    (a track far below the others, like -60 dB, is basically silent). The first run downloads the model
    (about 450 MB, once)."""
    path = Path(file_path).expanduser()
    if not path.is_file():
        return f"I can't find a file at {path}. Give the full path to a file on this computer."
    loop_state = {"last": 0.0}

    def prog(frac, desc):  # called from the worker thread
        loop_state["frac"], loop_state["desc"] = frac, desc

    import anyio

    def work():
        wav = audio.decode_mono(str(path))
        stems = _separator(prog).separate(wav, prog)
        return wav, stems

    task_result: dict = {}

    async def runner():
        task_result["v"] = await anyio.to_thread.run_sync(work)

    async with anyio.create_task_group() as tg:
        tg.start_soon(runner)
        while "v" not in task_result:
            await anyio.sleep(2)
            try:
                await ctx.report_progress(float(loop_state.get("frac", 0.0)), 1.0, loop_state.get("desc", "Working"))
            except Exception:
                pass
    wav, stems = task_result["v"]

    sid = uuid.uuid4().hex
    folder = history_dir() / f"{time.strftime('%Y%m%d-%H%M%S')}-{sid}"
    folder.mkdir(parents=True, exist_ok=True)
    src_video = None
    if audio.has_video(str(path)):
        src_video = str(shutil.copy(path, folder / ("source" + path.suffix)))
    pipeline.save_session(stems, folder, src_video, path.name, dict(pipeline.LABELS),
                          {"engine": "BandIt v2 (via MCP)", "created": time.time(), "name": path.name})
    return json.dumps(_describe(folder), indent=2)


def _parse_timeline(text: str) -> list[tuple[float, float, int]]:
    segs = []
    for i, line in enumerate(l.strip() for l in (text or "").splitlines()):
        if not line:
            continue
        m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s+voice\s+(\d)", line, flags=re.I)
        if not m:
            raise ValueError(f"Line {i + 1} isn't understood: '{line}'. Use the form: 0.00-4.50 voice 1")
        segs.append((float(m[1]), float(m[2]), int(m[3]) - 1))
    return segs


def _timeline(segs) -> str:
    return "\n".join(f"{a:.2f}-{b:.2f} voice {int(v) + 1}" for a, b, v in segs)


@mcp.tool()
async def split_voices(session_id: str, num_voices: int = 2, corrections: str = "", ctx: Context = None) -> str:
    """Split the Voices track of a result into separate speakers (2 to 4).

    Returns a timeline of who talks when, like '0.00-4.45 voice 1'. If it assigned someone wrongly, call again
    with `corrections` set to the edited timeline (same format, one line per block) to re-cut it instantly.
    It tells speakers apart by sound, so one person doing several voices may not split cleanly."""
    folder = _find_session(session_id)
    n = max(2, min(4, int(num_voices)))
    x = pipeline.voices_total(folder)
    if float(np.abs(x).max()) < 1e-4:
        return "There is no voice audio in this result to split."
    if corrections.strip():
        tracks, segs = voices.apply_segments(x, _parse_timeline(corrections), n)
    else:
        import anyio

        if not MOCK:
            await anyio.to_thread.run_sync(voices.ensure_packages)
        inject = dict(embed=voices.mock_embed, vad=voices.mock_vad) if MOCK else {}
        tracks, segs = await anyio.to_thread.run_sync(lambda: voices.split(x, n, None, **inject))
    man = pipeline.save_voice_split(folder, tracks, segs)
    found = sum(1 for s in man["stems"] if s["key"] in pipeline.VOICE_KEYS)
    out = _describe(folder)
    out["timeline"] = _timeline(man.get("meta", {}).get("voice_split", {}).get("segments", segs)).splitlines()
    out["note"] = ("Only one voice was found, so nothing was split." if found < 2
                   else f"Split into {found} voice tracks. Name them 'voice 1', 'voice 2' in export_mix.")
    return json.dumps(out, indent=2)


def _gains_from(folder: Path, remove: list[str], gains: dict | None) -> dict[str, float]:
    man = json.loads((folder / "manifest.json").read_text())
    names = {}
    for s in man["stems"]:
        k, label = s["key"], s["label"].lower()
        names[label] = k
        names[k] = k
    names["voice"] = "voices"
    names["voice 1"] = names.get("voice 1", "voices")
    names["voice 2"] = names.get("voice 2", "voices2")
    names["voice 3"] = names.get("voice 3", "voices3")
    names["voice 4"] = names.get("voice 4", "voices4")
    present = {s["key"] for s in man["stems"]}
    out = {k: 1.0 for k in present}

    def key_of(name: str) -> str:
        k = names.get(str(name).strip().lower())
        if not k or k not in present:
            raise ValueError(f"There is no track called '{name}'. Available: "
                             + ", ".join(sorted({s['label'].lower() for s in man['stems']})))
        return k

    known = {"voices", "voice", "music", "effects", "other", "voice 1", "voice 2", "voice 3", "voice 4"}
    for name in remove or []:
        nm = str(name).strip().lower()
        if nm in known and names.get(nm) not in present:
            continue  # e.g. "remove music" on a clip that has none: nothing to remove, not an error
        out[key_of(name)] = 0.0
    for name, g in (gains or {}).items():
        out[key_of(name)] = min(2.0, max(0.0, float(g)))
    if not any(out.values()):
        raise ValueError("Everything is muted, so there would be nothing to export.")
    return out


def _out_folder(save_to: str, folder: Path) -> Path:
    if save_to and save_to.strip():
        d = Path(save_to.strip()).expanduser()
        if d.suffix:  # looks like a file name, not a folder
            raise ValueError("save_to must be a folder, not a file name.")
        d.mkdir(parents=True, exist_ok=True)
        return d
    return exports_dir() / _sid_of(folder)[:8]


def _export_one(folder: Path, remove, gains, as_video: bool, fmt: str, save_to: str) -> dict:
    from . import fx as fxmod

    g = _gains_from(folder, remove or [], gains)
    out = pipeline.export(folder, g, "WAV 24-bit" if as_video else fmt, as_video=as_video,
                          out_dir=_out_folder(save_to, folder), fx=fxmod.load(folder), unique=True)
    return {"saved_to": str(out), "tracks_used": {k: v for k, v in g.items() if v > 0}}


@mcp.tool()
async def export_mix(session_id: str, remove: list[str] | None = None, gains: dict[str, float] | None = None,
                     as_video: bool = False, format: str = "WAV 24-bit", save_to: str = "") -> str:
    """Mix a result's tracks back together and save a file (audio, or the original video with the new audio).

    remove: tracks to drop, e.g. ["music"] or ["music", "effects"] or ["voice 2"].
    gains: optional volume per track, 0 to 2 (1 = unchanged), e.g. {"music": 0.3}.
    as_video: true to get the original video with the new audio (only if the source was a video).
    format: 'WAV 24-bit', 'WAV 16-bit', 'FLAC 24-bit', 'MP3 320 kbps', 'MP3 192 kbps', etc.
    save_to: a folder to put the file in (created if needed), e.g. the user's Desktop. Empty = the default
    'From Claude' folder in Documents/Un-Mix-a-Lot. An existing file with the same name is never replaced (a number is added).
    Track names: voices, music, effects, other, or voice 1..4 after split_voices."""
    return json.dumps(_export_one(_find_session(session_id), remove, gains, as_video, format, save_to), indent=2)


@mcp.tool()
async def export_stems(session_id: str, format: str = "WAV 24-bit") -> str:
    """Save every track of a result as its own audio file (voices, music, effects, and any split voices)."""
    folder = _find_session(session_id)
    z, files = pipeline.export_stems_zip(folder, exports_dir() / _sid_of(folder)[:8], format)
    return json.dumps({"files": [str(f) for f in files], "zip": str(z)}, indent=2)


MEDIA_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


def _search_roots() -> list[Path]:
    h = Path.home()
    roots = [h / n for n in ("Desktop", "Downloads", "Documents", "Videos", "Movies", "Music")]
    od = [p for p in h.glob("OneDrive*") if p.is_dir()]
    roots += [r / n for r in od for n in ("Desktop", "Documents")]
    return [r for r in roots if r.is_dir()]


def _walk(root: Path, depth: int):
    """Files under root, at most `depth` folders down (keeps searches fast)."""
    try:
        for e in root.iterdir():
            if e.is_file():
                yield e
            elif e.is_dir() and depth > 0 and not e.name.startswith(".") and e.name.lower() not in ("node_modules", "appdata"):
                yield from _walk(e, depth - 1)
    except OSError:
        return


@mcp.tool()
async def find_files(name: str = "", folder: str = "", only_media: bool = True) -> str:
    """Find audio/video files on this computer so you can use them with separate_audio.

    name: part of a file name (e.g. 'Solara'). folder: a full folder path to list, OR just a folder name
    (e.g. 'Scenes') to look for among Desktop, Downloads, Documents and Videos. With only a folder, every
    media file inside it is listed. Returns full paths, newest first (max 50)."""
    folders: list[Path] = []
    if folder.strip():
        f = Path(folder.strip()).expanduser()
        if f.is_dir():
            folders = [f]
        else:
            want = folder.strip().lower()
            for r in _search_roots():
                if r.name.lower() == want:
                    folders.append(r)
            for r in _search_roots():
                try:
                    folders += [e for e in r.iterdir() if e.is_dir() and e.name.lower() == want]
                    for sub in (e for e in r.iterdir() if e.is_dir() and not e.name.startswith(".")):
                        folders += [e for e in sub.iterdir() if e.is_dir() and e.name.lower() == want]
                except OSError:
                    continue
            if not folders:
                return f"I couldn't find a folder called '{folder}'. Ask for its full path."
    else:
        folders = _search_roots()
    folders = list(dict.fromkeys(folders))
    q = name.strip().lower()
    hits = [p for r in folders for p in _walk(r, 3 if not folder.strip() else 1)
            if (not q or q in p.name.lower()) and (not only_media or p.suffix.lower() in MEDIA_EXT)]
    hits = sorted({p: p.stat().st_mtime for p in hits}.items(), key=lambda kv: -kv[1])[:50]
    if not hits:
        return "No matching files found."
    return json.dumps([{"path": str(p), "mb": round(p.stat().st_size / 1e6, 1)} for p, _ in hits], indent=2)


@mcp.tool()
async def process_folder(folder: str, remove: list[str], save_to: str = "", as_video: bool = True,
                         format: str = "WAV 24-bit", ctx: Context = None) -> str:
    """Do separate + export for EVERY audio/video file in a folder (not subfolders), e.g. remove the music.

    folder: full path of the folder (use find_files first if you only know its name).
    remove: tracks to drop from each file, e.g. ["music"]. save_to: where finished files go; empty = a new
    'Un-Mixed' folder inside `folder`. Originals are never changed or deleted. Videos come back as videos
    (as_video true); audio files come back as audio. This can take a while for many files: tell the user.
    Files that fail are listed at the end and the rest continue."""
    src = Path(folder).expanduser()
    if not src.is_dir():
        return f"I can't find a folder at {src}."
    files = sorted(p for p in src.iterdir() if p.is_file() and p.suffix.lower() in MEDIA_EXT
                   and not p.name.startswith("."))
    if not files:
        return "There are no audio or video files in that folder."
    dest = save_to.strip() or str(src / "Un-Mixed")
    done, failed = [], []
    for i, f in enumerate(files):
        try:
            if ctx:
                await ctx.report_progress(i, len(files), f"{i + 1} of {len(files)}: {f.name}")
        except Exception:
            pass
        try:
            sep = json.loads(await separate_audio(str(f), ctx))
            res = _export_one(_find_session(sep["session_id"]), remove, None,
                              as_video and f.suffix.lower() in VIDEO_EXT, format, dest)
            done.append({"file": f.name, "saved_to": res["saved_to"], "session_id": sep["session_id"]})
        except Exception as e:  # keep going; report at the end
            failed.append({"file": f.name, "error": str(e)[:200]})
    return json.dumps({"processed": len(done), "failed": failed, "results": done, "output_folder": dest}, indent=2)


@mcp.tool()
async def list_results() -> str:
    """List results already made (newest first), including ones made in the Un-Mix-a-Lot app."""
    hd = history_dir()
    rows = []
    for f in sorted(hd.glob("*-*"), reverse=True)[:30] if hd.exists() else []:
        if (f / "manifest.json").exists():
            try:
                d = _describe(f)
                rows.append({"session_id": d["session_id"], "source": d["source"], "seconds": d["seconds"],
                             "tracks": list(d["tracks"])})
            except Exception:
                continue
    return json.dumps(rows, indent=2) if rows else "No results yet. Call separate_audio on a file first."


def main():
    mcp.run()
