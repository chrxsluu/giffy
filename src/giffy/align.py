"""Line the photos up on one point: find the shift of each frame, then crop.

Every decision (is there detail, is it a repeating pattern, where is the nearest
detail, is the match good, fill in a weak lens) is made on ANALYSIS_W-wide luma
copies, so the answer doesn't depend on the output size. refine() then sharpens
the shifts at the frames' own size without deciding anything.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from PIL import Image, ImageStat

from . import _giffy

ANALYSIS_W = 320      # decisions are made on a luma copy this wide (or the frame's width, if smaller)
PATCH = 0.10          # patch side, fraction of analysis width (32 px)
SEARCH_X = 0.12       # sideways search, fraction of width, each way, per lens step from the reference
SEARCH_Y = 0.06       # up-down search, fraction of height, each way (mounting slop, hand shake)
NCC_MIN = 0.85        # a match is good if its score is at least this (true matches score ~0.98)...
UNIQUE_MIN = 0.08     # ...and beats the best score elsewhere (> PATCH/4 away) by at least this
DETAIL_MIN = 5.0      # luma standard deviation a patch needs to count as detailed
REPEAT_MAX = 0.90     # a patch whose self-match has a second peak this high is a repeating pattern
NEAREST_MAX = 0.15    # nearest-detail search radius, fraction of width
REFINE_PATCH = 128    # refine() matches on copies reduced until its patch is at most this, px

CENTER = (0.5, 0.5)


@dataclass(frozen=True)
class Alignment:
    ref: int                                  # reference frame: (n - 1) // 2
    point: tuple[float, float]                # still point in the ref frame, fractions (after any move to nearby detail)
    shifts: tuple[tuple[float, float], ...]   # per frame: where the point sits minus where it sits in ref, fractions of w, h
    crop: tuple[float, float, float, float]   # left, top, right, bottom in ref-frame fractions; (0, 0, 1, 1) = no crop
    aspect: float                             # w / h of the frames it was made for
    status: str                               # "aligned" | "moved" (nearby detail used) | "none"
    confidence: float                         # worst frame's best score; 0.0 when status is "none"
    gains: tuple[tuple[float, float, float], ...] = ()   # per-frame lighting gains; () = no evening


class _Luma:
    """One frame as analysis-size luma bytes."""

    def __init__(self, im: Image.Image):
        self.im = im
        self.w, self.h = im.size
        self.buf = im.tobytes()


def small(frame: Image.Image) -> Image.Image:
    """The frame as an ANALYSIS_W-wide RGB copy (or the frame, if it's no wider)."""
    w, h = frame.size
    aw = min(ANALYSIS_W, w)
    ah = max(1, round(h * aw / w))
    if (aw, ah) == (w, h):
        return frame
    k = min(w // aw, h // ah)   # a fast whole-number shrink first, then the exact size
    f = frame.reduce(k) if k > 1 else frame
    return f.resize((aw, ah), Image.Resampling.BOX)


def _analysis(frames: list[Image.Image], smalls=None) -> list[_Luma]:
    return [_Luma(s.convert("L")) for s in (smalls or [small(f) for f in frames])]


def _patch(lum: _Luma, cx: float, cy: float, p: int) -> tuple[int, int]:
    """Top-left of the p x p patch centred near (cx, cy), kept inside the frame."""
    x = min(max(round(cx - p / 2), 0), lum.w - p)
    y = min(max(round(cy - p / 2), 0), lum.h - p)
    return x, y


def _match(a: _Luma, b: _Luma, x: int, y: int, p: int, rx: int, ry: int):
    return _giffy.match(a.buf, b.buf, a.w, a.h, x, y, p, 0, 0, rx, ry)


def _usable(lum: _Luma, x: int, y: int, p: int, rx: int, ry: int) -> bool:
    """Detailed, and not a repeating pattern."""
    if ImageStat.Stat(lum.im.crop((x, y, x + p, y + p))).stddev[0] < DETAIL_MIN:
        return False
    return _match(lum, lum, x, y, p, rx, ry)[3] <= REPEAT_MAX


def _detail_near(lum: _Luma, cx: float, cy: float, p: int, rx: int, ry: int):
    """(x, y, moved) of the first usable patch at or around (cx, cy), or None."""
    x, y = _patch(lum, cx, cy, p)
    if _usable(lum, x, y, p, rx, ry):
        return x, y, False
    k, limit = 1, NEAREST_MAX * lum.w
    while k * p / 2 <= limit:
        r = k * p / 2
        m = max(8, round(2 * math.pi * r / (p / 2)))
        for j in range(m):   # clockwise from the top
            a = 2 * math.pi * j / m
            x, y = _patch(lum, cx + r * math.sin(a), cy - r * math.cos(a), p)
            if _usable(lum, x, y, p, rx, ry):
                return x, y, True
        k += 1
    return None


def _none(n: int, ref: int, aspect: float, point=CENTER) -> Alignment:
    return Alignment(ref, (float(point[0]), float(point[1])), ((0.0, 0.0),) * n, (0.0, 0.0, 1.0, 1.0), aspect,
                     "none", 0.0)


def find(frames: list[Image.Image], at: tuple = CENTER, smalls: list[Image.Image] | None = None) -> Alignment:
    """Where each frame has to move so the point at `at` holds still.

    frames: upright RGB images, all the same size. at: (x, y) as fractions of
    the reference frame, or (x, y, frame) for a point seen in another frame.
    smalls: small(f) for each frame, if the caller already made them.
    """
    n = len(frames)
    ref = (n - 1) // 2
    w, h = frames[0].size
    aspect = w / h
    if n < 2:
        return _none(n, ref, aspect)
    lum = _analysis(frames, smalls)
    aw, ah = lum[0].w, lum[0].h
    p = round(PATCH * ANALYSIS_W)
    if aw < 2 * p or ah < 2 * p:   # too small for one patch with room around it
        return _none(n, ref, aspect)
    src = at[2] if len(at) > 2 else ref
    if not 0 <= src < n:
        raise ValueError(f"frame {src} is not in this shot")
    ry = max(1, round(SEARCH_Y * ah))
    rx = lambda steps: max(1, round(SEARCH_X * aw * steps))  # noqa: E731
    good = lambda m: m[2] >= NCC_MIN and m[2] - m[3] >= UNIQUE_MIN and not m[4]  # noqa: E731

    hit = _detail_near(lum[src], at[0] * aw, at[1] * ah, p, rx(1), ry)
    if hit is None:
        return _none(n, ref, aspect, at[:2])
    x, y, moved = hit
    if src != ref:   # the same spot in the reference frame
        m = _match(lum[src], lum[ref], x, y, p, rx(abs(src - ref)), ry)
        if not good(m):
            return _none(n, ref, aspect, at[:2])
        x, y = _patch(lum[ref], x + m[0] + p / 2, y + m[1] + p / 2, p)

    shifts, scores, bad = {ref: (0, 0)}, [], []
    for i in range(n):
        if i == ref:
            continue
        m = _match(lum[ref], lum[i], x, y, p, rx(abs(i - ref)), ry)
        if good(m):
            shifts[i] = (m[0], m[1])
            scores.append(m[2])
        else:
            bad.append(i)
    if bad:   # all or nothing: fill in one weak lens from its evenly spaced neighbours
        if len(bad) > 1 or n < 3:
            return _none(n, ref, aspect, at[:2])
        t = [i - ref for i in shifts if i != ref]
        fit = []
        for axis in (0, 1):
            s = [shifts[i][axis] for i in shifts if i != ref]
            step = sum(a * b for a, b in zip(s, t)) / sum(a * a for a in t)
            if any(abs(v - step * ti) > max(2, 0.25 * abs(step)) for v, ti in zip(s, t)):
                return _none(n, ref, aspect, at[:2])
            fit.append(step)
        shifts[bad[0]] = tuple(round(f * (bad[0] - ref)) for f in fit)

    px = [shifts[i] for i in range(n)]
    l, t, r, b = crop_boxes((aw, ah), px)[ref]
    return Alignment(ref, ((x + p / 2) / aw, (y + p / 2) / ah), tuple((dx / aw, dy / ah) for dx, dy in px),
                     (l / aw, t / ah, r / aw, b / ah), aspect, "moved" if moved else "aligned", min(scores))


def _match_near(a: Image.Image, b: Image.Image, x: int, y: int, p: int, dx0: int, dy0: int, r: int, k: int):
    """(dx, dy) in full pixels of the p x p patch at (x, y) of a, found in b within
    dx0, dy0 +- r * k, matched on k-times reduced copies of just that region;
    None unless the match is good."""
    w, h = a.size
    ml = min(x // k, math.ceil((x - (x + min(0, dx0) - (r + 1) * k)) / k))
    mt = min(y // k, math.ceil((y - (y + min(0, dy0) - (r + 1) * k)) / k))
    left, top = x - ml * k, y - mt * k   # patch lands on whole reduced pixels
    right = min(w, x + max(0, dx0) + (r + 1) * k + p)
    bottom = min(h, y + max(0, dy0) + (r + 1) * k + p)
    right, bottom = left + (right - left) // k * k, top + (bottom - top) // k * k
    pk = p // k
    if pk < 2 or (right - left) // k < ml + pk or (bottom - top) // k < mt + pk:
        return None
    box = (left, top, right, bottom)
    ra, rb = a.crop(box).convert("L"), b.crop(box).convert("L")
    if k > 1:
        ra, rb = ra.reduce(k), rb.reduce(k)
    m = _giffy.match(ra.tobytes(), rb.tobytes(), ra.width, ra.height, ml, mt, pk,
                     round(dx0 / k), round(dy0 / k), r, r)
    if m[2] < NCC_MIN or m[4]:
        return None
    return m[0] * k, m[1] * k


def refine(frames: list[Image.Image], al: Alignment) -> list[tuple[int, int]]:
    """al's shifts as whole pixels at these frames' size, re-matched locally with a
    patch covering the same share of the frame as find()'s, coarse to fine. Where a
    step doesn't match well, the answer so far stands."""
    n = len(frames)
    if al.status == "none":
        return [(0, 0)] * n
    w, h = frames[0].size
    aw = min(ANALYSIS_W, w)
    p = min(w, h, max(2, round(round(PATCH * ANALYSIS_W) * w / aw)))
    x = min(max(round(al.point[0] * w - p / 2), 0), w - p)
    y = min(max(round(al.point[1] * h - p / 2), 0), h - p)
    k = max(1, math.ceil(p / REFINE_PATCH))
    r = math.ceil(w / aw) + 1   # find()'s answer is within one analysis pixel
    steps = [(k, math.ceil(r / k) + 1), (1, k + 1)] if k > 1 else [(1, r)]
    out = []
    for i in range(n):
        d = (round(al.shifts[i][0] * w), round(al.shifts[i][1] * h))
        if i != al.ref:
            for kk, rr in steps:
                m = _match_near(frames[al.ref], frames[i], x, y, p, d[0], d[1], rr, kk)
                if m is None:
                    break
                d = m
        out.append((0, 0) if i == al.ref else d)
    return out


def crop_boxes(size: tuple[int, int], shifts_px: list[tuple[int, int]]) -> list[tuple[int, int, int, int]]:
    """One PIL box per frame: the largest rectangle of the frames' shape inside
    the area they share, centred in it, moved by each frame's shift."""
    w, h = size
    left = max(0, max(-sx for sx, _ in shifts_px))
    right = min(w, min(w - sx for sx, _ in shifts_px))
    top = max(0, max(-sy for _, sy in shifts_px))
    bottom = min(h, min(h - sy for _, sy in shifts_px))
    sw, sh = right - left, bottom - top
    if sw < 1 or sh < 1:
        raise ValueError("the frames don't overlap")
    if sw * h > sh * w:
        cw, ch = sh * w // h, sh
    else:
        cw, ch = sw, sw * h // w
    cl, ct = left + (sw - cw) // 2, top + (sh - ch) // 2
    return [(cl + sx, ct + sy, cl + sx + cw, ct + sy + ch) for sx, sy in shifts_px]
