import hashlib
import math
import random
import shutil
import struct
import subprocess
import sys

import PIL
import pytest
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps, ImageStat

import giffy
import refdec
from giffy import _giffy, encode, make_gif, wiggle_order

FFMPEG = shutil.which("ffmpeg")
OFF = dict(align=None, even=False)  # as giffy encoded before alignment existed


def raw_gif(idx: bytes, w: int, h: int, pal: bytes, min_size: int, frames: int = 1) -> bytes:
    """Smallest valid GIF around one LZW stream from _giffy.lzw."""
    bits = max(1, (len(pal) // 3 - 1).bit_length())
    data = _giffy.lzw(idx, min_size)
    frame = (b"\x21\xf9\x04\x04\x0a\x00\x00\x00"  # 100 ms, so decoders keep every frame
             + b"\x2c" + struct.pack("<HHHHB", 0, 0, w, h, 0) + data)
    return (b"GIF89a" + struct.pack("<HHBBB", w, h, 0xF0 | (bits - 1), 0, 0)
            + pal + bytes(3 * (1 << bits) - len(pal)) + frame * frames + b"\x3b")


def distinct_palette(n: int) -> bytes:
    return bytes(c for i in range(n) for c in (i, (i * 37) % 256, 255 - i))


def decode_rgb(path, n=None):
    """Every frame as RGB bytes, via Pillow."""
    with Image.open(path) as im:
        out = []
        for i in range(n or im.n_frames):
            im.seek(i)
            out.append(im.convert("RGB").tobytes())
        return out


def ffmpeg_rgb(path):
    with open(path, "rb") as f:
        w, h = struct.unpack("<HH", f.read(10)[6:10])  # the GIF's own size
    # passthrough: one output frame per GIF frame (no frame-rate duplication)
    raw = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-fps_mode", "passthrough",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         check=True, capture_output=True).stdout
    return [raw[i:i + w * h * 3] for i in range(0, len(raw), w * h * 3)]


def expected_rgb(idx: bytes, pal: bytes) -> bytes:
    return b"".join(pal[3 * i:3 * i + 3] for i in idx)


# ---------------------------------------------------------------- LZW

LZW_CASES = [  # (w, h, min code size, pattern)
    (1, 1, 2, "random"), (2, 1, 2, "random"), (7, 3, 3, "random"), (255, 1, 8, "random"),
    (256, 1, 8, "random"), (300, 200, 8, "random"), (300, 200, 2, "random"), (513, 77, 5, "random"),
    (640, 480, 8, "zeros"), (640, 480, 8, "ramp"), (640, 480, 4, "mostly-zero"), (1, 5000, 8, "zeros"),
]


def pattern(kind, n, colours, rnd):
    if kind == "zeros":
        return bytes(n)
    if kind == "ramp":
        return bytes(i % colours for i in range(n))
    if kind == "mostly-zero":
        return bytes(rnd.choice([rnd.randrange(colours), 0, 0, 0]) for _ in range(n))
    return bytes(rnd.randrange(colours) for _ in range(n))


@pytest.mark.parametrize("w,h,min_size,kind", LZW_CASES)
def test_lzw_round_trips(tmp_path, w, h, min_size, kind):
    rnd = random.Random(w * 1000 + h + min_size)
    colours = 1 << min_size
    idx = pattern(kind, w * h, colours, rnd)
    pal = distinct_palette(colours)
    p = tmp_path / "t.gif"
    p.write_bytes(raw_gif(idx, w, h, pal, min_size, frames=2))
    want = expected_rgb(idx, pal)
    assert refdec.decode(p.read_bytes()) == [idx, idx]
    assert decode_rgb(p) == [want, want]
    if FFMPEG:
        assert ffmpeg_rgb(p)[:2] == [want, want]


def test_lzw_survives_hash_generation_wrap(tmp_path):
    # > 4095 table resets wraps the hash table's generation counter
    w, h = 5000, 5000
    idx = random.Random(5).randbytes(w * h)
    pal = distinct_palette(256)
    p = tmp_path / "big.gif"
    p.write_bytes(raw_gif(idx, w, h, pal, 8))
    with Image.open(p) as im:
        assert im.tobytes() == idx  # first frame keeps its palette indexes


def test_lzw_rejects_index_too_big():
    with pytest.raises(ValueError):
        _giffy.lzw(bytes([0, 1, 4]), 2)
    with pytest.raises(ValueError):
        _giffy.lzw(b"", 8)
    with pytest.raises(ValueError):
        _giffy.lzw(b"\x00", 9)


# ---------------------------------------------------------------- C entry checks

def test_c_entry_points_check_their_buffers():
    lut = bytearray(b"\xff\xff") * _giffy.LUT_SIZE
    pal = distinct_palette(4)
    ok = (bytes(12), 2, 2, pal, lut, 2, 4.0, 14)
    _giffy.encode(*ok)
    for i, bad in [(0, bytes(11)), (3, b""), (3, bytes(4)), (4, bytearray(10)), (5, 1), (5, 9),
                   (6, -1.0), (6, 256.0), (6, float("nan")), (7, -1), (7, 17)]:
        args = list(ok)
        args[i] = bad
        with pytest.raises(ValueError):
            _giffy.encode(*args)
    with pytest.raises(ValueError):
        _giffy.encode(bytes(12), 2, 2, distinct_palette(5), lut, 2, 0.0, 16)  # 5 colours need 3 bits
    with pytest.raises(ValueError):
        _giffy.remap(bytes(10), 2, 2, pal)
    with pytest.raises(ValueError):
        _giffy.palette([bytes(12), bytes(11)], 2, 2, 256)
    with pytest.raises(ValueError):
        _giffy.palette([bytes(12)], 2, 2, 0)
    with pytest.raises(ValueError):
        _giffy.palette([], 2, 2, 256)
    with pytest.raises(ValueError):
        _giffy.encode(bytes(3 * 70000), 70000, 1, pal, lut, 2, 0.0, 16)


def frame_indexes(data: bytes, w: int, h: int, pal: bytes, tmp_path):
    """Palette indexes of one encoded frame, decoded by Pillow (and checked against ffmpeg)."""
    bits = max(1, (len(pal) // 3 - 1).bit_length())
    g = (b"GIF89a" + struct.pack("<HHBBB", w, h, 0xF0 | (bits - 1), 0, 0) + pal + bytes(3 * (1 << bits) - len(pal))
         + b"\x2c" + struct.pack("<HHHHB", 0, 0, w, h, 0) + data + b"\x3b")
    p = tmp_path / "f.gif"
    p.write_bytes(g)
    with Image.open(p) as im:
        idx = im.tobytes()
    assert refdec.decode(g) == [idx]
    if FFMPEG:
        assert ffmpeg_rgb(p)[0] == expected_rgb(idx, pal)
    return idx


@pytest.mark.parametrize("w,h", [(1, 1), (3, 2), (257, 3), (640, 480), (1500, 1000)])
def test_fused_encoder_matches_plain_lzw_when_not_lossy(tmp_path, w, h):
    rgb = scene(w, h, n=1)[0].tobytes() if w >= 32 else random.Random(w).randbytes(w * h * 3)
    pal, _ = _giffy.palette([rgb], w, h, 256)
    bits = max(2, (len(pal) // 3 - 1).bit_length())
    lut = bytearray(b"\xff\xff") * _giffy.LUT_SIZE
    data = _giffy.encode(rgb, w, h, pal, lut, bits, 0.0, 16)
    idx = frame_indexes(data, w, h, pal, tmp_path)
    assert data == _giffy.lzw(idx, bits)  # same pixels, same LZW: byte for byte


def block_errors(a: Image.Image, b: Image.Image) -> list[int]:
    """Sorted worst-channel error of each 10x10 block's average colour."""
    return sorted(max(abs(x - y) for x, y in zip(pa, pb))
                  for pa, pb in zip(a.reduce(10).getdata(), b.reduce(10).getdata()))


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
@pytest.mark.parametrize("lossy", [2.0, 4.0, 12.0, 60.0])
def test_lossy_streams_decode_the_same_everywhere_and_keep_colour(tmp_path, lossy):
    w, h = 1500, 1000  # several table resets
    src = scene(w, h, n=1)[0]
    rgb = src.tobytes()
    pal, _ = _giffy.palette([rgb], w, h, 256)
    lut = bytearray(b"\xff\xff") * _giffy.LUT_SIZE
    decoded = {}
    for L in (0.0, lossy):
        data = _giffy.encode(rgb, w, h, pal, lut, 8, L, 14)
        idx = frame_indexes(data, w, h, pal, tmp_path)
        decoded[L] = (len(data), Image.frombytes("RGB", (w, h), expected_rgb(idx, pal)))
    assert decoded[lossy][0] < decoded[0.0][0]
    # error diffusion keeps blocks' average colour about as true as without lossy
    base, ours = block_errors(src, decoded[0.0][1]), block_errors(src, decoded[lossy][1])
    p99 = lambda e: e[len(e) * 99 // 100]
    assert sum(ours) / len(ours) <= sum(base) / len(base) + 0.1 + lossy / 60
    assert p99(ours) <= p99(base) + 1 + lossy / 30
    assert psnr(decoded[lossy][1].tobytes(), rgb) > (30 if lossy <= 12 else 22)


def test_dither_lut_fill_is_deterministic():
    rgb = random.Random(3).randbytes(200 * 100 * 3)
    pal, exact = _giffy.palette([rgb], 200, 100, 256)
    assert not exact and len(pal) == 768
    outs = set()
    for _ in range(3):
        lut = bytearray(b"\xff\xff") * _giffy.LUT_SIZE
        outs.add(_giffy.encode(rgb, 200, 100, pal, lut, 8, 4.0, 14))
    assert len(outs) == 1


# ---------------------------------------------------------------- photos

def scene(w=640, h=480, n=4, shift=6, seed=7):
    """Parallax-shifted synthetic photos with per-lens noise (like shimmy's bench)."""
    rnd = random.Random(seed)
    big = Image.new("RGB", (w + shift * n, h))
    d = ImageDraw.Draw(big)
    for y in range(h):
        t = y / h
        d.line([(0, y), (big.width, y)], fill=(int(120 + 100 * t), int(170 - 40 * t), int(230 - 160 * t)))
    for _ in range(80):
        x, y, r = rnd.randrange(big.width), rnd.randrange(h), rnd.randrange(5, 60)
        d.ellipse([x - r, y - r, x + r, y + r], fill=tuple(rnd.randrange(256) for _ in range(3)))
    big = big.filter(ImageFilter.GaussianBlur(2))
    out = []
    for i in range(n):
        f = big.crop((i * shift, 0, i * shift + w, h))
        noise = Image.effect_noise((w, h), 12).convert("RGB")
        out.append(ImageChops.add(f, noise, scale=1.0, offset=-128))
    return out


def save_all(tmp_path, ims, ext="jpg", **kw):
    paths = []
    for i, im in enumerate(ims):
        p = tmp_path / f"lens{i + 1}.{ext}"
        im.save(p, **kw)
        paths.append(p)
    return paths


def psnr(a: bytes, b: bytes) -> float:
    da = Image.frombytes("RGB", (len(a) // 3, 1), a)
    db = Image.frombytes("RGB", (len(b) // 3, 1), b)
    mse = sum(ImageStat.Stat(ImageChops.difference(da, db)).sum2) / len(a)
    return 10 * math.log10(255 ** 2 / mse) if mse else math.inf


def tuned_pillow(frames):
    """Shimmy's current winner, for comparison: shared FASTOCTREE palette + Floyd-Steinberg."""
    w, h = frames[0].size
    mos = Image.new("RGB", (256 * len(frames), round(h * 256 / w)))
    for i, f in enumerate(frames):
        mos.paste(f.resize((256, mos.height), Image.Resampling.BILINEAR), (i * 256, 0))
    pal = mos.quantize(256, method=Image.Quantize.FASTOCTREE)
    return [f.quantize(palette=pal, dither=Image.Dither.FLOYDSTEINBERG).convert("RGB").tobytes() for f in frames]


def test_photos_keep_size_order_and_timing(tmp_path):
    paths = save_all(tmp_path, scene(), quality=92)
    out = make_gif(paths, tmp_path / "w.gif", **OFF)
    with Image.open(out) as gif:
        assert gif.size == (640, 480)
        assert gif.n_frames == 6
        assert gif.info["loop"] == 0
        assert gif.info["duration"] == 100
    f = decode_rgb(out)
    assert [f.index(x) for x in f] == [0, 1, 2, 3, 2, 1]  # 1-2-3-4-3-2, repeats identical


def test_quality_at_least_tuned_pillow(tmp_path):
    src = scene()
    for lossy in (0, giffy.LOSSY):
        gif = decode_rgb_bytes(encode(src, lossy=lossy, **OFF), tmp_path)
        ours = sum(psnr(g, s.tobytes()) for g, s in zip(gif, src)) / 4
        theirs = sum(psnr(t, s.tobytes()) for t, s in zip(tuned_pillow(src), src)) / 4
        assert ours >= theirs, (lossy, ours, theirs)


def decode_rgb_bytes(data: bytes, tmp_path):
    p = tmp_path / "x.gif"
    p.write_bytes(data)
    return decode_rgb(p)


def test_few_colours_are_lossless(tmp_path):
    rnd = random.Random(1)
    cols = [tuple(rnd.randrange(256) for _ in range(3)) for _ in range(5)]
    ims = []
    for _ in range(3):
        im = Image.new("RGB", (97, 61))
        im.putdata([rnd.choice(cols) for _ in range(97 * 61)])
        ims.append(im)
    data = encode(ims, **OFF)
    assert data[10] & 7 == 2  # 5 colours -> an 8-entry colour table
    got = decode_rgb_bytes(data, tmp_path)
    assert got == [ims[i].tobytes() for i in wiggle_order(3)]
    if FFMPEG:
        assert ffmpeg_rgb(tmp_path / "x.gif") == got


def test_single_colour(tmp_path):
    data = encode([Image.new("RGB", (5, 4), (9, 8, 7))], **OFF)
    assert decode_rgb_bytes(data, tmp_path) == [bytes((9, 8, 7)) * 20]


def test_exactly_256_colours_lossless_257_not(tmp_path):
    cols = [(i, 255 - i, (i * 7) % 256) for i in range(257)]
    im256 = Image.new("RGB", (16, 16)); im256.putdata(cols[:256])
    assert decode_rgb_bytes(encode([im256], **OFF), tmp_path) == [im256.tobytes()]
    pal, exact = _giffy.palette([Image.new("RGB", (257, 1)).tobytes()], 257, 1, 256)
    assert exact and len(pal) == 3
    im257 = Image.new("RGB", (257, 1)); im257.putdata(cols)
    pal, exact = _giffy.palette([im257.tobytes()], 257, 1, 256)
    assert not exact and len(pal) <= 768


def test_threads_never_change_the_bytes():
    src = scene(320, 240)
    one = encode(src, threads=1, **OFF)
    assert encode(src, threads=4, **OFF) == one
    assert encode(src, threads=3, **OFF) == one


def test_width_resizes_and_default_keeps_size(tmp_path):
    paths = save_all(tmp_path, scene(800, 600, n=2))
    with Image.open(make_gif(paths, tmp_path / "a.gif", **OFF)) as g:
        assert g.size == (800, 600) and g.n_frames == 2
    with Image.open(make_gif(paths, tmp_path / "b.gif", width=640, **OFF)) as g:
        assert g.size == (640, 480)


@pytest.mark.parametrize("orient", [3, 6, 8])
def test_exif_orientation_is_applied(tmp_path, orient):
    exif = Image.Exif()
    exif[0x0112] = orient
    paths = save_all(tmp_path, scene(400, 300, n=2), exif=exif, quality=95)
    want = ImageOps.exif_transpose(Image.open(paths[0]))
    got = decode_rgb_bytes(encode(paths, order=[0], **OFF), tmp_path)[0]
    assert Image.open(tmp_path / "x.gif").size == want.size
    assert psnr(got, want.tobytes()) > 28  # same picture, same way up
    small = decode_rgb_bytes(encode(paths, width=200, order=[0], **OFF), tmp_path)[0]
    assert Image.open(tmp_path / "x.gif").size == (200, round(want.height * 200 / want.width))
    assert psnr(small, want.resize((200, round(want.height * 200 / want.width))).tobytes()) > 25


def test_other_modes_load(tmp_path):
    base = scene(64, 48, n=1)[0]
    for im in [base.convert("RGBA"), base.convert("L"), base.convert("P"), base.convert("CMYK")]:
        with Image.open(make_gif([im], tmp_path / "m.gif", **OFF)) as g:
            assert g.size == (64, 48)


def test_caller_images_are_not_changed():
    im = scene(64, 48, n=1)[0].convert("RGBA")
    before = im.tobytes()
    encode([im, im], width=32)
    assert im.mode == "RGBA" and im.size == (64, 48) and im.tobytes() == before


def test_order_and_delay(tmp_path):
    src = scene(64, 48, n=3)
    p = tmp_path / "o.gif"
    p.write_bytes(encode(src, order=[2, 0], delay_ms=40))
    with Image.open(p) as g:
        assert g.n_frames == 2 and g.info["duration"] == 40


@pytest.mark.parametrize("kw", [dict(order=[]), dict(order=[0, 4]), dict(order=[-1]), dict(width=0),
                                dict(delay_ms=-20), dict(delay_ms=10 ** 7), dict(colors=0), dict(colors=300)])
def test_bad_arguments(kw):
    with pytest.raises(ValueError):
        encode(scene(32, 24, n=2), **kw)


def test_mismatched_sizes():
    with pytest.raises(ValueError, match="differ in size"):
        encode([Image.new("RGB", (10, 10)), Image.new("RGB", (10, 11))])
    with pytest.raises(ValueError):
        encode([])


def test_cli(tmp_path, capsys):
    paths = save_all(tmp_path, scene(64, 48, n=3))
    giffy.main([str(tmp_path / "c.gif"), *map(str, paths), "--forward", "--width", "32", "--no-align", "--no-even"])
    assert capsys.readouterr().out.strip().endswith("c.gif")
    with Image.open(tmp_path / "c.gif") as g:
        assert g.n_frames == 3 and g.size == (32, 24)


@pytest.mark.parametrize("colors", [2, 3, 16, 129])
def test_small_palettes_through_the_dither_path(tmp_path, colors):
    src = scene(200, 150, n=2)
    p = tmp_path / "s.gif"
    p.write_bytes(encode(src, colors=colors))
    frames = decode_rgb(p)
    for f in frames:
        assert len(set(f[i:i + 3] for i in range(0, len(f), 3))) <= colors
    if FFMPEG:
        assert ffmpeg_rgb(p) == frames


def test_no_dither(tmp_path):
    src = scene(200, 150, n=2)
    plain = decode_rgb_bytes(encode(src, dither=False, lossy=0, **OFF), tmp_path)
    assert psnr(plain[0], src[0].tobytes()) > 25


def test_random_shots_decode_the_same_in_pillow_and_ffmpeg(tmp_path):
    rnd = random.Random(11)
    for trial in range(25):
        w, h, n = rnd.randrange(1, 300), rnd.randrange(1, 200), rnd.randrange(1, 5)
        kind = rnd.choice(["scene", "noise", "flat"])
        if kind == "scene" and w >= 16 and h >= 16:
            ims = scene(w, h, n=n, seed=trial)
        elif kind == "noise":
            ims = [Image.frombytes("RGB", (w, h), rnd.randbytes(w * h * 3)) for _ in range(n)]
        else:
            ims = [Image.new("RGB", (w, h), tuple(rnd.randrange(256) for _ in range(3))) for _ in range(n)]
        kw = dict(colors=rnd.choice([2, 7, 64, 256]), lossy=rnd.choice([0, 1.5, 4, 30]),
                  dither=rnd.random() < 0.8, order=rnd.choice([None, list(range(n))]))
        p = tmp_path / f"r{trial}.gif"
        p.write_bytes(encode(ims, **kw))
        frames = decode_rgb(p)
        assert len(frames) == len(kw["order"] or wiggle_order(n)), (trial, kw)
        if FFMPEG:
            assert ffmpeg_rgb(p) == frames, (trial, w, h, kw)
        gif = p.read_bytes()
        pal = gif[13:13 + 3 * (2 << (gif[10] & 7))]
        assert [expected_rgb(i, pal) for i in refdec.decode(gif)] == frames


@pytest.mark.skipif(sys.platform != "darwin" or PIL.__version__ != "13.0.0",
                    reason="hashes were recorded on macOS with Pillow 13.0.0")
def seeded_scene(w, h, n=4, shift=6):
    """Like scene(), but every random value comes from one seeded generator
    (scene()'s effect_noise follows C rand(), so it changes with test order)."""
    rnd = random.Random(42)
    big = Image.new("RGB", (w + shift * n, h))
    d = ImageDraw.Draw(big)
    for y in range(h):
        t = y / h
        d.line([(0, y), (big.width, y)], fill=(int(120 + 100 * t), int(170 - 40 * t), int(230 - 160 * t)))
    for _ in range(80):
        x, y, r = rnd.randrange(big.width), rnd.randrange(h), rnd.randrange(5, 60)
        d.ellipse([x - r, y - r, x + r, y + r], fill=tuple(rnd.randrange(256) for _ in range(3)))
    big = big.filter(ImageFilter.GaussianBlur(2))
    out = []
    for i in range(n):
        f = big.crop((i * shift, 0, i * shift + w, h))
        noise = Image.frombytes("RGB", (w, h), bytes(rnd.randrange(116, 141) for _ in range(w * h * 3)))
        out.append(ImageChops.add(f, noise, scale=1.0, offset=-128))
    return out


def test_off_is_byte_identical_to_before():
    a = encode(seeded_scene(320, 240), **OFF)
    b = encode(seeded_scene(400, 300, n=3), width=200, lossy=0, **OFF)
    assert hashlib.sha256(a).hexdigest() == "7114fde56317d43b4ce656a9110ac5076c789ca31986bcb52b87bbca34cbf6aa"
    assert hashlib.sha256(b).hexdigest() == "f1253de05298e1115fb40ec00ab043c1c1835d6086ad0bd25c4d2558a0e7e085"
