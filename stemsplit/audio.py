"""Audio / video I/O helpers built on ffmpeg (no torchaudio needed here)."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np

# On Windows the app runs without a console; without this flag every ffmpeg call flashes a black window.
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)

SR = 48_000  # SAM Audio's native sample rate (mono)


def ffmpeg_exe() -> str:
    """System ffmpeg if present, otherwise the binary bundled by imageio-ffmpeg."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:  # pragma: no cover
        raise RuntimeError(
            "ffmpeg not found. Install it (https://ffmpeg.org) or `pip install imageio-ffmpeg`."
        ) from e


def _run(cmd: list[str], input_bytes: bytes | None = None) -> bytes:
    p = subprocess.run(cmd, input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NOWIN)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {p.stderr.decode(errors='replace')[-800:]}")
    return p.stdout


def decode_mono(path: str | Path, sr: int = SR) -> np.ndarray:
    """Decode any audio/video file to mono float32 at `sr`."""
    cmd = [ffmpeg_exe(), "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sr),
           "-f", "f32le", "-"]
    raw = _run(cmd)
    if not raw:
        raise ValueError("No audio stream found in this file.")
    return np.frombuffer(raw, dtype=np.float32).copy()


def has_video(path: str | Path) -> bool:
    """True if the file contains a (non-cover-art) video stream."""
    p = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NOWIN)
    text = p.stderr.decode(errors="replace")
    for line in text.splitlines():
        if "Video:" in line and "attached pic" not in line:
            return True
    return False


# Export choices shown in the UI: label -> (file extension, ffmpeg codec arguments)
QUALITIES = {
    "WAV 24-bit": ("wav", ["-c:a", "pcm_s24le"]),
    "WAV 16-bit": ("wav", ["-c:a", "pcm_s16le"]),
    "WAV 32-bit float": ("wav", ["-c:a", "pcm_f32le"]),
    "FLAC 24-bit": ("flac", ["-c:a", "flac", "-sample_fmt", "s32", "-bits_per_raw_sample", "24"]),
    "FLAC 16-bit": ("flac", ["-c:a", "flac", "-sample_fmt", "s16"]),
    "MP3 320 kbps": ("mp3", ["-c:a", "libmp3lame", "-b:a", "320k"]),
    "MP3 256 kbps": ("mp3", ["-c:a", "libmp3lame", "-b:a", "256k"]),
    "MP3 192 kbps": ("mp3", ["-c:a", "libmp3lame", "-b:a", "192k"]),
    "MP3 128 kbps": ("mp3", ["-c:a", "libmp3lame", "-b:a", "128k"]),
}
DEFAULT_QUALITY = "WAV 24-bit"


def resolve_quality(fmt: str) -> tuple[str, list[str] | None]:
    """A UI label (e.g. 'MP3 256 kbps') or a bare extension ('wav') -> (extension, codec args or None)."""
    if fmt in QUALITIES:
        return QUALITIES[fmt]
    return fmt.lower().lstrip("."), None


def write_audio(path: str | Path, wav: np.ndarray, sr: int = SR, *, bitrate: str = "192k",
                codec_args: list[str] | None = None) -> Path:
    """Write mono float32 audio. Format is picked from the extension (wav/flac/mp3/ogg) unless codec_args is given."""
    path = Path(path)
    ext = path.suffix.lower().lstrip(".")
    pcm = np.clip(wav, -1.0, 1.0).astype("<f4").tobytes()
    cmd = [ffmpeg_exe(), "-y", "-v", "error", "-f", "f32le", "-ar", str(sr), "-ac", "1", "-i", "-"]
    if codec_args:
        cmd += codec_args
    elif ext == "wav":
        cmd += ["-c:a", "pcm_s24le"]
    elif ext == "flac":
        cmd += ["-c:a", "flac"]
    elif ext == "mp3":
        cmd += ["-c:a", "libmp3lame", "-b:a", bitrate]
    elif ext == "ogg":
        cmd += ["-c:a", "libvorbis", "-b:a", bitrate]
    else:
        raise ValueError(f"Unsupported export format: {ext}")
    _run(cmd + [str(path)], input_bytes=pcm)
    return path


