"""giffy: fast wiggle GIFs that keep the photos' full size and quality.

    from giffy import make_gif
    make_gif(["lens1.jpg", "lens2.jpg", "lens3.jpg", "lens4.jpg"], "shot.gif")

Pillow decodes (and optionally resizes) the photos; the C core builds one shared
palette, then dithers and LZW-encodes each unique frame once, one frame per core.
Light lossy LZW (on by default) spends a little of the extra quality on size.
"""
from __future__ import annotations

import argparse
import os
import struct
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterable, Sequence, Union

from PIL import Image

from . import _giffy

__all__ = ["encode", "make_gif", "wiggle_order"]

Source = Union[str, os.PathLike, Image.Image]
T = Image.Transpose
# EXIF orientation -> the transpose that shows the photo upright (as ImageOps.exif_transpose)
_UPRIGHT = {2: T.FLIP_LEFT_RIGHT, 3: T.ROTATE_180, 4: T.FLIP_TOP_BOTTOM, 5: T.TRANSPOSE,
            6: T.ROTATE_270, 7: T.TRANSVERSE, 8: T.ROTATE_90}
_SIDEWAYS = {5, 6, 7, 8}
LOSSY = 4      # max extra RGB distance a pixel may take to lengthen an LZW match
STRENGTH = 14  # Floyd-Steinberg error kept, in 16ths: a touch under full cleans flat areas


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


def encode(images: Sequence[Source], width: int | None = None, delay_ms: int = 100,
           order: Iterable[int] | None = None, colors: int = 256, dither: bool = True,
           lossy: float = LOSSY, threads: int | None = None) -> bytes:
    """GIF bytes for `images` (paths or PIL images), looping forever.

    width: None keeps the photos' own size; else resize to this many px wide.
    order: playback order as frame indexes; None = wiggle_order (1-2-3-4-3-2).
    lossy: 0 = best quality; higher = smaller files (shots with <= `colors`
    colours are always stored exactly). Alpha is dropped. All photos must come
    out the same size.
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
    with ThreadPoolExecutor(threads) as ex:
        loaded = dict(zip(used, ex.map(lambda i: _load(images[i], width), used)))
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
    return b"".join(parts)


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
    ap.add_argument("--threads", type=int)
    a = ap.parse_args(argv)
    order = range(len(a.images)) if a.forward else None
    print(make_gif(a.images, a.out, a.width, a.delay, order=order, colors=a.colors,
                   dither=not a.no_dither, lossy=a.lossy, threads=a.threads))
