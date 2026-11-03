# giffy encoder vs tuned Pillow

2026-11-02 · Christopher Luu

The first benchmark: giffy's encoder against shimmy's tuned Pillow encoder (one shared FASTOCTREE palette, Floyd–Steinberg, 4 threads), both in loop mode, unaligned.

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

In loop mode, giffy is faster and sharper on every row and smaller on all but one. The exception is the synthetic scene at 1080, which is 14% bigger: its heavy artificial noise suits Pillow's coarser palette. Pass `lossy=6` or more for smaller files.

Alignment and evening add 1–10 ms per shot on the M2 Pro at any size, measured as the extra time of a whole encode.

On a slower 4-core Linux box (Intel N150, gcc), the synthetic shot runs 718 → 370 ms full, 175 → 124 ms at 1080, and 47 → 39 ms at 320.

> [!NOTE]
> The real shots are 4-photo sets from a Fuji camera, taken seconds apart, downscaled to OV5640 size (2592 px on the long side). Real lens rigs, and the Pi's ARM cores, still need measuring.

Rerun with `uv run python bench/bench.py [PHOTO_DIR ...]` (one directory per shot). `bench/stages.py` splits the time by stage. The photo sets are not in the repo.
