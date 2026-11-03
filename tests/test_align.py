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
    dx, dy, best, second, edge = match(ref, shifted(ref, 17, -3), 100, 80, 32)
    assert not edge
    assert (dx, dy) == (17, -3)
    assert best > 0.99


def test_match_ignores_brightness_and_contrast():
    ref = textured()
    other = Image.eval(shifted(ref, 9, 2), lambda v: int(v * 0.7 + 30))
    dx, dy, best, _, _ = match(ref, other, 100, 80, 32)
    assert (dx, dy) == (9, 2)
    assert best > 0.98


def test_match_second_peak_on_repeats():
    stripes = Image.new("L", (320, 240))
    stripes.putdata([255 if (x // 6) % 2 else 0 for y in range(240) for x in range(320)])
    _, _, best, second, _ = match(stripes, stripes, 100, 80, 32)
    assert second > 0.9
    ref = textured()
    _, _, best, second, _ = match(ref, ref, 100, 80, 32)
    assert second < best - 0.1


def test_match_flags_a_best_spot_at_the_frame_edge():
    ref = textured()
    other = shifted(ref, -6, 0)          # the content moved left, off the frame
    m = match(ref, other, 0, 80, 32, rx=10, ry=2)   # patch flush with the left edge
    assert len(m) == 5 and m[4] is True


def test_match_flat_patch_scores_zero():
    flat = Image.new("L", (320, 240), 90)
    _, _, best, _, _ = match(flat, textured(), 100, 80, 32)
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
    big = bytes(4100 * 4100)
    with pytest.raises(ValueError):   # rows past 4096 px could overflow the 32-bit row sums
        _giffy.match(big, big, 4100, 4100, 0, 0, 4097, 0, 0, 1, 1)
    _, _, best, second, edge = _giffy.match(b, b, 320, 240, 10, 10, 32, 1000, 0, 4, 4)
    assert best == -1.0 and second == -1.0 and edge


# ---------------------------------------------------------------- alignment decisions

from giffy import align  # noqa: E402
from giffy.align import NEAREST_MAX, crop_boxes, find, refine  # noqa: E402


def textured_rgb(w, h, seed=1) -> Image.Image:
    return Image.merge("RGB", [textured(w, h, seed + k) for k in range(3)])


def parallax(n, fg_shift, bg_shift, size=(640, 480), seed=3, disc=True, shake=None):
    """n frames of a textured background moving bg_shift px per lens, with a
    textured disc in the middle moving fg_shift px per lens (ref = (n - 1) // 2).
    shake: optional per-frame vertical offset of the whole frame, px."""
    w, h = size
    ref, m = (n - 1) // 2, abs(bg_shift) * n + 8
    v = max([abs(s) for s in shake or [0]]) + 1
    bg = textured_rgb(w + 2 * m, h + 2 * v, seed)
    fg = textured_rgb(w, h, seed + 10)
    r = round(0.15 * w)
    frames = []
    for i in range(n):
        sy = v - (shake[i] if shake else 0)
        f = bg.crop((m - bg_shift * (i - ref), sy, m - bg_shift * (i - ref) + w, sy + h))
        if disc:
            cx, cy = w // 2 + fg_shift * (i - ref), h // 2 + (shake[i] if shake else 0)
            mask = Image.new("L", (w, h))
            ImageDraw.Draw(mask).ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)
            f.paste(shifted(fg, fg_shift * (i - ref), shake[i] if shake else 0), (0, 0), mask)
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


def test_hand_shake_up_and_down():
    shake = [20, 0, -18, 10]   # px at 480 high: beyond a 3% search, inside 6%
    frames = parallax(4, 20, 4, shake=shake)
    al = find(frames)
    assert al.status == "aligned"
    assert near(refine(frames, al), [(dx, sy) for (dx, _), sy in zip(FG, shake)])


def test_shake_beyond_the_search_is_unaligned():
    frames = parallax(4, 20, 4, shake=[68, 0, -68, 34])   # 3 px (analysis) past the 6% search
    assert find(frames).status == "none"


def test_a_different_view_is_not_matched():
    frames = parallax(4, 20, 4)
    frames[3] = textured_rgb(640, 480, 77)   # another scene altogether
    al = find(frames)
    assert al.status == "aligned"            # filled in from its neighbours, not matched
    assert abs(round(al.shifts[3][0] * 640) - 40) <= 1


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
    assert align.NCC_MIN <= al.confidence <= 1.0 and al.gains == ()
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


# ---------------------------------------------------------------- pipeline

import giffy  # noqa: E402
import refdec  # noqa: E402
from giffy import Shot, encode, encode_shot, make_gif  # noqa: E402
from test_giffy import FFMPEG, decode_rgb, expected_rgb, ffmpeg_rgb  # noqa: E402


def gif_frames(data: bytes, tmp_path):
    p = tmp_path / "a.gif"
    p.write_bytes(data)
    out = []
    with Image.open(p) as im:
        for i in range(im.n_frames):
            im.seek(i)
            out.append(im.convert("RGB"))
    return out


def held_still(frames) -> bool:
    """The centre of every frame matches frame 0's centre at offset (0, 0) +- 1 px."""
    ref = frames[0].convert("L")
    w, h = ref.size
    p = max(8, w // 10)
    x, y = (w - p) // 2, (h - p) // 2
    for f in frames[1:]:
        dx, dy, best, _, _ = _giffy.match(ref.tobytes(), f.convert("L").tobytes(), w, h, x, y, p, 0, 0, 8, 4)
        if abs(dx) > 1 or abs(dy) > 1 or best < 0.8:
            return False
    return True


def test_aligned_gif_holds_the_point(tmp_path):
    frames = gif_frames(encode(parallax(4, 20, 4)), tmp_path)
    assert held_still(frames)
    w, h = frames[0].size
    assert abs(w * 3 - h * 4) <= 4 and w < 640
    assert not held_still(gif_frames(encode(parallax(4, 20, 4), align=None, even=False), tmp_path))


def test_width_is_the_final_width(tmp_path):
    w, h = gif_frames(encode(parallax(4, 20, 4), width=320), tmp_path)[0].size
    assert w == 320 and abs(h - 240) <= 1


def test_preview_and_full_hold_the_same_point(tmp_path):
    frames = parallax(4, 20, 4)
    preview = encode_shot(frames, width=160)
    full = encode_shot(frames, width=640, align=preview.alignment)
    assert isinstance(preview, Shot) and preview.alignment.status == full.alignment.status == "aligned"
    assert full.alignment.point == preview.alignment.point and full.alignment.gains == preview.alignment.gains
    assert held_still(gif_frames(preview.gif, tmp_path)) and held_still(gif_frames(full.gif, tmp_path))


def test_reused_alignment_must_fit():
    al = encode_shot(parallax(4, 20, 4)).alignment
    with pytest.raises(ValueError, match="different shot"):
        encode_shot(parallax(3, 20, 4), align=al)
    with pytest.raises(ValueError, match="different shot"):
        encode_shot(parallax(4, 20, 4, size=(640, 360)), align=al)


def test_far_subject_no_crop(tmp_path):
    shot = encode_shot(parallax(4, 0, 0))
    assert gif_frames(shot.gif, tmp_path)[0].size == (640, 480)


def test_single_and_tiny(tmp_path):
    one = encode_shot([textured_rgb(200, 150)])
    assert one.alignment.status == "none" and gif_frames(one.gif, tmp_path)[0].size == (200, 150)
    tiny = encode_shot([textured_rgb(40, 30, s) for s in range(4)])
    assert tiny.alignment.status == "none" and gif_frames(tiny.gif, tmp_path)[0].size == (40, 30)


def test_threads_never_change_the_bytes_aligned():
    frames = parallax(4, 20, 4)
    one = encode(frames, threads=1)
    assert encode(frames, threads=3) == one and encode(frames, threads=4) == one


def test_make_gif_drop_in(tmp_path):
    paths = []
    for i, f in enumerate(parallax(4, 20, 4, size=(800, 600))):
        paths.append(tmp_path / f"cam{i + 1}.jpg")
        f.save(paths[-1], quality=92)
    out = make_gif(paths, tmp_path / "w.gif", 640, 100)
    assert out == tmp_path / "w.gif"
    frames = decode_rgb(out)
    assert len(frames) == 6
    gif = out.read_bytes()
    pal = gif[13:13 + 3 * (2 << (gif[10] & 7))]
    assert [expected_rgb(i, pal) for i in refdec.decode(gif)] == frames
    with Image.open(out) as g:
        assert g.size[0] == 640
        if FFMPEG:
            assert ffmpeg_rgb(out) == frames


def test_cli_align_flags(tmp_path):
    paths = []
    for i, f in enumerate(parallax(4, 20, 4)):
        paths.append(tmp_path / f"cam{i + 1}.png")
        f.save(paths[-1])
    giffy.main([str(tmp_path / "n.gif"), *map(str, paths), "--no-align"])
    assert (tmp_path / "n.gif").read_bytes() == encode(paths, align=None)
    giffy.main([str(tmp_path / "t.gif"), *map(str, paths), "--align-at", "0.1", "0.1"])
    assert (tmp_path / "t.gif").read_bytes() == encode(paths, align=(0.1, 0.1))
    giffy.main([str(tmp_path / "f.gif"), *map(str, paths), "--align-at", "0.5625", "0.5", "3"])
    assert (tmp_path / "f.gif").read_bytes() == encode(paths, align=(0.5625, 0.5, 3))


def test_unaligned_shot_is_not_evened():
    frames = [textured_rgb(640, 480, s) for s in (1, 20, 40, 60)]   # four unrelated views
    frames[1] = scaled(frames[1], (0.7, 0.7, 0.7))
    shot = encode_shot(frames)
    assert shot.alignment.status == "none"
    assert shot.alignment.gains == ((1.0, 1.0, 1.0),) * 4


# ---------------------------------------------------------------- final review fixes

def test_moved_point_holds_at_full_size():
    # flat frame, detail starting 180 px right of centre: find moves the point onto it
    w, h = 2592, 1944
    square = textured_rgb(420, 420, 31)
    frames = []
    for i in range(4):
        f = Image.new("RGB", (w, h), (128, 128, 128))
        f.paste(square, (w // 2 + 180 + 30 * (i - 1), h // 2 - 210))
        frames.append(f)
    al = find(frames)
    assert al.status == "moved"
    assert near(refine(frames, al), [(30 * (i - 1), 0) for i in range(4)])


def test_tap_in_the_corner_is_right_or_unaligned():
    frames = parallax(4, 20, 4)
    al = find(frames, (1.0, 1.0))
    assert al.status == "none" or near(refine(frames, al), [(4 * (i - 1), 0) for i in range(4)]), al


def test_no_align_means_no_evening_unless_asked():
    frames = [textured_rgb(640, 480, s) for s in (1, 20, 40, 60)]
    assert encode(frames, align=None) == encode(frames, align=None, even=False)
    assert encode(frames, align=None, even=True) != encode(frames, align=None, even=False)


def test_align_point_as_a_list():
    frames = parallax(4, 20, 4)
    want = encode(frames, order=[1, 2, 3], align=(0.5625, 0.5, 3))
    assert encode(frames, order=[1, 2, 3], align=[0.5625, 0.5, 3]) == want
