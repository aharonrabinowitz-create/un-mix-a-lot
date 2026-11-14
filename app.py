"""Un-Mix-a-Lot: a simple Gradio UI for Meta's SAM Audio.

One codebase, two modes:
  * Local    : `python app.py` on a machine with an NVIDIA GPU.
  * ZeroGPU  : deploy as a Hugging Face Space (SDK: gradio). Detected via the SPACE_ID env var.
"""
from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading

# python.org Python on a Mac ships without root certificates; use certifi's so downloads work.
try:
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
except Exception:
    pass
import time
import uuid
import webbrowser
from pathlib import Path
from urllib.parse import quote

import gradio as gr

from stemsplit import __version__, audio, claude_config, cinematic, fx as fxmod, hardware, hfaccess, mvsep, pipeline, updates, voices
from stemsplit.fxjs import FX_JS
from stemsplit.mixer import MIXER_CSS, MIXER_HTML, MIXER_JS
from stemsplit.separator import MODEL_IDS, get_separator

try:
    from huggingface_hub import constants as hf_constants
except Exception:  # pragma: no cover
    hf_constants = None

# ----------------------------------------------------------------------------- mode / config
try:
    import spaces  # only exists on Hugging Face
except ImportError:
    spaces = None
ZERO = spaces is not None and bool(os.environ.get("SPACE_ID"))
MOCK = os.environ.get("STEMSPLIT_MOCK") == "1"
DESKTOP = os.environ.get("STEMSPLIT_DESKTOP") == "1"  # running inside the native window

MODELS = [m.strip() for m in os.environ.get("STEMSPLIT_MODELS", "small" if ZERO else "small,base,large").split(",")
          if m.strip() in MODEL_IDS]
MAX_SECONDS = float(os.environ.get("STEMSPLIT_MAX_SECONDS", 60 if ZERO else 0))  # 0 = unlimited
SEC_PER_PASS = float(os.environ.get("STEMSPLIT_SEC_PER_PASS", 8))  # ZeroGPU duration estimate; calibrate!
MAX_GPU_SECONDS = int(os.environ.get("STEMSPLIT_MAX_GPU_SECONDS", 120))

WORK = Path(os.environ.get("STEMSPLIT_WORKDIR", Path(tempfile.gettempdir()) / "stemsplit"))
WORK.mkdir(parents=True, exist_ok=True)
SESSION_TTL = 6 * 3600
# Where exports go in the desktop app (browser downloads don't fit a native window well).
def _default_export_dir() -> Path:
    docs = Path.home() / "Documents"
    old = docs / "Stem Split"  # the folder used before the app was renamed: keep using it if it's already there
    new = docs / "Un-Mix-a-Lot"
    return old if old.exists() and not new.exists() else new


EXPORT_DIR = Path(os.environ.get("STEMSPLIT_EXPORT_DIR") or _default_export_dir())


def device() -> str:
    forced = os.environ.get("STEMSPLIT_DEVICE", "").strip().lower()  # e.g. "mps" to try an Apple chip's GPU (experimental)
    if forced:
        return forced
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def gpu(duration):
    """@spaces.GPU on ZeroGPU, a no-op everywhere else."""
    return spaces.GPU(duration=duration) if ZERO else (lambda f: f)


def _n_passes(o):  # noqa: ANN001
    n_voice = len([p for p in o["prompts"]["voices"].split(";") if p.strip()]) or 1
    voices = n_voice + (1 if o["prompts"].get("voices2") else 0)
    return voices * (2 if o.get("voice_cleanup") else 1) + 1 + (0 if o["fast"] else 1)


def _gpu_duration(wav, size, o):  # same signature as run_model
    n_chunks = max(1, int(len(wav) / audio.SR / max(1.0, o["chunk_s"] - o["overlap_s"])) + 1)
    return int(min(MAX_GPU_SECONDS, 15 + n_chunks * _n_passes(o) * SEC_PER_PASS))


@gpu(duration=_gpu_duration)
def run_model(wav, size, o, progress=None):
    """The only function that touches the GPU. On ZeroGPU it runs inside a GPU slot."""
    if progress:
        progress(0.0, "Loading model (first load takes a minute)…")
    model = get_separator(size, rankers=o["candidates"] > 1, spans=o["predict_spans"],
                          evict=not ZERO).to(device())
    opts = pipeline.Options(**{k: v for k, v in o.items()})
    return pipeline.separate_stems(wav, model, opts, progress)


ENGINE_SAM = "SAM Audio (Local)"
ENGINE_LOCAL = "BandIt v2 (Local)"
ENGINE_CLOUD = "DnR v3 (Cloud, MVSEP)"
ENGINE_CLOUD2 = "BandIt v2 (Cloud, MVSEP)"
CLOUD_ENGINES = {ENGINE_CLOUD: "dnr", ENGINE_CLOUD2: "bandit"}
HW = hardware.detect()
SAM_OK = ZERO or MOCK or HW["sam_ok"]
ENGINES = [ENGINE_SAM] if ZERO else [ENGINE_LOCAL, ENGINE_CLOUD2, ENGINE_CLOUD] + ([ENGINE_SAM] if SAM_OK else [])
_cin: dict = {}


def _cinematic_model(progress):
    if MOCK:
        return cinematic.MockCinematic()
    if "m" not in _cin:
        cinematic.download(progress)
        if progress:
            progress(0.0, "Loading BandIt v2…")
        _cin["m"] = cinematic.CinematicSeparator(device())
    return _cin["m"]


def run_engine(wav, engine, size, o, token, progress=None, cancelled=None):
    """Dispatch to the chosen engine, then optionally pull a 2nd voice out with SAM Audio."""
    if engine == ENGINE_SAM or ZERO:
        return run_model(wav, size, o, progress)
    if engine == ENGINE_LOCAL:
        stems = _cinematic_model(progress).separate(wav, progress)
    else:
        stems = (cinematic.MockCinematic().separate(wav, progress) if MOCK
                 else mvsep.separate(wav, token, progress, cancelled=cancelled,
                                     model=CLOUD_ENGINES[engine]))
    if o["prompts"]["voices2"]:  # chain: SAM Audio splits the clean voices stem into Voice 1 / Voice 2
        if progress:
            progress(0.0, "Loading SAM Audio to split the voices…")
        model = get_separator(size, spans=o["predict_spans"], evict=True).to(device())
        stems = pipeline.split_voice(stems, model, o["prompts"]["voices2"], pipeline.Options(**o), progress)
    return stems


def preload():
    """ZeroGPU: load weights on CPU at startup (the GPU is attached per call)."""
    if ZERO and not MOCK:
        for m in MODELS:
            get_separator(m, evict=False).to("cuda")


# ----------------------------------------------------------------------------- status / progress
# One status record per browser session, polled by a timer and drawn as a progress bar. This replaces
# Gradio's raw progress text (which dumped download-bar characters into the page).
PROG: dict[str, dict] = {}
IDLE = "Add a file, then press Separate."


def _p(cid, text, frac=None, *, active=True, kind="info", phase="run", action=""):
    PROG[cid or "?"] = dict(text=text, frac=frac, active=active, kind=kind, phase=phase, t=time.time(), action=action)


def hf_cache_bytes() -> int:
    """Size of the downloaded SAM Audio weights; its growth shows first-time download progress."""
    try:
        root = Path(hf_constants.HF_HUB_CACHE)
        return sum(f.stat().st_size for d in root.glob("models--facebook--sam-audio*")
                   for f in (d / "blobs").glob("*") if f.is_file())
    except Exception:
        return 0


