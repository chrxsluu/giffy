import random

import pytest
from PIL import Image, ImageDraw, ImageFilter

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
