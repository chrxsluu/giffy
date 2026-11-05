# giffy

2026-11-02 · Christopher Luu

Fast, full-quality GIFs from a handful of photos, made for [shimmy](https://github.com/chrxsluu/shimmy), the four-lens wigglecam. giffy turns the lens photos into a wiggle GIF that keeps their full size. Out of the box it lines the frames up on one point and evens out the lenses' lighting. It also makes plain GIFs.

Against Pillow doing the same job, giffy is 4.6–14.6× faster than plain Pillow and 1.2–3.0× faster than shimmy's tuned Pillow. It is sharper in every case, and makes the smaller file in 24 of 27 cases (see [Results](#results)).

## Modes

| Mode | Plays | Lined up and evened out |
|---|---|---|
| `normal` | once, in order (1-2-3-4) | no |
| `loop` | back and forth (1-2-3-4-3-2), forever | no |
| `shimmy` (default) | back and forth, forever | yes, on the centre |

Options you pass yourself win over the mode's choices: `order=` sets the playback, `align=` lines up any mode, and `align=None` turns alignment off.

## Install

```bash
uv add git+https://github.com/chrxsluu/giffy     # or: pip install git+https://github.com/chrxsluu/giffy
```

The repo is private, so the machine needs GitHub access (a `gh auth login` or an SSH key). giffy compiles one C file on install, so it also needs a C compiler and Python headers. On Raspberry Pi OS:

```bash
sudo apt install build-essential python3-dev
```

It needs Python 3.11+ and Pillow 9.4+, and has no other dependencies.

## Use it

```python
from giffy import make_gif

paths = ["lens1.jpg", "lens2.jpg", "lens3.jpg", "lens4.jpg"]
make_gif(paths, "shot.gif")                           # shimmy: full size, lined up on the centre
make_gif(paths, "share.gif", width=1080)              # the finished GIF is 1080 px wide
make_gif(paths, "cup.gif", align=(0.3, 0.6))          # hold this spot still instead
make_gif(paths, "wiggle.gif", mode="loop")            # back and forth, frames as shot
make_gif(paths, "plain.gif", mode="normal")           # an ordinary GIF that plays once
make_gif(paths, "best.gif", lossy=0)                  # best quality, bigger file
```

```bash
giffy shot.gif lens1.jpg lens2.jpg lens3.jpg lens4.jpg [--mode normal|loop|shimmy] [--width 1080]
      [--lossy 4] [--forward] [--align-at X Y [FRAME]] [--no-align] [--no-even] [--delay MS]
```

`make_gif(images, out, width=None, delay_ms=100, order=None, colors=256, dither=True, lossy=4, threads=None, align=<by mode>, even=None, mode="shimmy")` takes paths or PIL images and returns the output path. `encode(...)` takes the same arguments and returns the GIF as bytes.

What it handles for you:
- **Rotation:** photos come out the right way up, using the camera's EXIF orientation.
- **Alpha** is dropped.
- **Exact colours:** shots with 256 colours or fewer are stored exactly.
- **Mismatched sizes:** if the photos come out different sizes, it raises `ValueError` instead of guessing.
- **Oversized or forged photos:** a photo over `giffy.MAX_PIXELS` (50 MP) is rejected from its header, before anything is decoded, so a bad file can't use up memory.
- **Determinism:** the same photos always give the same bytes, whatever the thread count.

### Alignment

In shimmy mode, the frames are lined up so one point holds still while the scene rotates around it:

- **Which point:** the centre by default. `align=(x, y)` picks another spot, as fractions of the middle frame. `align=(x, y, frame)` takes a spot as seen in a given frame, which is what a tap on a playing GIF gives you.
- **Plain spots:** if the point is plain (sky, a blank wall) or a repeating pattern, giffy holds the nearest unique detail still instead, within 15% of the frame width.
- **No reliable match:** if nothing matches reliably, the frames stay as shot rather than lining up on a guess.
- **Crop:** the frames are cropped to the area they all share, keeping the photos' shape (4:3 on the rig). `width` is the finished width, after the crop.
- **Lighting:** each lens's brightness and colour are evened out so the wiggle doesn't flicker, without changing the shot's overall look.

For shimmy's screen, `encode_shot(...)` returns a `Shot` with the GIF and the `Alignment` it used. The alignment records the shifts, the crop, the lighting gains, a confidence score, and a status: `"aligned"`, `"moved"` (nearby detail was used) or `"none"` (frames left as shot). Make the 320 px preview first, then hand its alignment to the full GIF, so both hold exactly the same point:

```python
from giffy import encode_shot

preview = encode_shot(paths, width=320)                         # on screen first
full = encode_shot(paths, width=1080, align=preview.alignment)   # same still point and lighting
again = encode_shot(paths, width=320, align=(0.3, 0.6, 2))       # re-align on a tap seen in frame 3
```

`make_gif(paths, out, width, delay_ms)` is a drop-in for shimmy's earlier `shimmy_spike.wiggle.make_gif`.

## Results

Median of 3 runs on an M2 Pro, starting from the JPEG files. These are the 1080 px times and file sizes.

- **Pillow default** is the plain `frames[0].save(out, save_all=True, ...)` call.
- **Tuned Pillow** is shimmy's earlier encoder: one shared palette, Floyd–Steinberg dithering, 4 threads.
- **Shimmy mode for Pillow:** Pillow can't align, so in shimmy mode both Pillow versions encode giffy's aligned frames.

| Shot | Mode | Pillow default | Tuned Pillow | giffy |
|---|---|---|---|---|
| Real skyline, 3 photos | normal | 264 ms, 1.35 MB | 86 ms, 1.12 MB | **45 ms, 0.98 MB** |
| | loop | 336 ms, 1.79 MB | 96 ms, 1.51 MB | **46 ms, 1.30 MB** |
| | shimmy | 335 ms, 1.81 MB | 84 ms, 1.54 MB | **51 ms, 1.31 MB** |
| Real street, 4 photos | loop | 688 ms, 4.95 MB | 190 ms, 2.66 MB | **97 ms, 2.47 MB** |
| Synthetic, 4 photos | loop | 759 ms, 2.59 MB | 133 ms, **1.88 MB** | **62 ms**, 2.15 MB |

- **Full size:** the gap widens. Synthetic loop takes 2,265 ms with plain Pillow, 458 ms tuned and 155 ms with giffy.
- **Quality:** giffy's perceived quality (PSNR after a 1 px blur) is the highest in every case. Plain Pillow doesn't dither, so it bands, and scores 3–5 dB lower.
- **The one size miss:** the synthetic scene at 1080 px, where tuned Pillow's file is 11–13% smaller. Its heavy artificial noise suits Pillow's coarser palette.
- **Alignment cost:** about 1–11 ms per shot.
- **Slower hardware:** on a 4-core Intel N150 running Linux, the synthetic loop runs in 370 ms against tuned Pillow's 718 ms at full size.

Full tables: [docs/benchmarks/modes.md](docs/benchmarks/modes.md) (all three modes, all sizes) and [docs/benchmarks/encoder.md](docs/benchmarks/encoder.md) (the first encoder comparison, five shots). The Raspberry Pi itself hasn't been measured yet.

## How it works

Pillow decodes the JPEGs with libjpeg-turbo, in parallel, and does the resizing (JPEG draft decode, then Lanczos). Alignment and evening run in Python on small copies (`align.py`, `even.py`), with the patch matching in C. Everything after that is one C file, `src/giffy/_giffy.c`, which releases the GIL so frames run on separate cores.

1. **Line up, crop, even out** (shimmy mode).
   - **Matching:** on a 320 px grey copy, giffy finds where the detail around the still point sits in each frame. It uses zero-mean normalised cross-correlation, so a brighter lens can't fool it.
   - **All or nothing:** every frame is aligned, or none is. One weak lens is filled in from its evenly spaced neighbours.
   - **Finish:** the shifts are refined at full resolution, the frames are cropped to their shared area, and each lens gets per-channel gains that multiply to 1.
2. **One palette for the whole shot.** Exact colours if there are 256 or fewer. Otherwise Wu's variance split on a 5-bit histogram of about 1M sampled pixels, then one k-means pass. It fits far better than Pillow's FASTOCTREE, and is where most of the quality gain comes from.
3. **Dither and compress in one pass.** Floyd–Steinberg at 14/16 strength (full strength mostly adds noise to flat areas) feeds the LZW encoder pixel by pixel.
4. **Lossy LZW, with the error diffused.** When a pixel's nearest colour would end the current LZW string, it may take a colour up to `lossy` RGB units further away that continues the string. The extra error goes to its neighbours like any dither error, so areas keep their true colour. This is why the files come out smaller than Pillow's despite the finer palette.
5. **Each unique frame is encoded once.** The back-and-forth loop writes frames 2 and 3 twice from the same bytes, under one global colour table.

## Tests

```bash
uv run pytest
```

There are 105 tests. Every stream is decoded by three independent decoders that must agree: Pillow, ffmpeg (when installed) and a plain-Python reference (`tests/refdec.py`).

What they cover:
- **The encoder:** sizes from 1×1 to 25 MP, every LZW code size, lossless small palettes, lossy colour fidelity, EXIF orientation, resizing, thread-count determinism, bad arguments and randomised shots.
- **Alignment:** generated shots with known shifts, covering foreground vs background, taps on other frames, plain spots, flat and dark shots, repeating patterns, a weak lens and taps at the frame edge.
- **Lighting:** known lens colour differences are removed without changing the overall look.
- **Modes:** normal plays once in order, and loop is byte-for-byte giffy's output from before alignment existed.

The suite also passes under AddressSanitizer and UBSan (clang on macOS, gcc on Linux), and on Python 3.11 with Pillow 9.4 (Raspberry Pi OS Bookworm's versions).

## Layout

```text
src/giffy/__init__.py   pipeline, modes, make_gif / encode / encode_shot, CLI
src/giffy/align.py      finding the still point, refining shifts, the shared crop
src/giffy/even.py       per-lens lighting gains
src/giffy/_giffy.c      palette, dither + lossy LZW, plain LZW, patch matching
tests/                  pytest suite and the reference decoder
bench/                  benchmarks (bring your own photo sets)
docs/                   design, plans and benchmark reports
```

## License

All rights reserved, for now.
