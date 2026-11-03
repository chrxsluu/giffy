"""giffy: fast wiggle GIFs that keep the photos' full size and quality.

    from giffy import make_gif
    make_gif(["lens1.jpg", "lens2.jpg", "lens3.jpg", "lens4.jpg"], "shot.gif")

Pillow decodes (and optionally resizes) the photos; the C core builds one shared
palette, then dithers and LZW-encodes each unique frame once, one frame per core.
Light lossy LZW (on by default) spends a little of the extra quality on size.
By default the frames are first lined up on the centre (align.py), cropped to
the area they share, and evened out in brightness and colour (even.py).
"""
from __future__ import annotations

import argparse
import math
import os
import struct
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, Sequence, Union

from PIL import Image

from . import _giffy, even as _even
from .align import CENTER, Alignment, crop_boxes, find, refine, small

__all__ = ["Alignment", "CENTER", "Shot", "encode", "encode_shot", "make_gif", "wiggle_order"]

Source = Union[str, os.PathLike, Image.Image]
T = Image.Transpose
# EXIF orientation -> the transpose that shows the photo upright (as ImageOps.exif_transpose)
_UPRIGHT = {2: T.FLIP_LEFT_RIGHT, 3: T.ROTATE_180, 4: T.FLIP_TOP_BOTTOM, 5: T.TRANSPOSE,
            6: T.ROTATE_270, 7: T.TRANSVERSE, 8: T.ROTATE_90}
_SIDEWAYS = {5, 6, 7, 8}
LOSSY = 4      # max extra RGB distance a pixel may take to lengthen an LZW match
STRENGTH = 14  # Floyd-Steinberg error kept, in 16ths: a touch under full cleans flat areas
CROP_ROOM = 0.85  # aligned shots decode at width / this, so a crop up to 15% needs no upscaling


@dataclass(frozen=True)
class Shot:
    gif: bytes
    alignment: Alignment | None   # None when alignment was off


def wiggle_order(n: int) -> list[int]:
    """Back-and-forth playback: 4 frames -> 1-2-3-4-3-2 (0-based)."""
    return list(range(n)) + list(range(n - 2, 0, -1))


def _load(src: Source, width: int | None) -> tuple[tuple[int, int], bytes]:
    """Upright RGB pixels of one photo, `width` px wide (None = as shot)."""
    own = not isinstance(src, Image.Image)
    f = im = Image.open(src) if own else src
    try:
        orient = im.getexif().get(0x0112, 1)
        w, h = im.size[::-1] if orient in _SIDEWAYS else im.size
        if width and width != w:
            size = (width, max(1, round(h * width / w)))
            raw = size[::-1] if orient in _SIDEWAYS else size
            if own:
                im.draft("RGB", raw)  # JPEG: decode at 1/2, 1/4 or 1/8 when that's still big enough
            im = im if im.mode == "RGB" else im.convert("RGB")
            im = im.resize(raw, Image.Resampling.LANCZOS)
        elif im.mode != "RGB":
            im = im.convert("RGB")
        if orient in _UPRIGHT:
            im = im.transpose(_UPRIGHT[orient])
        return im.size, im.tobytes()
    finally:
        if own:
            f.close()


def _upright(src: Source, width: int | None) -> Image.Image:
    """Upright RGB copy of one photo, not resized: JPEGs decode at reduced size
    when that still leaves room to crop and come out `width` px wide."""
    own = not isinstance(src, Image.Image)
    f = im = Image.open(src) if own else src
    try:
        orient = im.getexif().get(0x0112, 1)
        w, h = im.size[::-1] if orient in _SIDEWAYS else im.size
        if own and width and width < w:
            tw = min(w, math.ceil(width / CROP_ROOM))
            size = (tw, max(1, round(h * tw / w)))
            im.draft("RGB", size[::-1] if orient in _SIDEWAYS else size)
        im = im.convert("RGB") if im.mode != "RGB" else im.copy()
        return im.transpose(_UPRIGHT[orient]) if orient in _UPRIGHT else im
    finally:
        if own:
            f.close()


def _fits(al: Alignment, n: int, aspect: float) -> bool:
    return len(al.shifts) == n and al.ref == (n - 1) // 2 and abs(al.aspect / aspect - 1) <= 0.01


