import random

import pytest
from PIL import Image, ImageDraw, ImageFilter, ImageStat

from giffy import _giffy


def textured(w=320, h=240, seed=1) -> Image.Image:
    """Grey blurred blobs: detail everywhere, no repeats."""
    rnd = random.Random(seed)
    im = Image.new("L", (w, h), 128)
    d = ImageDraw.Draw(im)
    for _ in range(w * h // 300):
        x, y, r = rnd.randrange(w), rnd.randrange(h), rnd.randrange(3, 14)
        d.ellipse([x - r, y - r, x + r, y + r], fill=rnd.randrange(256))
    return im.filter(ImageFilter.GaussianBlur(1.5))


def shifted(im: Image.Image, dx: int, dy: int) -> Image.Image:
    """im's content moved by (dx, dy); uncovered edges filled grey."""
    out = Image.new(im.mode, im.size, 128)
    out.paste(im, (dx, dy))
    return out


def match(ref, other, x, y, size, dx0=0, dy0=0, rx=40, ry=8):
    return _giffy.match(ref.tobytes(), other.tobytes(), ref.width, ref.height, x, y, size, dx0, dy0, rx, ry)


# ---------------------------------------------------------------- C matcher

def test_match_finds_a_known_shift():
    ref = textured()
    dx, dy, best, second = match(ref, shifted(ref, 17, -3), 100, 80, 32)
    assert (dx, dy) == (17, -3)
    assert best > 0.99


def test_match_ignores_brightness_and_contrast():
    ref = textured()
    other = Image.eval(shifted(ref, 9, 2), lambda v: int(v * 0.7 + 30))
    dx, dy, best, _ = match(ref, other, 100, 80, 32)
    assert (dx, dy) == (9, 2)
    assert best > 0.98


def test_match_second_peak_on_repeats():
    stripes = Image.new("L", (320, 240))
    stripes.putdata([255 if (x // 6) % 2 else 0 for y in range(240) for x in range(320)])
    _, _, best, second = match(stripes, stripes, 100, 80, 32)
    assert second > 0.9
    ref = textured()
    _, _, best, second = match(ref, ref, 100, 80, 32)
    assert second < best - 0.1


def test_match_flat_patch_scores_zero():
    flat = Image.new("L", (320, 240), 90)
    _, _, best, _ = match(flat, textured(), 100, 80, 32)
    assert best == 0.0


def test_match_validates():
    ref = textured()
    b = ref.tobytes()
    with pytest.raises(ValueError):
        _giffy.match(b[:-1], b, 320, 240, 10, 10, 32, 0, 0, 4, 4)
    with pytest.raises(ValueError):
        _giffy.match(b, b, 320, 240, 300, 10, 32, 0, 0, 4, 4)   # patch leaves ref
    with pytest.raises(ValueError):
        _giffy.match(b, b, 320, 240, 10, 10, 1, 0, 0, 4, 4)
    with pytest.raises(ValueError):
        _giffy.match(b, b, 320, 240, 10, 10, 32, 0, 0, -1, 4)
    _, _, best, second = _giffy.match(b, b, 320, 240, 10, 10, 32, 1000, 0, 4, 4)
    assert best == -1.0 and second == -1.0


# ---------------------------------------------------------------- alignment decisions

from giffy import align  # noqa: E402
from giffy.align import NEAREST_MAX, crop_boxes, find, refine  # noqa: E402


def textured_rgb(w, h, seed=1) -> Image.Image:
    return Image.merge("RGB", [textured(w, h, seed + k) for k in range(3)])


def parallax(n, fg_shift, bg_shift, size=(640, 480), seed=3, disc=True):
    """n frames of a textured background moving bg_shift px per lens, with a
    textured disc in the middle moving fg_shift px per lens (ref = (n - 1) // 2)."""
    w, h = size
    ref, m = (n - 1) // 2, abs(bg_shift) * n + 8
    bg = textured_rgb(w + 2 * m, h, seed)
    fg = textured_rgb(w, h, seed + 10)
    r = round(0.15 * w)
    frames = []
    for i in range(n):
        f = bg.crop((m - bg_shift * (i - ref), 0, m - bg_shift * (i - ref) + w, h))
        if disc:
            cx, cy = w // 2 + fg_shift * (i - ref), h // 2
            mask = Image.new("L", (w, h))
            ImageDraw.Draw(mask).ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)
            f.paste(shifted(fg, fg_shift * (i - ref), 0), (0, 0), mask)
        frames.append(f)
    return frames


def near(got, want, tol=1):
    return all(abs(a - c) <= tol and abs(b - d) <= tol for (a, b), (c, d) in zip(got, want)) and len(got) == len(want)


FG = [(-20, 0), (0, 0), (20, 0), (40, 0)]


def test_center_holds_the_foreground():
    frames = parallax(4, 20, 4)
    al = find(frames)
    assert al.status == "aligned" and al.ref == 1
    assert near(refine(frames, al), FG)


def test_tap_on_background_holds_the_background():
    frames = parallax(4, 20, 4)
    al = find(frames, (0.1, 0.1))
    assert al.status in ("aligned", "moved")
    assert near(refine(frames, al), [(4 * (i - 1), 0) for i in range(4)])


def test_tap_on_another_frame():
    frames = parallax(4, 20, 4)
    al = find(frames, ((320 + 40) / 640, 0.5, 3))   # the disc centre as frame 3 shows it
    assert al.status == "aligned"
    assert near(refine(frames, al), FG)


def test_plain_center_moves_to_nearby_detail():
    frames = parallax(4, 0, 6, disc=False)
    for f in frames:   # a flat square, 10% of the width, in the middle of each frame's content
        ImageDraw.Draw(f).rectangle([288, 208, 352, 272], fill=(120, 120, 120))
    frames = [shifted(f, 0, 0) for f in frames]
    al = find(frames)
    assert al.status == "moved"
    assert abs(al.point[0] - 0.5) <= NEAREST_MAX and abs(al.point[1] - 0.5) * 0.75 <= NEAREST_MAX
    assert near(refine(frames, al), [(6 * (i - 1), 0) for i in range(4)])


@pytest.mark.parametrize("kind", ["grey", "dark"])
def test_flat_or_dark_shot_is_unaligned(kind):
    rnd = random.Random(2)
    if kind == "grey":
        frames = [Image.new("RGB", (640, 480), (128, 128, 128)) for _ in range(4)]
    else:
        frames = [Image.frombytes("RGB", (640, 480), bytes(rnd.randrange(6) for _ in range(640 * 480 * 3)))
                  for _ in range(4)]
    al = find(frames)
    assert al.status == "none" and al.confidence == 0.0
    assert al.shifts == ((0.0, 0.0),) * 4 and al.crop == (0.0, 0.0, 1.0, 1.0)


def test_repeating_pattern_is_unaligned():
    stripes = Image.new("L", (680, 480))
    stripes.putdata([255 if (x // 6) % 2 else 0 for y in range(480) for x in range(680)])
    frames = [stripes.crop((8 * i, 0, 8 * i + 640, 480)).convert("RGB") for i in range(4)]
    assert find(frames).status == "none"


def noise(size=(640, 480), seed=9):
    return Image.frombytes("RGB", size, random.Random(seed).randbytes(size[0] * size[1] * 3))


def test_one_weak_lens_is_filled_in():
    frames = parallax(4, 20, 4)
    frames[3] = noise()
    al = find(frames)
    assert al.status == "aligned"
    assert near(refine(frames, al)[:3], FG[:3]) and abs(round(al.shifts[3][0] * 640) - 40) <= 1
    frames[2] = noise(seed=10)
    assert find(frames).status == "none"


def test_point_near_the_edge():
    al = find(parallax(4, 20, 4), (0.99, 0.01))
    assert al.status in ("aligned", "moved", "none")


def test_tiny_and_single_frames():
    assert find([textured_rgb(40, 30)] * 4).status == "none"
    assert find(parallax(1, 0, 0)).status == "none"


def test_same_decisions_at_any_size():
    frames = parallax(4, 20, 4)
    big = [f.resize((1280, 960), Image.Resampling.LANCZOS) for f in frames]
    a, b = find(frames), find(big)
    assert a.status == b.status
    assert abs(a.point[0] - b.point[0]) <= 1 / 320 and abs(a.point[1] - b.point[1]) <= 1 / 240
    assert all(abs(p - q) <= 1 / 320 for s, t in zip(a.shifts, b.shifts) for p, q in zip(s, t))


def test_crop_boxes_keep_the_shape():
    boxes = crop_boxes((640, 480), FG)
    sizes = {(r - l, b - t) for l, t, r, b in boxes}
    assert len(sizes) == 1
    (cw, ch), = sizes
    assert abs(cw * 3 - ch * 4) <= 4
    assert all(l >= 0 and t >= 0 and r <= 640 and b <= 480 for l, t, r, b in boxes)
    assert crop_boxes((640, 480), [(0, 0)] * 4) == [(0, 0, 640, 480)] * 4


def test_alignment_records_what_happened():
    al = find(parallax(4, 20, 4))
    assert isinstance(al, align.Alignment)
    assert al.aspect == pytest.approx(640 / 480)
    assert 0.6 <= al.confidence <= 1.0 and al.gains == ()
    assert al.crop != (0.0, 0.0, 1.0, 1.0)


# ---------------------------------------------------------------- even lighting

from giffy import even  # noqa: E402
from giffy.even import GAIN_MAX, GAIN_MIN  # noqa: E402


def scaled(im: Image.Image, k: tuple[float, float, float]) -> Image.Image:
    return Image.merge("RGB", [b.point(lambda v, f=f: min(255, round(v * f))) for b, f in zip(im.split(), k)])


def channel_means(im: Image.Image, box=None):
    return ImageStat.Stat(im if box is None else im.crop(box)).mean


def spread(frames, box=None) -> float:
    """Largest relative difference between frames' channel means."""
    ms = [channel_means(f, box) for f in frames]
    return max((max(m[c] for m in ms) - min(m[c] for m in ms)) / (sum(m[c] for m in ms) / len(ms)) for c in range(3))


def test_even_removes_lens_differences():
    base = textured_rgb(320, 240, 5)
    frames = [base, scaled(base, (1.08, 0.95, 1.0)), base, scaled(base, (0.93, 1.04, 1.06))]
    assert spread(frames) > 0.05
    g = even.gains(frames)
    assert spread([even.apply(f, gi) for f, gi in zip(frames, g)]) < 0.01


def test_even_leaves_matched_frames_alone():
    base = textured_rgb(320, 240, 5)
    assert all(abs(v - 1.0) < 1e-6 for gi in even.gains([base] * 4) for v in gi)
    assert even.apply(base, (1.0, 1.0, 1.0)) is base


def test_even_ignores_content_differences():
    g = even.gains(parallax(4, 20, 4))
    assert all(abs(v - 1.0) <= 0.01 for gi in g for v in gi), g


def test_even_is_capped():
    base = textured_rgb(320, 240, 5)
    g = even.gains([base, scaled(base, (0.5, 0.5, 0.5)), base, base])
    assert all(v <= GAIN_MAX for v in g[1]) and all(GAIN_MIN <= v for gi in g for v in gi)


def test_even_skips_clipped_pixels():
    base = textured_rgb(320, 240, 5)
    ImageDraw.Draw(base).rectangle([0, 0, 223, 239], fill=(255, 255, 255))   # 70% pure white
    frames = [base, scaled(base, (1.05, 1.05, 1.05)), base, base]
    mid = (224, 0, 320, 240)
    assert spread(frames, mid) > 0.03
    g = even.gains(frames)
    assert spread([even.apply(f, gi) for f, gi in zip(frames, g)], mid) < 0.01