def render_status(cid):
    p = PROG.get(cid or "?") or dict(text=IDLE, frac=None, active=False, kind="info", phase="")
    text = html.escape(p["text"])
    if p["active"]:
        if p.get("phase") == "loading":
            grown = hf_cache_bytes() - p.get("bytes0", 0)
            if grown > 5_000_000:
                text += f"<br>Downloading models (one-time): {grown / 1e9:.1f} GB so far. This can take a while."
            text += f"<br><small>{int(time.time() - p['t'])} s</small>"
        frac = p.get("frac")
        bar = (f'<div class="pg"><div class="pgb" style="width:{int(max(0, min(1, frac)) * 100)}%"></div></div>'
               if frac is not None else '<div class="pg ind"><div class="pgb"></div></div>')
        return f'<div class="stat">{bar}<div class="pt">{text}</div></div>'
    cls = {"ok": "ok", "err": "err"}.get(p["kind"], "")
    if p.get("action") == "sam_setup":
        text += ('<br><a href="#" style="color:inherit;text-decoration:underline" onclick="'
                 "document.getElementById('settings-modal').classList.add('open');"
                 "[].forEach.call(document.querySelectorAll('#settings-modal button'),function(b){if(b.textContent.trim()=='Model')b.click()});"
                 "return false;\">Open the SAM Audio setup</a>")
    return f'<div class="stat {cls}"><div class="pg idle"><div class="pgb"></div></div><div class="pt">{text}</div></div>'


# ----------------------------------------------------------------------------- handlers
def _gc():
    now = time.time()
    for d in WORK.iterdir():
        try:
            if d.is_dir() and now - d.stat().st_mtime > SESSION_TTL:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def _fail(cid, msg, action=""):
    _p(cid, msg, active=False, kind="err", action=action)
    raise gr.Error(msg)


# ----------------------------------------------------------------------------- settings (settings.json next to the app)
SETTINGS_FILE = Path(__file__).resolve().parent / "settings.json"
HEX = re.compile(r"[0-9a-f]{32}")


def get_setting(key: str, default: str = "") -> str:
    try:
        return str(json.loads(SETTINGS_FILE.read_text()).get(key, default))
    except Exception:
        return default


def set_setting(key: str, value: str):
    try:
        data = json.loads(SETTINGS_FILE.read_text()) if SETTINGS_FILE.exists() else {}
        data[key] = value
        SETTINGS_FILE.write_text(json.dumps(data, indent=2))
    except Exception:
        pass


def keep_history() -> bool:
    return not ZERO and get_setting("keep_history", "1") == "1"


def history_dir() -> Path:
    return Path(get_setting("history_dir", str(EXPORT_DIR / "History")))


def _msg(kind: str, text: str) -> str:
    return f'<div class="setmsg {kind}">{html.escape(text)}</div>'


def _last_dir() -> Path:
    """Where the user last saved an export (falls back to Documents\\Un-Mix-a-Lot)."""
    try:
        p = Path(get_setting("last_export_dir"))
        if p.is_dir():
            return p
    except Exception:
        pass
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    return EXPORT_DIR


def _remember_dir(p: Path):
    set_setting("last_export_dir", str(p))


def _dialog(kind: str, **kw):
    """Native Save-as / Choose-folder dialog in the app window. Returns a path string or None."""
    import webview
    win = webview.windows[0]
    fd = getattr(webview, "FileDialog", None)
    mode = (fd.SAVE if kind == "save" else fd.FOLDER) if fd else (
        webview.SAVE_DIALOG if kind == "save" else webview.FOLDER_DIALOG)
    r = win.create_file_dialog(mode, **kw)
    if not r:
        return None
    return str(r if isinstance(r, str) else r[0])


# ----------------------------------------------------------------------------- sessions and history
def _find_session(sid: str) -> Path | None:
    if not HEX.fullmatch(sid or ""):
        return None
    d = WORK / sid
    if (d / "manifest.json").exists():
        return d
    for h in history_dir().glob(f"*-{sid}"):
        if (h / "manifest.json").exists():
            return h
    return None


def _session_dir(cid, sid: str) -> Path:
    if not HEX.fullmatch(sid or ""):
        _fail(cid, "No separation yet. Add a file and press Separate first.")
    d = _find_session(sid)
    if d is None:
        _fail(cid, "That result is no longer available. Please run the separation again.")
    return d


def _out_dir(sid: str) -> Path:
    d = WORK / "exports" / sid
    d.mkdir(parents=True, exist_ok=True)
    return d


def _url(p: Path) -> str:
    try:  # the version stops the browser showing an old copy after a file is rewritten (voice split)
        v = int(p.stat().st_mtime)
    except OSError:
        v = 0
    return "/gradio_api/file=" + quote(p.as_posix(), safe="/:") + f"?v={v}"


def _payload(sd: Path) -> tuple[str, bool]:
    """JSON the mixer loads, plus whether this result still has its source video."""
    man = json.loads((sd / "manifest.json").read_text())
    vid = next(iter(sorted(sd.glob("videoprev.*"))), None)
    vs = (man.get("meta") or {}).get("voice_split") or {}
    stems = [dict(s, url=_url(sd / s["preview"])) for s in man["stems"]]
    orig = (man.get("original") or {}).get("preview")
    if orig and (sd / orig).exists():  # the untouched original, hidden until "Original" is pressed
        stems.append({"key": "original", "label": "Original", "silent": False, "ghost": True, "url": _url(sd / orig)})
    payload = {"duration": man["duration"], "video_url": _url(vid) if vid else None,
               "stems": stems,
               "segments": vs.get("segments") or [], "nvoices": vs.get("voices", 0),
               "fx": fxmod.load(sd)}
    has_video = bool(man.get("source_video") and Path(man["source_video"]).exists())
    return json.dumps(payload), has_video


def _dir_mb(d: Path) -> float:
    try:
        return sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) / 1e6
    except OSError:
        return 0.0


def _entries() -> list[dict]:
    """Past results: saved history first, plus recent temporary ones from this session."""
    found: dict[str, dict] = {}
    roots = [(history_dir(), True), (WORK, False)]
    for root, saved in roots:
        if not root.is_dir():
            continue
        for d in root.iterdir():
            mf = d / "manifest.json"
            sid = d.name[-32:]
            if not (d.is_dir() and mf.exists() and HEX.fullmatch(sid)) or sid in found:
                continue
            try:
                man = json.loads(mf.read_text())
            except Exception:
                continue
            meta = man.get("meta") or {}
            found[sid] = dict(sid=sid, dir=d, saved=saved, name=meta.get("name") or man.get("source_name", "?"),
                              engine=meta.get("engine", ""), created=meta.get("created") or d.stat().st_mtime,
                              dur=man.get("duration", 0), mb=_dir_mb(d))
    return sorted(found.values(), key=lambda e: e["created"], reverse=True)


def _hist_view():
    es = _entries()
    choices = []
    for e in es:
        when = time.strftime("%b %d, %I:%M %p", time.localtime(e["created"])).replace(" 0", " ")
        label = f'{e["name"]} · {e["engine"]} · {when} · {e["dur"]:.0f} s · {e["mb"]:.0f} MB' + ("" if e["saved"] else " · not saved")
        choices.append((label, e["sid"]))
    saved = [e for e in es if e["saved"]]
    info = (f'{len(saved)} saved result(s), {sum(e["mb"] for e in saved):.0f} MB in {history_dir()}'
            if choices else "No results yet. Run a separation and it will show up here.")
    if keep_history() is False and not ZERO:
        info += " History is off, so new results are not saved."
    return gr.update(choices=choices, value=None), info


def on_hist_refresh():
    return _hist_view()


def on_hist_open(cid, sel):
    if not sel:
        _fail(cid, "Pick a result in the list first.")
    sd = _find_session(sel)
    if sd is None:
        _fail(cid, "That result can't be found any more.")
    if sd.parent != WORK:  # bring it into the work folder so the mixer can serve it
        shutil.copytree(sd, WORK / sel, dirs_exist_ok=True)
        sd = WORK / sel
    payload, has_video = _payload(sd)
    _p(cid, "Opened a saved result.", active=False, kind="ok")
    return payload, sel, gr.update(visible=has_video)


def on_hist_delete(cid, sel):
    if sel:
        for root in (WORK / sel, *history_dir().glob(f"*-{sel}")):
            shutil.rmtree(root, ignore_errors=True)
        gr.Info("Deleted.")
    return _hist_view()


# ----------------------------------------------------------------------------- instant preview
PRE: dict[str, dict] = {}  # browser session -> {"path": the loaded file, "dir": its preview folder}


