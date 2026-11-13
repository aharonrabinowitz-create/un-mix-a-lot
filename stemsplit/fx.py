"""Per-track audio effects: the Python side (used when exporting).

The same chain is built live in the browser (stemsplit/fxjs.py) for instant preview. Both read the
parameter list, ranges and presets from this file, so they stay in step. Effects never change the
saved stems: the settings are stored next to the result (fx.json) and applied on export only.

Chain order: robot -> chorus -> bass / treble -> low cut / high cut -> crunch -> echo -> reverb -> static
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

SR = 48_000

# name: (default, min, max, step, label, unit)
PARAMS = {
    "bass":        (0.0, -12.0, 12.0, 0.5, "Low shelf", "dB"),
    "treble":      (0.0, -12.0, 12.0, 0.5, "High shelf", "dB"),
    "lowcut":      (20.0, 20.0, 1000.0, 1.0, "High-pass", "Hz"),
    "highcut":     (20000.0, 1000.0, 20000.0, 1.0, "Low-pass", "Hz"),
    "robot":       (0.0, 0.0, 1.0, 0.01, "Mix", ""),
    "robot_hz":    (55.0, 20.0, 300.0, 1.0, "Frequency", "Hz"),
    "chorus":      (0.0, 0.0, 1.0, 0.01, "Mix", ""),
    "chorus_depth": (4.0, 0.5, 10.0, 0.1, "Depth", "ms"),
    "chorus_rate": (0.8, 0.1, 5.0, 0.1, "Rate", "Hz"),
    "crunch":      (0.0, 0.0, 1.0, 0.01, "Amount", ""),
    "static":      (0.0, 0.0, 1.0, 0.01, "Level", ""),
    "echo":        (0.0, 0.0, 1.0, 0.01, "Mix", ""),
    "echo_time":   (0.3, 0.05, 1.0, 0.01, "Time", "s"),
    "echo_fb":     (0.35, 0.0, 0.9, 0.01, "Feedback", ""),
    "reverb":      (0.0, 0.0, 1.0, 0.01, "Mix", ""),
    "reverb_size": (1.2, 0.1, 4.0, 0.05, "Decay", "s"),
}
DEFAULTS = {k: v[0] for k, v in PARAMS.items()}

# The panel: one module per effect, in signal order. "off" = the values that mean "not used";
# "on" = what switching the module on starts with (unless the user's last settings are remembered).
MODULES = [
    {"id": "eq", "name": "EQ", "params": ["bass", "treble"], "off": {"bass": 0, "treble": 0}, "on": {"bass": 3, "treble": 3}},
    {"id": "filter", "name": "Filter", "params": ["lowcut", "highcut"], "off": {"lowcut": 20, "highcut": 20000},
     "on": {"lowcut": 300, "highcut": 4000}},
    {"id": "ring", "name": "Ring modulator", "params": ["robot", "robot_hz"], "off": {"robot": 0}, "on": {"robot": 0.8}},
    {"id": "chorus", "name": "Chorus", "params": ["chorus", "chorus_depth", "chorus_rate"], "off": {"chorus": 0}, "on": {"chorus": 0.5}},
    {"id": "dist", "name": "Distortion", "params": ["crunch"], "off": {"crunch": 0}, "on": {"crunch": 0.3}},
    {"id": "delay", "name": "Delay", "params": ["echo", "echo_time", "echo_fb"], "off": {"echo": 0}, "on": {"echo": 0.3}},
    {"id": "reverb", "name": "Reverb", "params": ["reverb", "reverb_size"], "off": {"reverb": 0}, "on": {"reverb": 0.3}},
    {"id": "noise", "name": "Noise", "params": ["static"], "off": {"static": 0}, "on": {"static": 0.05}},
]

# Starting points with friendly names. Every number can be changed in the panel.
PRESETS = {
    "None": {},
    "Robot Voice": {"robot": 0.85, "robot_hz": 55, "lowcut": 120, "highcut": 7000},
    "Chorus": {"chorus": 0.6, "chorus_depth": 5, "chorus_rate": 0.7},
    "Radio": {"lowcut": 450, "highcut": 3200},
    "Old Radio (gritty)": {"lowcut": 400, "highcut": 3200, "crunch": 0.35, "static": 0.06},
    "Space Helmet": {"lowcut": 350, "highcut": 3500, "reverb": 0.3, "reverb_size": 0.15},
    "Telephone": {"lowcut": 500, "highcut": 2800},
    "Cave": {"lowcut": 80, "reverb": 0.55, "reverb_size": 3.0},
    "Underwater": {"highcut": 900, "chorus": 0.5, "chorus_depth": 6, "chorus_rate": 0.5, "reverb": 0.2, "reverb_size": 0.6},
}

# Fixed internals shared with the browser version.
BASS_HZ, TREBLE_HZ = 200.0, 4000.0
FILTER_Q = 1 / math.sqrt(2)
RING_COMB_S, RING_COMB_FB, RING_MAKEUP = 0.007, 0.55, 1.4
RING_TAPS, ECHO_TAPS = 12, 32  # repeats are built from a fixed number of delayed copies (same in the browser)
CHORUS_BASE_S = 0.020
IR_SEED, NOISE_SEED = 1234, 777
STATIC_LEVEL = 0.1


def config() -> dict:
    """What the browser needs: names, ranges, defaults, presets."""
    return {"modules": MODULES,
            "params": {k: {"def": v[0], "min": v[1], "max": v[2], "step": v[3], "label": v[4], "unit": v[5]}
                       for k, v in PARAMS.items()},
            "presets": {n: {**DEFAULTS, **p} for n, p in PRESETS.items()},
            "constants": {"bassHz": BASS_HZ, "trebleHz": TREBLE_HZ, "q": FILTER_Q, "ringCombS": RING_COMB_S,
                          "ringCombFb": RING_COMB_FB, "ringTaps": RING_TAPS, "echoTaps": ECHO_TAPS, "ringMakeup": RING_MAKEUP, "chorusBaseS": CHORUS_BASE_S,
                          "irSeed": IR_SEED, "noiseSeed": NOISE_SEED, "staticLevel": STATIC_LEVEL}}


def clean(p: dict | None) -> dict:
    """Fill in defaults and clamp every value to its range."""
    out = dict(DEFAULTS)
    for k, v in (p or {}).items():
        if k in PARAMS:
            try:
                out[k] = min(PARAMS[k][2], max(PARAMS[k][1], float(v)))
            except (TypeError, ValueError):
                pass
    return out


def is_default(p: dict | None) -> bool:
    return clean(p) == DEFAULTS


# ---------------------------------------------------------------- deterministic noise (same in the browser)
def mulberry32(seed: int, n: int) -> np.ndarray:
    """n values in [0, 1), identical to the JavaScript mulberry32 generator."""
    M = 0xFFFFFFFF
    a = (np.uint64(seed & M) + (np.arange(1, n + 1, dtype=np.uint64) * np.uint64(0x6D2B79F5))) & np.uint64(M)
    t = ((a ^ (a >> np.uint64(15))) * (np.uint64(1) | a)) & np.uint64(M)
    t = (((t + (((t ^ (t >> np.uint64(7))) * (np.uint64(61) | t)) & np.uint64(M))) & np.uint64(M)) ^ t)
    return ((t ^ (t >> np.uint64(14))) & np.uint64(M)).astype(np.float64) / 4294967296.0


def impulse_response(size_s: float, sr: int = SR) -> np.ndarray:
    n = max(1, int(size_s * sr))
    noise = mulberry32(IR_SEED, n) * 2 - 1
    ir = noise * np.exp(-6.9078 * np.arange(n) / n)
    return ir / (math.sqrt(float(np.sum(ir ** 2))) + 1e-12)


def noise_loop(sr: int = SR) -> np.ndarray:
    return mulberry32(NOISE_SEED, sr) * 2 - 1


# ---------------------------------------------------------------- pieces
def _biquad(kind: str, f: float, gain_db: float, sr: int):
    w0 = 2 * math.pi * f / sr
    cw, sw = math.cos(w0), math.sin(w0)
    if kind in ("lowpass", "highpass"):
        al = sw / (2 * FILTER_Q)
        if kind == "lowpass":
            b = [(1 - cw) / 2, 1 - cw, (1 - cw) / 2]
        else:
            b = [(1 + cw) / 2, -(1 + cw), (1 + cw) / 2]
        a = [1 + al, -2 * cw, 1 - al]
    else:  # shelves, slope 1
        A = 10 ** (gain_db / 40)
        al = sw / 2 * math.sqrt(2)
        sq = 2 * math.sqrt(A) * al
        if kind == "lowshelf":
            b = [A * ((A + 1) - (A - 1) * cw + sq), 2 * A * ((A - 1) - (A + 1) * cw), A * ((A + 1) - (A - 1) * cw - sq)]
            a = [(A + 1) + (A - 1) * cw + sq, -2 * ((A - 1) + (A + 1) * cw), (A + 1) + (A - 1) * cw - sq]
        else:
            b = [A * ((A + 1) + (A - 1) * cw + sq), -2 * A * ((A - 1) + (A + 1) * cw), A * ((A + 1) + (A - 1) * cw - sq)]
            a = [(A + 1) - (A - 1) * cw + sq, 2 * ((A - 1) - (A + 1) * cw), (A + 1) - (A - 1) * cw - sq]
    return np.array(b) / a[0], np.array(a) / a[0]


def _filter(x, kind, f, gain_db, sr):
    from scipy.signal import lfilter
    b, a = _biquad(kind, min(f, sr * 0.49), gain_db, sr)
    return lfilter(b, a, x)


def _taps(w: np.ndarray, delay: int, fb: float, first: int, count: int) -> np.ndarray:
    """sum over k of fb**(k-first) * w[n - k*delay], k = first .. first+count-1."""
    y = np.zeros_like(w)
    for k in range(first, first + count):
        sh = k * delay
        if sh >= len(w):
            break
        y[sh:] += (fb ** (k - first)) * w[:len(w) - sh]
    return y


def _comb(w: np.ndarray, delay: int, fb: float) -> np.ndarray:
    """Short metallic comb: the signal plus RING_TAPS - 1 fading copies."""
    return _taps(w, delay, fb, 0, RING_TAPS)


def _echo(x: np.ndarray, delay: int, fb: float) -> np.ndarray:
    """Wet signal only: ECHO_TAPS repeats, each quieter by `fb`."""
    return _taps(x, delay, fb, 1, ECHO_TAPS)


def _chorus_wet(x: np.ndarray, depth_ms: float, rate: float, sr: int) -> np.ndarray:
    n = np.arange(len(x))
    d = (CHORUS_BASE_S + depth_ms / 1000 * np.sin(2 * math.pi * rate * n / sr)) * sr
    pos = n - d
    i0 = np.floor(pos).astype(np.int64)
    fr = pos - i0
    ok0, ok1 = i0 >= 0, i0 + 1 >= 0
    v0 = np.where(ok0, x[np.clip(i0, 0, len(x) - 1)], 0.0)
    v1 = np.where(ok1, x[np.clip(i0 + 1, 0, len(x) - 1)], 0.0)
    return v0 * (1 - fr) + v1 * fr


def _crunch_curve(c: float) -> np.ndarray:
    k = 1 + 9 * c
    xs = np.linspace(-1, 1, 4096)
    return np.tanh(k * xs) / math.tanh(k)


def apply(x: np.ndarray, params: dict | None, sr: int = SR) -> np.ndarray:
    """Run the chain on a mono float array. Same length out; default settings return the input unchanged."""
    p = clean(params)
    x = np.asarray(x, dtype=np.float64)
    if p == DEFAULTS or len(x) == 0:
        return x.astype(np.float32)
    n = len(x)
    y = x
    if p["robot"] > 0:
        w = y * np.sin(2 * math.pi * p["robot_hz"] * np.arange(n) / sr)
        w = _comb(w, int(round(RING_COMB_S * sr)), RING_COMB_FB)
        y = (1 - p["robot"]) * y + p["robot"] * RING_MAKEUP * w
    if p["chorus"] > 0:
        y = y + p["chorus"] * _chorus_wet(y, p["chorus_depth"], p["chorus_rate"], sr)
    y = _filter(y, "lowshelf", BASS_HZ, p["bass"], sr)
    y = _filter(y, "highshelf", TREBLE_HZ, p["treble"], sr)
    y = _filter(y, "highpass", p["lowcut"], 0, sr)
    y = _filter(y, "lowpass", p["highcut"], 0, sr)
    if p["crunch"] > 0:
        m = min(1.0, p["crunch"] * 3)
        y = (1 - m) * y + m * np.interp(y, np.linspace(-1, 1, 4096), _crunch_curve(p["crunch"]))
    if p["echo"] > 0:
        y = y + p["echo"] * _echo(y, max(1, int(round(p["echo_time"] * sr))), p["echo_fb"])
    if p["reverb"] > 0:
        from scipy.signal import fftconvolve
        y = y + p["reverb"] * fftconvolve(y, impulse_response(p["reverb_size"], sr))[:n]
    if p["static"] > 0:
        nz = noise_loop(sr)
        y = y + p["static"] * STATIC_LEVEL * np.resize(nz, n)
    return y.astype(np.float32)


# ---------------------------------------------------------------- saved with each result
def load(session_dir: Path) -> dict:
    try:
        raw = json.loads((Path(session_dir) / "fx.json").read_text())
        return {k: clean(v) for k, v in raw.items() if isinstance(v, dict) and not is_default(v)}
    except Exception:
        return {}


def save(session_dir: Path, fx: dict) -> dict:
    keep = {k: clean(v) for k, v in (fx or {}).items() if isinstance(v, dict) and not is_default(v)}
    (Path(session_dir) / "fx.json").write_text(json.dumps(keep))
    return keep
