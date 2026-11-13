"""Split long audio into overlapping chunks and stitch results back with crossfades."""
from __future__ import annotations

import numpy as np


def plan_chunks(n: int, sr: int, chunk_s: float = 30.0, overlap_s: float = 2.0) -> list[tuple[int, int]]:
    """Return [(start, end), ...] sample ranges covering n samples.

    Consecutive chunks overlap by `overlap_s`. The final chunk is never shorter than the
    overlap, so every crossfade region is fully populated.
    """
    chunk = int(round(chunk_s * sr))
    overlap = int(round(overlap_s * sr))
    if chunk <= 2 * overlap:
        raise ValueError("chunk length must be more than twice the overlap")
    hop = chunk - overlap
    spans, start = [], 0
    while True:
        end = min(start + chunk, n)
        spans.append((start, end))
        if end >= n:
            break
        start += hop
    return spans


def _ramps(overlap: int) -> tuple[np.ndarray, np.ndarray]:
    # Equal-gain (sums to exactly 1) raised-cosine ramps. Stems are additive, so
    # sum-to-one weights keep voices + music + effects + other == original.
    t = np.linspace(0.0, 1.0, overlap, endpoint=False, dtype=np.float32)
    fade_in = 0.5 - 0.5 * np.cos(np.pi * t)
    return fade_in, 1.0 - fade_in


def crossfade_add(out: np.ndarray, piece: np.ndarray, start: int, *, first: bool, last: bool,
                  overlap: int) -> None:
    """Accumulate `piece` into `out` at `start`, with fades on the interior edges."""
    w = np.ones(len(piece), dtype=np.float32)
    fade_in, fade_out = _ramps(overlap)
    if not first and len(w) >= overlap:
        w[:overlap] = fade_in
    if not last and len(w) >= overlap:
        w[-overlap:] = fade_out
    out[start:start + len(piece)] += piece * w