def _write_preview(wav, d: Path) -> Path:
    prev = d / "original.preview.mp3"
    try:
        audio.write_audio(prev, wav, audio.SR, bitrate="160k")
    except Exception:
        prev = d / "original.preview.wav"
        audio.write_audio(prev, wav)
    return prev


def on_file_loaded(cid, file):
    """A file was dropped in: show it in the player right away, with one waveform row for its audio."""
    if not file:
        PRE.pop(cid, None)
        return "", ""
    path = file if isinstance(file, str) else file.name
    _gc()
    _p(cid, "Loading your file…")
    try:
        wav = audio.decode_mono(path)
    except Exception as e:
        _fail(cid, f"Couldn't read that file: {e}")
    d = WORK / ("pre-" + uuid.uuid4().hex)
    d.mkdir(parents=True, exist_ok=True)
    prev = _write_preview(wav, d)
    vid = None
    if audio.has_video(path):
        _p(cid, "Preparing the video preview…")
        try:
            vid = audio.make_video_preview(path, d / "videoprev",
                                           codec=os.environ.get("STEMSPLIT_PREVIEW_CODEC", "h264"))
        except Exception:
            vid = None  # no preview video; the audio waveform still works
    PRE[cid] = dict(path=path, dir=d)
    payload = {"duration": len(wav) / audio.SR, "video_url": _url(vid) if vid else None, "preview": True,
               "stems": [{"key": "original", "label": "Original", "silent": False, "url": _url(prev)}],
               "segments": [], "nvoices": 0, "fx": {}}
    _p(cid, "Loaded. Press Separate to split it into voices, music and effects.", active=False, kind="ok")
    return json.dumps(payload), ""


def on_file_cleared(cid):
    PRE.pop(cid, None)
    return "", ""


# ----------------------------------------------------------------------------- separate
CANCEL: dict[str, threading.Event] = {}


def on_separate(cid, file, engine, size, fast, chunk_s, overlap_s, p_voices, p_music, p_effects, spans,
                cands, seed, cleanup):
    ev = CANCEL.setdefault(cid, threading.Event())
    ev.clear()
    if not file:
        _fail(cid, "Add an audio or video file first.")
    path = file if isinstance(file, str) else file.name
    _gc()
    _p(cid, "Reading file…")
    try:
        wav = audio.decode_mono(path)
    except Exception as e:
        _fail(cid, f"Couldn't read that file: {e}")
    secs = len(wav) / audio.SR
    if MAX_SECONDS and secs > MAX_SECONDS:
        _fail(cid, f"This demo is limited to {int(MAX_SECONDS)} s per file (yours is {secs:.0f} s). "
                   "Trim it, or run the app locally for long files.")
    if engine in CLOUD_ENGINES and not MOCK and not get_setting("mvsep_token"):
        _fail(cid, "Add your MVSEP token first: Settings, Model tab, Cloud (MVSEP).")
    if engine == ENGINE_SAM and not ZERO and (not MOCK or os.environ.get("STEMSPLIT_MOCK_HF")) \
            and hfaccess.needs_setup(size):
        _fail(cid, hfaccess.SETUP_HINT, "sam_setup")
    if overlap_s * 2 >= chunk_s:
        _fail(cid, "Chunk length must be more than twice the crossfade.")
    if ZERO:  # keep the shared demo cheap: no optional heavy parts
        spans, cands = False, 1
    engine = ENGINE_SAM if ZERO else engine
    o = dict(chunk_s=float(chunk_s), overlap_s=float(overlap_s), fast=bool(fast),
             predict_spans=bool(spans), candidates=int(cands), seed=int(seed), voice_cleanup=bool(cleanup),
             prompts={"voices": p_voices.strip() or "speech", "voices2": "",  # second-voice split is parked
                      "music": p_music.strip() or "music",
                      "effects": p_effects.strip() or "sound effects"})
    t = time.time()
    if engine in CLOUD_ENGINES:
        _p(cid, "Uploading your audio to mvsep.com…")

    def cb(frac, desc):  # progress from the engines
        if ev.is_set():
            raise mvsep.Cancelled("Cancelled.")
        if desc.startswith("Loading"):
            _p(cid, desc, None, phase="loading")
            PROG[cid]["bytes0"] = bytes0
        else:
            _p(cid, desc, 0.05 + 0.9 * frac)

    bytes0 = hf_cache_bytes()
    try:
        if ZERO:  # progress callbacks can't cross into the GPU worker process
            _p(cid, "Waiting for a GPU slot and separating…")
            stems = run_model(wav, size, o)
        else:
            stems = run_engine(wav, engine, size, o, get_setting("mvsep_token"), cb, ev.is_set)
    except mvsep.Cancelled as e:
        _p(cid, str(e) or "Cancelled.", active=False)
        gr.Info(str(e) or "Cancelled.")
        return gr.skip(), gr.skip(), gr.skip()
    except Exception as e:
        if engine == ENGINE_SAM and not ZERO and hfaccess.is_access_error(e):
            _fail(cid, "Meta hasn't approved your access to this SAM Audio model yet. "
                       "Check the setup, or wait for their approval email.", "sam_setup")
        _fail(cid, f"Separation failed: {e}")
    _p(cid, "Preparing mixer…", 0.97)
    sid = uuid.uuid4().hex
    sd = WORK / sid
    sd.mkdir(parents=True, exist_ok=True)
    src_video = None
    if audio.has_video(path):  # keep our own copy; Gradio's upload cache may be cleaned
        src_video = str(shutil.copy(path, sd / ("source" + Path(path).suffix)))
    used = {ENGINE_SAM: f"SAM Audio ({size})", ENGINE_LOCAL: "BandIt v2", ENGINE_CLOUD: "MVSEP DnR v3",
            ENGINE_CLOUD2: "MVSEP BandIt v2"}[engine]
    pipeline.save_session(stems, sd, src_video, Path(path).name, dict(pipeline.LABELS),
                          {"engine": used, "created": time.time(), "name": Path(path).name})
    try:  # keep the untouched original so it can be played back for comparison
        orig = _write_preview(wav, sd)
        mf = sd / "manifest.json"
        man = json.loads(mf.read_text())
        man["original"] = {"preview": orig.name}
        mf.write_text(json.dumps(man))
    except Exception:
        pass
    if src_video:
        pre = PRE.get(cid)
        reused = False
        if pre and pre["path"] == path:  # the video preview was already made when the file was loaded
            for f in Path(pre["dir"]).glob("videoprev.*"):
                shutil.copy(f, sd / f.name)
                reused = True
        if not reused:
            _p(cid, "Preparing video preview…", 0.98)
            try:
                audio.make_video_preview(src_video, sd / "videoprev",
                                         codec=os.environ.get("STEMSPLIT_PREVIEW_CODEC", "h264"))
            except Exception:
                pass  # no preview video; audio-only mixer still works
    saved_note = ""
    if keep_history():
        try:
            hd = history_dir()
            hd.mkdir(parents=True, exist_ok=True)
            shutil.copytree(sd, hd / f"{time.strftime('%Y%m%d-%H%M%S')}-{sid}")
            saved_note = " Saved to History."
        except Exception as e:  # history must never lose the result itself
            saved_note = f" (Couldn't save to History: {e})"
    payload, has_video = _payload(sd)
    _p(cid, f"Done: {secs:.0f} s of audio in {time.time() - t:.0f} s ({used}).{saved_note}",
       active=False, kind="ok")
    return payload, sid, gr.update(visible=has_video)


def _mirror_history(sd: Path, sid: str):
    """After the tracks changed (voice split, picked sound), update the History copy too."""
    pats = ("*.npy", "*.preview.*")
    for h in history_dir().glob(f"*-{sid}"):
        if not (h / "manifest.json").exists():
            continue
        try:
            keep = {f.name for pat in pats for f in sd.glob(pat)}
            for pat in pats:
                for f in h.glob(pat):
                    if f.name not in keep:
                        f.unlink()
            for name in keep | {"manifest.json"}:
                shutil.copy(sd / name, h / name)
        except OSError:
            pass