def remux_video(video_path: str | Path, audio_path: str | Path, out_path: str | Path) -> Path:
    """Copy the video stream untouched and replace the audio track (codec chosen to suit the container)."""
    out_path = Path(out_path)
    acodec = ["-c:a", "libopus", "-b:a", "192k"] if out_path.suffix.lower() == ".webm" else ["-c:a", "aac", "-b:a", "256k"]
    cmd = [ffmpeg_exe(), "-y", "-v", "error", "-i", str(video_path), "-i", str(audio_path),
           "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", *acodec, "-shortest", str(out_path)]
    _run(cmd)
    return out_path


def duration_seconds(path: str | Path) -> float:
    """Best-effort duration probe via ffmpeg's banner output."""
    p = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NOWIN)
    for line in p.stderr.decode(errors="replace").splitlines():
        line = line.strip()
        if line.startswith("Duration:"):
            t = line.split(",")[0].split("Duration:")[1].strip()
            if t == "N/A":
                break
            h, m, s = t.split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    return float(len(decode_mono(path))) / SR


def _video_info(path: str | Path) -> tuple[str, bool, int]:
    """(codec, is_8bit_420, height) of the first video stream, from ffmpeg's banner. Empty codec if unknown."""
    import re

    p = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NOWIN)
    for line in p.stderr.decode(errors="replace").splitlines():
        if "Video:" in line and "Stream #" in line:
            codec = line.split("Video:")[1].strip().split()[0].strip(",")
            m = re.search(r"\b(\d{3,5})x(\d{3,5})\b", line)
            return codec, ("yuv420p" in line and "10le" not in line), (int(m.group(2)) if m else 0)
    return "", False, 0


def make_video_preview(src: str | Path, out_base: str | Path, *, half: bool = False,
                       codec: str = "h264") -> Path:
    """Silent, browser-friendly copy of a video for the in-app preview.

    Full resolution (default): an ordinary H.264 file is copied without re-encoding, so the preview is the
    original picture; anything else is re-encoded at the original size (up to 4K) at high quality.
    half=True: re-encoded at half the width and height, which is lighter on slower computers.
    STEMSPLIT_PREVIEW_CODEC=vp9 forces a WebM re-encode.
    """
    out_base = Path(out_base)
    if half:
        vf = "scale='trunc(iw/4)*2':'trunc(ih/4)*2'"  # half size, kept even
        crf = "20"
    else:
        vf = "scale=-2:'min(2160,ih)'"  # unchanged unless taller than 4K
        crf = "18"
    if codec == "h264" and not half:
        vcodec, is420, vh = _video_info(src)
        if vcodec == "h264" and is420 and 0 < vh <= 2160:
            out = out_base.with_suffix(".mp4")
            try:
                _run([ffmpeg_exe(), "-y", "-v", "error", "-i", str(src), "-map", "0:v:0", "-an", "-c:v", "copy",
                      "-movflags", "+faststart", str(out)])
                return out
            except Exception:
                pass  # odd container or stream: fall through to a normal re-encode
    if codec == "vp9":
        out, enc = out_base.with_suffix(".webm"), ["-c:v", "libvpx-vp9", "-crf", "24", "-b:v", "0",
                                                    "-deadline", "realtime", "-cpu-used", "8"]
    else:
        out, enc = out_base.with_suffix(".mp4"), ["-c:v", "libx264", "-preset", "veryfast",
                                                  "-crf", crf, "-pix_fmt", "yuv420p",
                                                  "-movflags", "+faststart"]
    _run([ffmpeg_exe(), "-y", "-v", "error", "-i", str(src), "-map", "0:v:0", "-an", "-vf", vf,
          *enc, str(out)])
    return out
