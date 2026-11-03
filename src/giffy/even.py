"""Even out the lenses: per-frame, per-channel gains so the wiggle doesn't flicker.

Gains are measured on small copies of the aligned, cropped frames: for each
pixel the frame is compared with the average of all frames, and the median of
those ratios (mid-tones only, so clipped and near-black pixels can't skew it)
is that lens's difference. Medians shrug off the background that differs
between lenses. The gains multiply to 1 across frames, so the shot as a whole
keeps its look, and each is capped.
"""
from __future__ import annotations

import math
import statistics

from PIL import Image

GAIN_MIN, GAIN_MAX = 0.85, 1.18   # cap per channel
MID_LO, MID_HI = 16, 240          # only unclipped mid-tones are measured
EVEN_W = 80                       # gains are measured on copies this wide


def gains(frames: list[Image.Image]) -> tuple[tuple[float, float, float], ...]:
    """One (r, g, b) gain per frame; frames already aligned and cropped."""
    w, h = frames[0].size
    size = (min(EVEN_W, w), max(1, round(h * min(EVEN_W, w) / w)))
    small = [f.convert("RGB").resize(size, Image.Resampling.BOX).tobytes() for f in frames]
    n, out = len(frames), [[1.0, 1.0, 1.0] for _ in frames]
    for c in range(3):
        cols = [s[c::3] for s in small]
        ratios = [[] for _ in frames]
        for px in zip(*cols):
            if min(px) < MID_LO or max(px) > MID_HI:
                continue
            mean = sum(px) / n
            for i, v in enumerate(px):
                ratios[i].append(mean / v)
        if not ratios[0]:
            continue
        g = [statistics.median(r) for r in ratios]
        norm = math.exp(sum(map(math.log, g)) / n)   # gains multiply to 1: the look stays put
        for i in range(n):
            out[i][c] = min(GAIN_MAX, max(GAIN_MIN, g[i] / norm))
    return tuple(tuple(g) for g in out)


def apply(frame: Image.Image, g: tuple[float, float, float]) -> Image.Image:
    """frame with each channel multiplied by its gain (frame itself if all are 1)."""
    if all(v == 1.0 for v in g):
        return frame
    return frame.point([min(255, round(v * k)) for k in g for v in range(256)])