def on_split_voices(cid, sid, n):
    sd = _session_dir(cid, sid)
    n = int(n)
    _p(cid, "Preparing the voice split…", 0.02)
    try:
        if not MOCK:
            voices.ensure_packages(lambda f, t: _p(cid, t, None, phase="loading"))
        x = pipeline.voices_total(sd)
        if float(abs(x).max()) < 1e-4:
            _fail(cid, "There is no voice audio in this result to split.")
        inject = dict(embed=voices.mock_embed, vad=voices.mock_vad) if MOCK else {}
        tracks, segs = voices.split(x, n, lambda f, t: _p(cid, t, 0.05 + 0.9 * f), **inject)
        man = pipeline.save_voice_split(sd, tracks, segs)
    except gr.Error:
        raise
    except Exception as e:
        _fail(cid, f"Voice split failed: {e}")
    _mirror_history(sd, sid)
    found = sum(1 for s in man["stems"] if s["key"] in pipeline.VOICE_KEYS)
    payload, _ = _payload(sd)
    if found < 2:
        _p(cid, "Only one voice was found, so nothing was split.", active=False, kind="info")
    else:
        _p(cid, f"Split the voices into {found} tracks. Listen, and mute or solo each one.", active=False, kind="ok")
    return payload


def on_edit_voices(cid, sid, raw):
    """The user moved a boundary or reassigned a block in the "Who's talking" strip: re-cut instantly."""
    sd = _session_dir(cid, sid)
    try:
        data = json.loads(raw or "{}")
        segs = [(float(a), float(b), int(v)) for a, b, v in data["segments"]]
        n = int(data.get("n", 2))
        tracks, clean = voices.apply_segments(pipeline.voices_total(sd), segs, n)
        man = pipeline.save_voice_split(sd, tracks, clean)
    except Exception as e:
        _fail(cid, f"Couldn't apply that change: {e}")
    _mirror_history(sd, sid)
    payload, _ = _payload(sd)
    found = sum(1 for s in man["stems"] if s["key"] in pipeline.VOICE_KEYS)
    _p(cid, f"Updated. {found} voice track(s).", active=False, kind="ok")
    return payload


def on_cancel(cid):
    CANCEL.setdefault(cid, threading.Event()).set()
    _p(cid, "Cancelling… (it stops at the next step)")


# ----------------------------------------------------------------------------- export
def _gains(cid, raw: str) -> dict:
    try:
        g = json.loads(raw or "{}")
    except json.JSONDecodeError:
        g = {}
    out = {k: min(2.0, max(0.0, float(g[k]))) for k in pipeline.STEMS if k in g}
    if not out:
        _fail(cid, "Nothing to export yet. Run a separation first.")
    return out


def _delivered(cid, paths: list[Path], folder_name: str | None = None):
    """Desktop: ask where to save (opening in the last-used folder). Browser: normal download."""
    if DESKTOP:
        start = _last_dir()
        if folder_name is None and len(paths) == 1:  # a single file: Save as…
            p = paths[0]
            ft = (f"{p.suffix[1:].upper()} file (*{p.suffix})",)
            chosen = _dialog("save", directory=str(start), save_filename=p.name, file_types=ft)
            if not chosen:
                _p(cid, "Export cancelled.", active=False)
                return None
            dest = Path(chosen)
            if dest.suffix.lower() != p.suffix.lower():
                dest = dest.with_name(dest.name + p.suffix)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(p, dest)
            _remember_dir(dest.parent)
            _p(cid, f"Saved: {dest}", active=False, kind="ok")
            gr.Info(f"Saved {dest.name}")
            return None
        chosen = _dialog("folder", directory=str(start))  # several files: choose a folder
        if not chosen:
            _p(cid, "Export cancelled.", active=False)
            return None
        target = Path(chosen) / (folder_name or "Stems")
        target.mkdir(parents=True, exist_ok=True)
        for p in paths:
            shutil.copy(p, target / p.name)
        _remember_dir(Path(chosen))
        _p(cid, f"Saved {len(paths)} files to {target}", active=False, kind="ok")
        gr.Info(f"Saved {len(paths)} files")
        return None
    _p(cid, f"Exported {len(paths)} file(s). Download them below.", active=False, kind="ok")
    return [str(p) for p in paths]


def _fx_in(d: Path, raw) -> dict:
    """Effect settings sent by the mixer at export time (also saved with the result)."""
    try:
        data = json.loads(raw) if raw else None
    except Exception:
        data = None
    if isinstance(data, dict):
        return fxmod.save(d, data)
    return fxmod.load(d)


def on_save_fx(cid, sid, raw):
    """The mixer's effect settings changed: keep them with the result (and its History copy)."""
    try:
        d = _session_dir(cid, sid)
        _fx_in(d, raw)
        for h in history_dir().glob(f"*-{sid}"):
            if (h / "manifest.json").exists():
                shutil.copy(d / "fx.json", h / "fx.json")
    except Exception:
        pass


def _export_mix(cid, gains_json, sid, quality, video: bool, fx_raw=None):
    d = _session_dir(cid, sid)
    gains = _gains(cid, gains_json)
    if not any(gains.values()):
        _fail(cid, "Every track is muted: nothing to export.")
    _p(cid, "Exporting video…" if video else "Exporting mix…")
    try:  # video: build from a lossless mix so the audio is only compressed once, by the video container
        out = pipeline.export(d, gains, "WAV 24-bit" if video else quality, as_video=video, out_dir=_out_dir(sid),
                              fx=_fx_in(d, fx_raw))
    except FileNotFoundError as e:
        _fail(cid, str(e))
    return _delivered(cid, [out])


def on_export_mix(cid, gains_json, sid, quality, fx_raw=None):
    return _export_mix(cid, gains_json, sid, quality, False, fx_raw)


def on_export_video(cid, gains_json, sid, quality, fx_raw=None):
    return _export_mix(cid, gains_json, sid, quality, True, fx_raw)


def on_export_stems(cid, sid, quality, fx_raw=None):
    d = _session_dir(cid, sid)
    _p(cid, "Exporting stems…")
    z, files = pipeline.export_stems_zip(d, _out_dir(sid), quality, fx=_fx_in(d, fx_raw))
    name = Path(json.loads((d / "manifest.json").read_text())["source_name"]).stem + " stems"
    return _delivered(cid, list(files) if DESKTOP else [z] + list(files), folder_name=name)


def on_open_folder(cid):
    folder = _last_dir()
    try:
        if sys.platform == "win32":
            os.startfile(str(folder))  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])
    except Exception as e:
        _fail(cid, f"Couldn't open the folder: {e}")
    _p(cid, f"Opened {folder}", active=False, kind="ok")


# ----------------------------------------------------------------------------- settings actions (each gives visible feedback)
def on_save_token(cid, token):
    tok = (token or "").strip()
    set_setting("mvsep_token", tok)
    text = "MVSEP token saved." if tok else "MVSEP token cleared."
    gr.Info(text)
    return _msg("ok", text)


def on_mvsep_login(cid, email, password):
    if not email or not password:
        gr.Warning("Enter your MVSEP email and password first.")
        return gr.skip(), gr.skip(), _msg("err", "Enter your MVSEP email and password first.")
    try:
        tok = mvsep.login(email.strip(), password)
    except Exception as e:
        gr.Warning(str(e))
        return gr.skip(), gr.skip(), _msg("err", str(e))
    set_setting("mvsep_token", tok)  # only the token is kept; the password is never stored
    gr.Info("Token fetched and saved.")
    return tok, "", _msg("ok", "Got your MVSEP token and saved it. The password was not kept.")


def _sam_state(sizes=None, note=""):
    """Small status panel for the SAM Audio setup. Without `sizes` it only reports what is saved on this PC."""
    rows = ""
    if sizes:
        rows = "".join(f'<div>{k}: {hfaccess.WORDS[v]}</div>' for k, v in sizes.items())
    else:
        have = "A token is saved on this PC." if hfaccess.get_token() else "No token saved yet."
        rows = f"<div>{have} Press step 3 to check your access.</div>"
    return f'<div class="setmsg info">{note}{rows}</div>'


