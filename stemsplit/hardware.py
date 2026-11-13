"""What can this computer run? Checked once at start so the app only offers engines that will work."""
from __future__ import annotations

import os
import sys

SAM_MIN_GB = 10  # SAM Audio needs roughly 13-14 GB; below this it will not run


def detect() -> dict:
    """{"kind": "cuda"|"mps"|"cpu", "name", "vram_gb", "sam_ok", "summary"}.

    STEMSPLIT_HW can force a result for testing: "cuda:24", "cuda:6", "mps" or "cpu".
    """
    kind, name, gb = "cpu", "", 0.0
    forced = os.environ.get("STEMSPLIT_HW", "").strip().lower()
    if forced:
        kind = forced.split(":")[0]
        gb = float(forced.split(":")[1]) if ":" in forced else 0.0
        name = {"cuda": "NVIDIA GPU", "mps": "Apple chip"}.get(kind, "")
    else:
        try:
            import torch
            if torch.cuda.is_available():
                p = torch.cuda.get_device_properties(0)
                kind, name, gb = "cuda", p.name, p.total_memory / 1e9
            elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                kind, name = "mps", "Apple chip"
        except Exception:
            if sys.platform == "darwin":
                kind, name = "mps", "Apple chip"
    sam_ok = kind == "cuda" and gb >= SAM_MIN_GB
    if kind == "cuda":
        head = f"{name}, {gb:.0f} GB"
        tail = "All engines are available." if sam_ok else f"SAM Audio is hidden because it needs about 13 GB of video memory."
    elif kind == "mps":
        head, tail = name, "SAM Audio is hidden (it needs an NVIDIA GPU). BandIt v2 and the cloud engines work."
    else:
        head, tail = "No dedicated GPU", "SAM Audio is hidden (it needs an NVIDIA GPU). BandIt v2 and the cloud engines work."
    return {"kind": kind, "name": name, "vram_gb": gb, "sam_ok": sam_ok, "summary": f"Your computer: {head}. {tail}"}
