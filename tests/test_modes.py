import hashlib
import sys

import PIL
import pytest
from PIL import Image

import giffy
import refdec
from giffy import encode, encode_shot
from test_align import parallax
from test_giffy import FFMPEG, decode_rgb, expected_rgb, ffmpeg_rgb, seeded_scene


def gif_info(data: bytes, tmp_path):
    p = tmp_path / "m.gif"
    p.write_bytes(data)
    with Image.open(p) as im:
        return im.size, im.n_frames, im.info.get("loop"), decode_rgb(p), p


def test_default_mode_is_shimmy():
    frames = parallax(4, 20, 4)
    assert encode(frames) == encode(frames, mode="shimmy")


@pytest.mark.skipif(sys.platform != "darwin" or PIL.__version__ != "13.0.0",
                    reason="hashes were recorded on macOS with Pillow 13.0.0")
def test_loop_mode_is_giffy_before_alignment():
    a = encode(seeded_scene(320, 240), mode="loop")
    b = encode(seeded_scene(400, 300, n=3), width=200, lossy=0, mode="loop")
    assert hashlib.sha256(a).hexdigest() == "7114fde56317d43b4ce656a9110ac5076c789ca31986bcb52b87bbca34cbf6aa"
    assert hashlib.sha256(b).hexdigest() == "f1253de05298e1115fb40ec00ab043c1c1835d6086ad0bd25c4d2558a0e7e085"


def test_loop_mode_bounces_forever_unaligned(tmp_path):
    shot = encode_shot(parallax(4, 20, 4), mode="loop")
    size, n, loop, frames, _ = gif_info(shot.gif, tmp_path)
    assert size == (640, 480) and n == 6 and loop == 0 and shot.alignment is None
    assert [frames.index(f) for f in frames] == [0, 1, 2, 3, 2, 1]


def test_normal_mode_plays_once_in_order(tmp_path):
    shot = encode_shot(parallax(4, 20, 4), mode="normal")
    size, n, loop, frames, p = gif_info(shot.gif, tmp_path)
    assert size == (640, 480) and n == 4 and loop is None and shot.alignment is None
    assert b"NETSCAPE2.0" not in shot.gif
    assert len(set(frames)) == 4
    gif = p.read_bytes()
    pal = gif[13:13 + 3 * (2 << (gif[10] & 7))]
    assert [expected_rgb(i, pal) for i in refdec.decode(gif)] == frames
    if FFMPEG:
        assert ffmpeg_rgb(p) == frames


def test_normal_mode_single_photo(tmp_path):
    size, n, loop, _, _ = gif_info(encode([parallax(1, 0, 0)[0]], mode="normal"), tmp_path)
    assert size == (640, 480) and n == 1 and loop is None


def test_shimmy_mode_aligns_and_loops(tmp_path):
    shot = encode_shot(parallax(4, 20, 4), mode="shimmy")
    size, n, loop, _, _ = gif_info(shot.gif, tmp_path)
    assert shot.alignment.status == "aligned" and size[0] < 640 and n == 6 and loop == 0


def test_explicit_options_override_the_mode(tmp_path):
    frames = parallax(4, 20, 4)
    _, n, loop, _, _ = gif_info(encode(frames, mode="normal", order=[0, 1, 0]), tmp_path)
    assert n == 3 and loop is None
    shot = encode_shot(frames, mode="loop", align=(0.5, 0.5))
    assert shot.alignment.status == "aligned"
    assert encode(frames, mode="shimmy", align=None) == encode(frames, mode="loop")
    _, n, loop, _, _ = gif_info(encode(frames, mode="normal", align=(0.5, 0.5)), tmp_path)
    assert n == 4 and loop is None


def test_unknown_mode():
    with pytest.raises(ValueError, match="mode"):
        encode(parallax(2, 0, 0), mode="boomerang")


def test_cli_modes(tmp_path):
    paths = []
    for i, f in enumerate(parallax(4, 20, 4)):
        paths.append(tmp_path / f"cam{i + 1}.png")
        f.save(paths[-1])
    for mode in ("normal", "loop", "shimmy"):
        out = tmp_path / f"{mode}.gif"
        giffy.main([str(out), *map(str, paths), "--mode", mode])
        assert out.read_bytes() == encode(paths, mode=mode), mode
    giffy.main([str(tmp_path / "d.gif"), *map(str, paths)])
    assert (tmp_path / "d.gif").read_bytes() == encode(paths, mode="shimmy")