def on_sam_request(size):
    webbrowser.open(hfaccess.model_page(size if size in MODEL_IDS else "small"))
    return _msg("ok", f"Opened the SAM Audio ({size}) page. Sign in, accept Meta's terms and request access. "
                      "Each size is approved separately.")


def on_sam_tokenpage():
    webbrowser.open(hfaccess.TOKEN_PAGE)
    return _msg("ok", "Opened the Hugging Face token page. Create a token (Read is enough), copy it, and paste it here.")


def on_sam_save(tok):
    try:
        hfaccess.save_token(tok)
    except ValueError as e:
        gr.Warning(str(e))
        return gr.skip(), _sam_state(), _msg("err", str(e))
    gr.Info("Token saved.")
    return "", _sam_state(), _msg("ok", "Token saved on this PC. Now press step 3 to check your access.")


def _mcp_command() -> tuple[str, str]:
    """The Python and launcher file Claude should start. (pythonw has no console, so use python.exe for stdio.)"""
    py = Path(sys.executable)
    if py.name.lower() == "pythonw.exe":
        py = py.with_name("python.exe")
    return str(py), str(Path(__file__).resolve().parent / "run_mcp.py")


def on_mcp_setup():
    """Make sure the MCP library is installed, then show the text to paste into Claude Desktop."""
    try:
        import mcp  # noqa: F401
    except ImportError:
        try:
            subprocess.run([sys.executable if Path(sys.executable).name.lower() != "pythonw.exe"
                            else str(Path(sys.executable).with_name("python.exe")),
                            "-m", "pip", "install", "mcp>=1.2,<2"], check=True, capture_output=True,
                           creationflags=audio.NOWIN)
        except Exception as e:
            gr.Warning("Couldn't install the connection library. Check your internet and try again.")
            return gr.skip(), _msg("err", f"Couldn't install the 'mcp' library: {e}")
    cmd, script = _mcp_command()
    text = json.dumps({"mcpServers": {"un-mix-a-lot": {"command": cmd, "args": [script]}}}, indent=2)
    return text, _msg("ok", "Ready. Copy the text below, then follow the steps above it.")


def on_mcp_connect(go=False):
    if not go:
        return gr.skip(), gr.skip()
    cmd, script = _mcp_command()
    text, msg = on_mcp_setup()  # makes sure the library is installed; gives the paste-in text
    if not isinstance(text, str):
        return text, msg
    ok, note = claude_config.connect(cmd, script)
    if ok:
        gr.Info("Connected to Claude Desktop.")
    else:
        gr.Warning("Automatic setup didn't work, and that's okay. Just do it the copy-and-paste way: "
                   "copy the text in the box below and paste it into Claude Desktop (Settings, Developer, Edit Config).", duration=None)
    return text, _msg("ok" if ok else "info", note)


def on_mcp_disconnect(go=False):
    if not go:
        return gr.skip()
    ok, note = claude_config.disconnect()
    return _msg("ok" if ok else "err", note)


def _update_html(res: dict | None = None) -> str:
    base = f"You have version {__version__}."
    if res is None:
        return f'<div style="font-size:12px;opacity:.65">{base}</div>'
    if res["state"] == "new":
        return (f'<div style="font-size:13px"><b>Version {html.escape(res["version"])} is available.</b> {base} '
                f'Download the new installer and run it, the same way you installed this one.</div>')
    if res["state"] == "current":
        return f'<div style="font-size:12px;opacity:.75">{base} You have the latest version.</div>'
    return '<div style="font-size:12px;opacity:.75">Couldn\'t reach GitHub to check. Check your internet and try again.</div>'


_UPD: dict = {"url": updates.PAGE}


def _flag(res: dict) -> str:
    return "1" if res["state"] == "new" else ""


def on_check_updates(auto=False):
    res = updates.check()
    _UPD["url"] = res["url"]
    set_setting("last_update_check", str(int(time.time())))
    if res["state"] == "new":
        gr.Info(f"Version {res['version']} is available. See Settings.") if not auto else None
    elif not auto:
        gr.Info("You have the latest version." if res["state"] == "current" else "Couldn't reach GitHub.")
    return _update_html(res), gr.update(visible=res["state"] == "new"), _flag(res)


def on_auto_update_check():
    """Page load: at most once a day, and only if the user left automatic checks on."""
    if get_setting("auto_update", "1") != "1":
        return gr.skip(), gr.skip(), ""
    try:
        if time.time() - float(get_setting("last_update_check", "0")) < 86400:
            return gr.skip(), gr.skip(), ""
    except ValueError:
        pass
    return on_check_updates(auto=True)


def on_auto_update_setting(on):
    set_setting("auto_update", "1" if on else "0")


def on_open_update():
    try:
        webbrowser.open(_UPD["url"])
    except Exception:
        pass


def on_sam_check():
    res = {k: hfaccess.check(k) for k in hfaccess.SIZES}
    ok = [k for k, v in res.items() if v == "ok"]
    msg = _msg("ok", f"Ready: {', '.join(ok)}.") if ok else _msg("info", "Not ready yet. Details are below the buttons.")
    return _sam_state(res), msg


def on_open_mvsep():
    webbrowser.open("https://mvsep.com")
    return _msg("ok", "Opened mvsep.com in your browser.")


def on_history_settings(keep, folder):
    set_setting("keep_history", "1" if keep else "0")
    if (folder or "").strip():
        set_setting("history_dir", folder.strip())
    text = "History is on. New results are saved." if keep else "History is off. New results are not saved."
    gr.Info(text)
    return _msg("ok", text)


def on_choose_history_folder():
    chosen = _dialog("folder", directory=str(history_dir().parent if history_dir().parent.is_dir() else EXPORT_DIR))
    if not chosen:
        return gr.skip(), _msg("info", "No folder chosen.")
    set_setting("history_dir", chosen)
    gr.Info("History folder saved.")
    return chosen, _msg("ok", f"History folder set to {chosen}")


# ----------------------------------------------------------------------------- UI
DEFAULTS = dict(engine=ENGINES[0], size=MODELS[0], fast=True, p_v="speech", p_m="music", p_e="sound effects",
                chunk=30, overlap=2, cleanup=False, spans=False, cands=1, seed=0, quality=audio.DEFAULT_QUALITY)

ENGINE_NOTES = {
    ENGINE_SAM: "Describe what to pull out (Settings, Model tab). Best for picking out a specific sound.",
    ENGINE_LOCAL: "Splits into dialogue, music and effects with no prompts. First use downloads about 450 MB.",
    ENGINE_CLOUD: "Best quality, runs on mvsep.com. **Your audio is uploaded there.** Needs your MVSEP token (Settings, Model tab).",
    ENGINE_CLOUD2: "The same BandIt v2 model as on this PC, but run on mvsep.com. **Your audio is uploaded there.** Needs your MVSEP token (Settings, Model tab).",
}

# Always start in dark mode (Gradio honours ?__theme=dark).
DARK_JS = ("(function(){var u=new URL(location.href);if(!u.searchParams.has('__theme'))"
           "{u.searchParams.set('__theme','dark');location.replace(u.toString());}})();")
LOAD_JS = ("(m) => { if (m && m !== window.__lm) { window.__lm = m; window.somaMixer.load(m); }"
           " const h = document.getElementById('history-modal'); if (h) h.classList.remove('open'); }")
# An eye icon inside the token / password boxes (added once Gradio has drawn them) toggles show / hide.
EYE_JS = r"""(function(){
  const EYE='<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z"/><circle cx="12" cy="12" r="3"/></svg>';
  const OFF='<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.9 10.9 0 0 1 12 19C5 19 1 12 1 12a18.4 18.4 0 0 1 5.06-5.94M9.9 4.24A9.1 9.1 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19M14.12 14.12a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>';
  function add(id){
    const w=document.getElementById(id); if(!w) return;
    const i=w.querySelector('input'); if(!i) return;
    let b=w.querySelector('.eye');
    if(b){const ir=i.getBoundingClientRect(), wr=w.getBoundingClientRect(); if(ir.height>0) b.style.top=(ir.top-wr.top+(ir.height-26)/2)+'px'; return;}
    b=document.createElement('button'); b.type='button'; b.className='eye'; b.title='Show / hide'; b.innerHTML=EYE;
    b.addEventListener('click',function(e){e.preventDefault(); const show=i.type==='password'; i.type=show?'text':'password'; b.innerHTML=show?OFF:EYE;});
    w.appendChild(b);
  }
  setInterval(function(){add('mv-token');add('mv-pass');},500);
})();"""

