# giffy

2026-11-02 · Christopher Luu

Wiggle GIFs at the photos' full size, sharper, smaller and 2–5× faster than shimmy's tuned Pillow encoder. Sister project to [shimmy](https://github.com/chrxsluu/shimmy).

## Use it

```python
from giffy import make_gif

make_gif(["lens1.jpg", "lens2.jpg", "lens3.jpg", "lens4.jpg"], "shot.gif")              # full size
make_gif(paths, "share.gif", width=1080)                                              # resized
make_gif(paths, "best.gif", lossy=0)                                                   # max quality
```

```bash
giffy shot.gif lens1.jpg lens2.jpg lens3.jpg lens4.jpg [--width 1080] [--lossy 4] [--forward]
```

`make_gif(images, out, width=None, delay_ms=100, order=None, colors=256, dither=True, lossy=4, threads=None)` takes paths or PIL images and plays them 1-2-3-4-3-2, looping forever. `encode(...)` returns the GIF as bytes. It applies EXIF orientation, drops alpha, and raises `ValueError` if the photos come out different sizes. Shots with 256 colours or fewer are stored exactly (lossless).

It is a drop-in for shimmy's `shimmy_spike.wiggle.make_gif(paths, out, width, delay_ms)`. shimmy's 640 px floor stays in shimmy.

## Install

```bash
uv add ~/Documents/dev/code/giffy     # or: pip install ~/Documents/dev/code/giffy
```

It builds one C file on install, so the machine needs a C compiler and Python headers. On Raspberry Pi OS: `sudo apt install build-essential python3-dev`. Needs Python 3.11+ and Pillow 9.4+.

## Results

4 lens photos per shot, 4 threads, median of 5 runs on an M2 Pro. Quality is measured against the photos at the GIF's size: PSNR, and PSNR after a 1 px blur ("blur"), which is closer to what the eye sees through dither noise. Higher is better for both. Size is the whole 6-frame file.

| Shot | Size | Pillow ms | giffy ms | Pillow MB | giffy MB | Pillow PSNR / blur | giffy PSNR / blur |
|---|---|---|---|---|---|---|---|
| synthetic 2592×1944 | full | 462 | **155** | 17.73 | **17.63** | 25.5 / 38.4 | **30.4 / 42.9** |
| | 1080 | 112 | **62** | **1.88** | 2.15 | 30.4 / 42.4 | **32.5 / 44.0** |
| | 320 | 29 | **24** | 0.16 | **0.14** | 33.9 / 44.6 | **36.0 / 45.3** |
| street 1728×2592 | full | 368 | **138** | 6.58 | **6.07** | 35.9 / 42.9 | **38.3 / 45.6** |
| | 1080 | 191 | **99** | 2.66 | **2.47** | 36.1 / 43.2 | **38.4 / 45.8** |
| | 320 | 33 | **21** | 0.26 | **0.24** | 36.1 / 43.7 | **38.0 / 46.1** |
| evening 2592×1728 | full | 324 | **138** | 3.43 | **2.46** | 41.2 / 51.7 | **45.2 / 53.4** |
| | 1080 | 84 | **46** | 0.68 | **0.40** | 41.3 / 51.8 | **44.9 / 53.0** |
| | 320 | 17 | **12** | 0.06 | **0.04** | 41.3 / 51.9 | **44.0 / 53.2** |
| night 2592×1728 | full | 295 | **54** | 1.71 | **0.36** | 39.7 / 48.0 | **51.8 / 54.1** |
| | 1080 | 73 | **26** | 0.31 | **0.08** | 40.0 / 49.1 | **50.9 / 54.1** |
| | 320 | 12 | **5** | 0.03 | **0.01** | 39.9 / 49.2 | **49.4 / 54.8** |
| midnight 2592×1728 | full | 412 | **143** | 12.81 | **10.84** | 35.7 / 47.5 | **39.0 / 47.8** |
| | 1080 | 96 | **46** | 2.20 | **1.84** | 35.6 / 47.6 | **38.4 / 47.9** |
| | 320 | 18 | **11** | 0.21 | **0.19** | 35.0 / 47.3 | **37.1 / 47.6** |

giffy is faster and sharper on every row and smaller on all but one. The exception is the synthetic scene at 1080, which is 14% bigger: its heavy artificial noise suits Pillow's coarser palette. Pass `lossy=6` or more for smaller files.

On a slower 4-core Linux box (Intel N150, gcc), the synthetic shot runs 718 → 370 ms full, 175 → 124 ms at 1080, and 47 → 39 ms at 320.

> [!NOTE]
> The real shots are 4-photo handheld bursts from a Fuji camera, downscaled to OV5640 size (2592 px on the long side). Real lens rigs, and the Pi's ARM cores, still need measuring.

Rerun with `uv run python bench/bench.py [PHOTO_DIR ...]` (one directory per shot). `bench/stages.py` splits the time by stage.

## How it works

Pillow still decodes the JPEGs with libjpeg-turbo, in parallel, and handles resizing (JPEG draft decode, then Lanczos). Everything after that is one C file, `src/giffy/_giffy.c`, which releases the GIL so frames run on separate cores.

1. **One palette for the whole shot.** Exact colours if there are 256 or fewer. Otherwise Wu's variance split on a 5-bit histogram of about 1M sampled pixels, then one k-means pass. It's a far better fit than Pillow's FASTOCTREE, and is where most of the quality gain comes from.
2. **Dither and compress in one pass.** Floyd–Steinberg at 14/16 strength (full strength mostly adds noise to flat areas) feeds the LZW encoder pixel by pixel. A shared nearest-colour table (6 bits per channel) fills in as it goes.
3. **Lossy LZW, with the error diffused.** When a pixel's nearest colour would end the current LZW string, it may take a colour that continues the string instead, if that colour is at most `lossy` RGB units further from its target. The extra error goes to its neighbours like any dither error, so areas keep their true colour. This is what makes the files smaller than Pillow's despite the finer palette.
4. **Each unique frame is encoded once.** The 1-2-3-4-3-2 loop writes frames 2 and 3 twice from the same bytes, under one global colour table. Pillow re-encodes all six frames on one core.

Output is deterministic: the same photos give the same bytes, whatever the thread count.

## Tests

```bash
uv run pytest
```

There are 54 tests. Every stream is decoded by three independent decoders that must agree: Pillow, ffmpeg (when installed) and a plain-Python reference (`tests/refdec.py`). They cover sizes from 1×1 up to 25 MP (enough table resets to wrap the hash generation counter), every min code size, lossless small palettes, lossy colour fidelity, EXIF orientation, resizing, thread-count determinism, bad arguments and randomised shots. The suite also passes under AddressSanitizer and UBSan (clang on macOS, gcc on Linux) and on Python 3.11 with Pillow 9.4 (Raspberry Pi OS Bookworm's versions).