def encode(images: Sequence[Source], width: int | None = None, delay_ms: int = 100,
           order: Iterable[int] | None = None, colors: int = 256, dither: bool = True,
           lossy: float = LOSSY, threads: int | None = None, align=CENTER, even: bool | None = None) -> bytes:
    """GIF bytes for `images`: encode_shot(...).gif."""
    return encode_shot(images, width, delay_ms, order, colors, dither, lossy, threads, align, even).gif


def encode_shot(images: Sequence[Source], width: int | None = None, delay_ms: int = 100,
                order: Iterable[int] | None = None, colors: int = 256, dither: bool = True,
                lossy: float = LOSSY, threads: int | None = None, align=CENTER, even: bool | None = None) -> Shot:
    """The GIF for `images` (paths or PIL images), looping forever, and how it was aligned.

    width: None keeps the photos' size; else the finished GIF is this many px wide.
    order: playback order as frame indexes; None = wiggle_order (1-2-3-4-3-2).
    lossy: 0 = best quality; higher = smaller files (shots with <= `colors`
    colours are always stored exactly).
    align: the point to hold still, (x, y) as fractions of the middle frame or
    (x, y, frame) for a point seen in another frame; an Alignment from an
    earlier call (e.g. the preview) to reuse; or None to leave frames as shot.
    even: even out brightness and colour between the lenses. None (default) = when
    aligning; frames left as shot may not show the same view, so evening them is
    only done when asked for with even=True.
    Alpha is dropped. All photos must come out the same size.
    """
    if not images:
        raise ValueError("no images")
    if width is not None and width < 1:
        raise ValueError(f"width must be >= 1, got {width}")
    cs = round(delay_ms / 10)  # GIF delays are in 1/100 s
    if not 0 <= cs <= 0xFFFF:
        raise ValueError(f"delay {delay_ms} ms is outside 0..655350")
    order = wiggle_order(len(images)) if order is None else list(order)
    if not order or not all(0 <= i < len(images) for i in order):
        raise ValueError(f"order {order} must pick from frames 0..{len(images) - 1}")
    used = sorted(set(order))
    threads = threads or min(4, os.cpu_count() or 1)
    if even is None:
        even = align is not None
    if align is not None and not isinstance(align, Alignment):
        align = tuple(align)
    if isinstance(align, tuple):
        if len(align) not in (2, 3):
            raise ValueError(f"align {align} must be (x, y) or (x, y, frame)")
        if len(align) == 3:
            if align[2] not in used:
                raise ValueError(f"align frame {align[2]} isn't played in order {order}")
            align = (align[0], align[1], used.index(align[2]))
    alignment = None
    with ThreadPoolExecutor(threads) as ex:
        if align is None and not even:
            loaded = dict(zip(used, ex.map(lambda i: _load(images[i], width), used)))
        else:
            frames = list(ex.map(lambda i: _upright(images[i], width), used))
            if len({f.size for f in frames}) > 1:
                raise ValueError(f"photos differ in size: {sorted({f.size for f in frames})}")
            fw, fh = frames[0].size
            smalls = list(ex.map(small, frames))   # one cheap copy each, for finding and for lighting
            if align is not None:
                if isinstance(align, Alignment):
                    if not _fits(align, len(frames), fw / fh):
                        raise ValueError("alignment was made for a different shot")
                    alignment = align
                else:
                    alignment = find(frames, align, smalls)
                boxes = crop_boxes((fw, fh), refine(frames, alignment))
                frames = list(ex.map(lambda fb: fb[0].crop(fb[1]), zip(frames, boxes)))
                sw, sh = smalls[0].size
                sboxes = crop_boxes((sw, sh), [(round(dx * sw), round(dy * sh)) for dx, dy in alignment.shifts])
                smalls = [s.crop(b) for s, b in zip(smalls, sboxes)]
            if even:
                if alignment is not None and alignment.gains:
                    gains = alignment.gains
                elif alignment is not None and alignment.status == "none":
                    gains = ((1.0, 1.0, 1.0),) * len(frames)   # unaligned frames may not show the same view
                else:
                    gains = _even.gains(smalls)
                frames = list(ex.map(lambda fg: _even.apply(*fg), zip(frames, gains)))
                if alignment is not None:
                    alignment = replace(alignment, gains=gains)
            cw, ch = frames[0].size
            if width and width != cw:
                size = (width, max(1, round(ch * width / cw)))
                frames = list(ex.map(lambda f: f.resize(size, Image.Resampling.LANCZOS), frames))
            loaded = {i: (f.size, f.tobytes()) for i, f in zip(used, frames)}
        sizes = {size for size, _ in loaded.values()}
        if len(sizes) > 1:
            raise ValueError(f"photos differ in size: {sorted(sizes)}")
        (w, h), = sizes
        if w > 0xFFFF or h > 0xFFFF:
            raise ValueError(f"{w}x{h} is too big for a GIF (max 65535 a side)")
        pal, exact = _giffy.palette([px for _, px in loaded.values()], w, h, colors)
        bits = max(1, (len(pal) // 3 - 1).bit_length())  # global colour table holds 2**bits colours
        lut = bytearray(b"\xff\xff") * _giffy.LUT_SIZE    # shared nearest-colour cache, filled lazily

        def frame(i):
            px = loaded[i][1]
            if exact:
                return _giffy.lzw(_giffy.remap(px, w, h, pal), max(2, bits))
            return _giffy.encode(px, w, h, pal, lut, max(2, bits), lossy, STRENGTH if dither else 0)

        data = dict(zip(used, ex.map(frame, used)))
    parts = [b"GIF89a", struct.pack("<HHBBB", w, h, 0xF0 | (bits - 1), 0, 0),
             pal, bytes(3 * (1 << bits) - len(pal)),
             b"\x21\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00"]           # loop forever
    gce = b"\x21\xf9\x04\x04" + struct.pack("<H", cs) + b"\x00\x00"   # delay, keep previous frame
    desc = b"\x2c" + struct.pack("<HHHHB", 0, 0, w, h, 0)              # full frame, global palette
    for i in order:
        parts += [gce, desc, data[i]]
    parts.append(b"\x3b")
    return Shot(b"".join(parts), alignment)


def make_gif(images: Sequence[Source], out: str | os.PathLike, width: int | None = None,
             delay_ms: int = 100, **kw) -> Path:
    """Write encode(images, ...) to `out` and return its path."""
    out = Path(out)
    out.write_bytes(encode(images, width, delay_ms, **kw))
    return out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="giffy", description="Make a wiggle GIF at full size and quality")
    ap.add_argument("out", type=Path)
    ap.add_argument("images", type=Path, nargs="+")
    ap.add_argument("--width", type=int, help="resize to this width (default: keep the photos' size)")
    ap.add_argument("--delay", type=int, default=100, help="ms per frame (default 100)")
    ap.add_argument("--colors", type=int, default=256)
    ap.add_argument("--forward", action="store_true", help="play 1-2-3-4 instead of 1-2-3-4-3-2")
    ap.add_argument("--lossy", type=float, default=LOSSY, help=f"0 = best quality, higher = smaller (default {LOSSY})")
    ap.add_argument("--no-dither", action="store_true")
    ap.add_argument("--no-align", action="store_true", help="leave the frames as shot")
    ap.add_argument("--align-at", nargs="+", metavar="X Y [FRAME]",
                    help="point to hold still, as fractions (default: the centre); FRAME = the frame it's seen in")
    ap.add_argument("--no-even", action="store_true", help="don't even out brightness and colour (it's off with --no-align)")
    ap.add_argument("--threads", type=int)
    a = ap.parse_args(argv)
    order = range(len(a.images)) if a.forward else None
    align = CENTER
    if a.no_align:
        align = None
    elif a.align_at:
        if len(a.align_at) not in (2, 3):
            ap.error("--align-at takes X Y or X Y FRAME")
        align = (float(a.align_at[0]), float(a.align_at[1])) + ((int(a.align_at[2]),) if len(a.align_at) == 3 else ())
    print(make_gif(a.images, a.out, a.width, a.delay, order=order, colors=a.colors,
                   dither=not a.no_dither, lossy=a.lossy, threads=a.threads, align=align,
                   even=False if a.no_even else None))