APP_CSS = MIXER_CSS + """
#b-settings{position:relative}
#b-settings.has-update::after{content:"";position:absolute;top:3px;right:3px;width:9px;height:9px;border-radius:50%;background:#f97316;box-shadow:0 0 0 2px rgba(0,0,0,.35)}
footer{display:none !important}
.gradio-container{max-width:none !important;padding:10px 14px !important}
#left,#right{gap:8px !important}
.sm-title{font-size:20px;font-weight:700;line-height:1.1}
.sm-title span{font-size:12px;font-weight:400;opacity:.6;margin-left:8px}
#status{height:128px;min-height:128px;max-height:128px;overflow-y:auto;padding:4px 2px !important}
.pg.idle{visibility:hidden}
#left .block{padding:6px !important}
#left .gr-group,#left .form{gap:6px}
#dropfile .wrap,#dropfile button{padding:6px 8px !important;min-height:0 !important}
#dropfile{font-size:13px}
#dropfile .icon-wrap{width:22px !important;height:22px !important;margin-bottom:2px !important}
#dropfile .or{margin:0 !important}
#dropfile [class*=center]{line-height:1.25 !important}
#lic{font-size:11px;opacity:.6}
#hw{font-size:11px;opacity:.65;padding:0 4px !important}
#lic p{margin:0}
/* field labels are headings, not buttons */
span[data-testid="block-info"],[data-testid="block-label"]{background:transparent !important;border:0 !important;
  box-shadow:none !important;padding:0 !important;color:var(--body-text-color-subdued,#9aa3b2) !important;
  font-size:11px !important;font-weight:600 !important;letter-spacing:.07em;text-transform:uppercase}
[data-testid="block-label"]{position:static !important}
.sec{font-size:11px;font-weight:600;letter-spacing:.07em;text-transform:uppercase;opacity:.65;margin:6px 0 -2px 2px}
#expbtns button{height:44px;white-space:nowrap}
#expbtns{gap:8px !important}
/* settings / history cards: one column, one vertical scroll, nothing sideways */
#settings-card,#history-card{overflow-x:hidden !important;overflow-y:auto !important}
#settings-card textarea,#settings-card input[type=text],#settings-card input[type=password]{overflow:hidden !important}
#settings-card .block,#history-card .block{min-width:0 !important}
.setmsg{font-size:13px;padding:6px 10px;border-radius:8px;background:rgba(148,163,184,.12)}
.setmsg.ok{color:#4ade80;background:rgba(74,222,128,.10)}
.setmsg.err{color:#f87171;background:rgba(248,113,113,.10)}
.toast-wrap{z-index:3000 !important}
#mv-token,#mv-pass{position:relative}
#mv-token input,#mv-pass input{padding-right:40px !important}
.eye{position:absolute;right:10px;top:34px;width:26px;height:26px;display:flex;align-items:center;justify-content:center;
  background:transparent;border:0;color:var(--body-text-color-subdued,#9aa3b2);cursor:pointer;padding:0;z-index:5}
.eye:hover{color:var(--body-text-color,#fff)}
.hid{position:absolute !important;left:-9999px !important;width:1px !important;height:1px !important;overflow:hidden !important;opacity:0 !important;pointer-events:none !important}
"""


def build() -> gr.Blocks:
    with gr.Blocks(title="Un-Mix-a-Lot", css=APP_CSS,
                   head=f"<script>{DARK_JS}</script><script>{FX_JS}</script><script>{MIXER_JS}</script><script>{EYE_JS}</script>",
                   theme=gr.themes.Soft(primary_hue="orange")) as demo:
        manifest = gr.Textbox(visible=False)
        sid = gr.State("")
        cid = gr.State("")
        with gr.Row(equal_height=False):
            # ---------------- left: everything you operate
            with gr.Column(scale=0, min_width=340, elem_id="left"):
                gr.HTML('<div class="sm-title">Un-Mix-a-Lot<span>voices · music · effects</span></div>')
                if not ZERO and not MOCK:
                    gr.Markdown(HW["summary"], elem_id="hw")
                if ZERO:
                    gr.Markdown(f"Free demo: files up to **{int(MAX_SECONDS)} s**. Run it locally for long files.")
                file = gr.File(show_label=False, file_types=["audio", "video"], type="filepath", height=96, elem_id="dropfile")
                engine = gr.Dropdown(ENGINES, value=DEFAULTS["engine"], label="Engine", visible=len(ENGINES) > 1)
                note = gr.Markdown(ENGINE_NOTES[ENGINES[0]], elem_id="engine-note", visible=len(ENGINES) > 1)
                with gr.Row(visible=ENGINES[0] == ENGINE_SAM) as sam_row:
                    size = gr.Dropdown(MODELS, value=DEFAULTS["size"], label="SAM model", scale=1, min_width=90)
                    fast = gr.Checkbox(DEFAULTS["fast"], label="Effects = leftovers", scale=2, min_width=140,
                                       info="Faster, recommended")
                go = gr.Button("Separate", variant="primary")
                b_cancel = gr.Button("Cancel", variant="stop", visible=False)
                status = gr.HTML(render_status(""), elem_id="status")
                gr.HTML('<div class="sec">Export</div>')
                quality = gr.Dropdown(list(audio.QUALITIES), value=DEFAULTS["quality"], label="Audio quality")
                with gr.Row(elem_id="expbtns"):
                    b_mix = gr.Button("Export mix", variant="primary", min_width=100)
                    b_video = gr.Button("Export video", variant="primary", min_width=100, visible=False)
                with gr.Row(elem_id="expbtns"):
                    b_stems = gr.Button("Export stems", min_width=100)
                with gr.Row():
                    b_set = gr.Button("Settings", size="sm", min_width=80, elem_id="b-settings")
                    b_hist = gr.Button("History", size="sm", min_width=80, visible=not ZERO)
                with gr.Row():
                    b_reset = gr.Button("Reset controls", size="sm", min_width=80)
                    b_open = gr.Button("Open export folder", size="sm", min_width=80, visible=not ZERO)
                out_files = gr.File(label="Downloads", file_count="multiple", interactive=False,
                                    visible=not DESKTOP, height=80)
            # ---------------- right: big preview + mixer
            with gr.Column(scale=1, elem_id="right"):
                gr.HTML(MIXER_HTML)

        # ---------------- settings pop-up: one column, General tab and Model tab
        with gr.Column(elem_id="settings-modal"):
            with gr.Column(elem_id="settings-card"):
                with gr.Row():
                    gr.HTML('<b style="font-size:16px">Settings</b>')
                    b_close = gr.Button("Close", size="sm", scale=0, min_width=70)
                set_msg = gr.HTML(_msg("info", "Changes here are saved as you make them."))
                with gr.Tabs():
                    with gr.Tab("General"):
                        if not ZERO:
                            gr.HTML('<div class="sec">History</div>')
                            keep_hist = gr.Checkbox(keep_history(), label="Keep history (saves each result so you can reopen it)")
                            hist_dir = gr.Textbox(str(history_dir()), label="History folder", lines=1, max_lines=1)
                            b_hist_dir = gr.Button("Choose folder…", size="sm", visible=DESKTOP)
                        if not ZERO:
                            gr.HTML('<div class="sec">Updates</div>')
                            upd_html = gr.HTML(_update_html())
                            upd_flag = gr.Textbox("", visible=False)
                            b_upd_get = gr.Button("Download the new version", size="sm", variant="primary", visible=False)
                            b_upd = gr.Button("Check for updates", size="sm")
                            auto_upd = gr.Checkbox(get_setting("auto_update", "1") == "1",
                                                   label="Check automatically (once a day, contacts GitHub)")
                        gr.HTML('<div class="sec">Use at your own risk</div>'
                                '<div style="font-size:12px;opacity:.65">Un-Mix-a-Lot is free software provided "as is", with no warranty. '
                                'Always work on copies of your files. The author(s) are not responsible for lost or damaged files or other harm. '
                                'Full text: DISCLAIMER.txt in the app folder.</div>')
                        gr.HTML('<div class="sec">Defaults</div>')
                        gr.HTML('<div style="font-size:12px;opacity:.65">Puts the model settings back to their starting values. '
                                'Your MVSEP token and folders are kept.</div>')
                        b_restore = gr.Button("Restore defaults", size="sm")
                    with gr.Tab("Model"):
                        with gr.Column(visible=ENGINES[0] == ENGINE_SAM) as grp_sam:
                            if not ZERO:
                                gr.HTML('<div class="sec">SAM Audio: free Hugging Face access (one time)</div>'
                                        '<div style="font-size:12px;opacity:.65">Meta shares SAM Audio through Hugging Face, so it needs a '
                                        'free account and their approval. Three steps:</div>')
                                b_samreq = gr.Button("1. Request access (opens Hugging Face)", size="sm")
                                b_samtok = gr.Button("2. Make a token (opens Hugging Face)", size="sm")
                                hf_tok = gr.Textbox(label="Paste your token here", type="password", lines=1, max_lines=1)
                                b_samsave = gr.Button("Save token", size="sm")
                                b_samchk = gr.Button("3. Check my access", size="sm")
                                sam_state = gr.HTML(_sam_state())
                            gr.HTML('<div class="sec">SAM Audio: what to listen for</div>')
                            p_v = gr.Textbox(DEFAULTS["p_v"], label="Voices", placeholder="e.g. man speaking", lines=1, max_lines=1)
                            p_m = gr.Textbox(DEFAULTS["p_m"], label="Music", lines=1, max_lines=1)
                            p_e = gr.Textbox(DEFAULTS["p_e"], label="Effects (when leftovers is off)", lines=1, max_lines=1)
                            gr.HTML('<div class="sec">SAM Audio: processing</div>')
                            chunk_s = gr.Slider(10, 60, DEFAULTS["chunk"], step=1, label="Chunk length (s)")
                            overlap_s = gr.Slider(0.5, 5, DEFAULTS["overlap"], step=0.5, label="Crossfade (s)")
                            cleanup = gr.Checkbox(DEFAULTS["cleanup"], label="Voice sweep: re-check leftovers for voices (slower)")
                            spans = gr.Checkbox(DEFAULTS["spans"], label="Predict time spans (slower, more GPU memory)", visible=not ZERO)
                            cands = gr.Slider(1, 8, DEFAULTS["cands"], step=1, visible=not ZERO,
                                              label="Reranking candidates (slower, more GPU memory)")
                            seed = gr.Number(DEFAULTS["seed"], precision=0, label="Seed")
                        with gr.Column(visible=ENGINES[0] in CLOUD_ENGINES) as grp_cloud:
                            if not ZERO:
                                gr.HTML('<div class="sec">Cloud (MVSEP)</div>'
                                        '<div style="font-size:12px;opacity:.65">Paste your MVSEP API token, or fetch it with your '
                                        'login (the password is not saved).</div>')
                                b_site = gr.Button("Open mvsep.com", size="sm")
                                token = gr.Textbox(get_setting("mvsep_token"), label="API token", type="password",
                                                   lines=1, max_lines=1, elem_id="mv-token")
                                b_token = gr.Button("Save token", size="sm")
                                mv_email = gr.Textbox(label="MVSEP email", lines=1, max_lines=1)
                                mv_pass = gr.Textbox(label="MVSEP password", type="password", lines=1, max_lines=1,
                                                     elem_id="mv-pass")
                                b_login = gr.Button("Get token from login", size="sm")
                        with gr.Column(visible=ENGINES[0] == ENGINE_LOCAL) as grp_none:
                            gr.HTML('<div style="font-size:13px;opacity:.75;padding:8px 2px">This engine has no settings. '
                                    'It runs on its own once you press Separate.</div>')
                    if not ZERO:
                        with gr.Tab("MCP"):
                            gr.HTML('<div class="sec">MCP: let Claude use Un-Mix-a-Lot (optional)</div>'
                                    '<div style="font-size:12px;opacity:.65;line-height:1.55">'
                                    'MCP lets Claude Desktop run this app for you, on your own PC. Then you can just ask, and Claude '
                                    'does the clicking.<br><b>Setup</b><br>'
                                    '1. Press "Connect to Claude Desktop" below.<br>'
                                    '2. Quit Claude Desktop completely and open it again.<br>'
                                    '3. Start a new chat and ask for something, like the examples below.<br>'
                                    'Didn\'t work? Press "Show the text to paste" and paste it into Claude Desktop: Settings, Developer, '
                                    'Edit Config, then restart it.</div>')
                            mcp_go = gr.Checkbox(False, visible=False)
                            b_mcp_on = gr.Button("Connect to Claude Desktop", size="sm", variant="primary")
                            b_mcp_off = gr.Button("Disconnect", size="sm")
                            b_mcp = gr.Button("Show the text to paste", size="sm")
                            mcp_text = gr.Textbox(label="Paste this into Claude Desktop", lines=8, max_lines=8, interactive=False,
                                                  show_copy_button=True)
                            gr.HTML('<div class="sec">Things you can ask Claude</div>'
                                    '<div style="font-size:12px;opacity:.65;line-height:1.6">'
                                    '&bull; "Remove the music from <i>Solara_Confrontation</i> and save the video to my Desktop."<br>'
                                    '&bull; "Take every video in my Scenes folder and remove the music."<br>'
                                    '&bull; "Split the voices in that clip into 3 speakers, then give me just voice 2."<br>'
                                    '&bull; "Lower the music to 30% and keep everything else."<br>'
                                    '&bull; "Save the voices, music and effects as separate files."<br><br>'
                                    'Good to know: your original files are never changed or deleted. Finished files go where you say, '
                                    'or into Documents/Un-Mix-a-Lot/From Claude. Everything Claude makes also shows up in History here, '
                                    'so you can listen and check. Claude can\'t hear audio, so give it a listen yourself.</div>')

        # ---------------- history pop-up
        with gr.Column(elem_id="history-modal"):
            with gr.Column(elem_id="history-card"):
                with gr.Row():
                    gr.HTML('<b style="font-size:16px">History</b>')
                    b_hclose = gr.Button("Close", size="sm", scale=0, min_width=70)
                hist_info = gr.HTML("")
                hist_list = gr.Radio([], label="Past results (newest first)")
                with gr.Row():
                    b_hopen = gr.Button("Open", variant="primary", size="sm")
                    b_hdel = gr.Button("Delete", size="sm")

        # hidden controls that the mixer's own buttons click (Split voices, and edits to the voice strip)
        n_box = gr.Textbox(visible=False)
        seg_box = gr.Textbox(visible=False)
        b_split = gr.Button("split", elem_id="split-btn", elem_classes=["hid"])
        b_edit = gr.Button("edit", elem_id="edit-btn", elem_classes=["hid"])
        fx_box = gr.Textbox(visible=False)
        b_fx = gr.Button("fx", elem_id="fx-btn", elem_classes=["hid"])

        timer = gr.Timer(0.7)
        demo.load(lambda: uuid.uuid4().hex, None, cid)
        timer.tick(render_status, cid, status, show_progress="hidden", queue=False)

        # --- a file was added or removed: show it in the player straight away
        pre_box = gr.Textbox(visible=False)
        file.upload(on_file_loaded, [cid, file], [pre_box, sid], show_progress="hidden", concurrency_limit=2,
                    concurrency_id="pre").success(None, [pre_box], None, js=LOAD_JS)
        file.clear(on_file_cleared, cid, [pre_box, sid], show_progress="hidden", queue=False
                   ).then(None, None, None, js="() => window.somaMixer.unload()")

        # --- separate / cancel (the Separate button is swapped for Cancel while a run is going)
        sep_inputs = [cid, file, engine, size, fast, chunk_s, overlap_s, p_v, p_m, p_e, spans, cands, seed, cleanup]
        run = go.click(lambda: (gr.update(visible=False), gr.update(visible=True)), None, [go, b_cancel], queue=False
                       ).then(on_separate, sep_inputs, [manifest, sid, b_video], concurrency_limit=1,
                              concurrency_id="gpu", show_progress="hidden")
        run.then(lambda: (gr.update(visible=True), gr.update(visible=False)), None, [go, b_cancel], queue=False)
        run.success(None, [manifest], None, js=LOAD_JS)
        b_cancel.click(on_cancel, cid, None, queue=False, show_progress="hidden")
        b_split.click(on_split_voices, [cid, sid, n_box], manifest, concurrency_limit=1, concurrency_id="split",
                      show_progress="hidden",
                      js="(c, s, n) => [c, s, document.getElementById('sm-nv').value]").success(
            None, [manifest], None, js=LOAD_JS)
        b_edit.click(on_edit_voices, [cid, sid, seg_box], manifest, concurrency_limit=1, concurrency_id="split",
                     show_progress="hidden",
                     js="(c, s, x) => [c, s, window.somaMixer.segments()]").success(None, [manifest], None, js=LOAD_JS)

        # --- export
        b_fx.click(on_save_fx, [cid, sid, fx_box], None, show_progress="hidden", queue=False,
                   js="(c, s, x) => [c, s, window.somaMixer.fxJson()]")
        b_mix.click(on_export_mix, [cid, manifest, sid, quality, fx_box], out_files, show_progress="hidden",
                    js="(c, m, s, q, f) => [c, window.somaMixer.gains(), s, q, window.somaMixer.fxJson()]")
        b_video.click(on_export_video, [cid, manifest, sid, quality, fx_box], out_files, show_progress="hidden",
                      js="(c, m, s, q, f) => [c, window.somaMixer.gains(), s, q, window.somaMixer.fxJson()]")
        b_stems.click(on_export_stems, [cid, sid, quality, fx_box], out_files, show_progress="hidden",
                      js="(c, s, q, f) => [c, s, q, window.somaMixer.fxJson()]")
        b_open.click(on_open_folder, cid, None, show_progress="hidden")

        # --- settings / history pop-ups
        b_set.click(None, None, None, js="() => document.getElementById('settings-modal').classList.add('open')")
        b_close.click(None, None, None, js="() => document.getElementById('settings-modal').classList.remove('open')")
        b_hist.click(on_hist_refresh, None, [hist_list, hist_info], show_progress="hidden",
                     js="() => { document.getElementById('history-modal').classList.add('open'); }")
        b_hclose.click(None, None, None, js="() => document.getElementById('history-modal').classList.remove('open')")
        b_hopen.click(on_hist_open, [cid, hist_list], [manifest, sid, b_video], show_progress="hidden"
                      ).success(None, [manifest], None, js=LOAD_JS)
        b_hdel.click(on_hist_delete, [cid, hist_list], [hist_list, hist_info], show_progress="hidden",
                     js="(c, s) => (s && confirm('Delete this saved result? This cannot be undone.')) ? [c, s] : [c, null]")

        # --- which controls show depends on the engine
        def _vis(e):
            return (gr.update(visible=e == ENGINE_SAM), gr.update(visible=e == ENGINE_SAM),
                    gr.update(visible=e in CLOUD_ENGINES), gr.update(visible=e == ENGINE_LOCAL), ENGINE_NOTES[e])
        engine.change(_vis, engine, [sam_row, grp_sam, grp_cloud, grp_none, note], show_progress="hidden", queue=False)

        if not ZERO:
            b_token.click(on_save_token, [cid, token], set_msg, show_progress="hidden")
            b_login.click(on_mvsep_login, [cid, mv_email, mv_pass], [token, mv_pass, set_msg], show_progress="hidden")
            b_site.click(on_open_mvsep, None, set_msg, show_progress="hidden")
            b_samreq.click(on_sam_request, size, set_msg, show_progress="hidden")
            b_samtok.click(on_sam_tokenpage, None, set_msg, show_progress="hidden")
            b_samsave.click(on_sam_save, hf_tok, [hf_tok, sam_state, set_msg], show_progress="hidden")
            b_samchk.click(on_sam_check, None, [sam_state, set_msg], show_progress="hidden")
            _DOT_JS = ("(f) => { const b = document.getElementById('b-settings'); "
                       "if (b) b.classList.toggle('has-update', !!f); }")
            b_upd.click(on_check_updates, None, [upd_html, b_upd_get, upd_flag], show_progress="hidden").then(
                None, upd_flag, None, js=_DOT_JS)
            b_upd_get.click(on_open_update, None, None, show_progress="hidden")
            auto_upd.change(on_auto_update_setting, auto_upd, None, show_progress="hidden")
            demo.load(on_auto_update_check, None, [upd_html, b_upd_get, upd_flag], show_progress="hidden").then(
                None, upd_flag, None, js=_DOT_JS)
            b_mcp.click(on_mcp_setup, None, [mcp_text, set_msg], show_progress="hidden")
            _CONNECT_JS = ("() => [confirm('You are about to edit a file: Claude Desktop\\'s settings file.\\n\\n"
                           "This adds one entry so Claude can use Un-Mix-a-Lot. Nothing else in the file is changed, "
                           "and a dated backup copy is saved first.\\n\\nContinue?')]")
            _DISCONNECT_JS = ("() => [confirm('You are about to edit Claude Desktop\\'s settings file.\\n\\n"
                              "This removes only the Un-Mix-a-Lot entry. Your other settings are not touched.\\n\\nContinue?')]")
            b_mcp_on.click(on_mcp_connect, mcp_go, [mcp_text, set_msg], show_progress="hidden", js=_CONNECT_JS)
            b_mcp_off.click(on_mcp_disconnect, mcp_go, set_msg, show_progress="hidden", js=_DISCONNECT_JS)
            keep_hist.change(on_history_settings, [keep_hist, hist_dir], set_msg, show_progress="hidden")
            hist_dir.submit(on_history_settings, [keep_hist, hist_dir], set_msg, show_progress="hidden")
            b_hist_dir.click(on_choose_history_folder, None, [hist_dir, set_msg], show_progress="hidden")

        model_ctrls = [p_v, p_m, p_e, chunk_s, overlap_s, cleanup, spans, cands, seed]
        model_keys = ("p_v", "p_m", "p_e", "chunk", "overlap", "cleanup", "spans", "cands", "seed")

        def _restore():
            gr.Info("Model settings restored to defaults.")
            return [DEFAULTS[k] for k in model_keys] + [_msg("ok", "Defaults restored. Your token and folders were kept.")]
        b_restore.click(_restore, None, model_ctrls + [set_msg], show_progress="hidden")

        # --- main-screen reset (also un-mutes the tracks); never touches tokens or folders
        reset_outputs = [engine, size, fast, quality] + model_ctrls
        b_reset.click(lambda: [DEFAULTS[k] for k in ("engine", "size", "fast", "quality") + model_keys],
                      None, reset_outputs, show_progress="hidden",
                      js="() => { if (window.somaMixer) window.somaMixer.reset(); }")
    return demo


demo = build()
demo.queue(max_size=20)


def serve(prevent_thread_lock: bool = False, port: int | None = None):
    """Start the Gradio server. Used by __main__ here and by desktop.py."""
    return demo.launch(allowed_paths=[str(WORK), str(EXPORT_DIR)], prevent_thread_lock=prevent_thread_lock,
                       inbrowser=os.environ.get("STEMSPLIT_OPEN") == "1" and not prevent_thread_lock,
                       server_name=os.environ.get("HOST", "127.0.0.1"),
                       server_port=port or int(os.environ.get("PORT", 7860)), show_api=False)


if __name__ == "__main__":
    preload()
    serve()
