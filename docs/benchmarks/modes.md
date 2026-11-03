# giffy modes vs Pillow

2026-11-02 · Christopher Luu

giffy is faster than both Pillow baselines in all 27 cases (3 shots × 3 sizes × 3 modes): **4.6–14.6×** faster than plain Pillow and **1.2–3.0×** faster than tuned Pillow. It gives the best perceived quality in every case, and the smallest file in 24 of 27; the three exceptions are the synthetic noise scene at 1080 px, where tuned Pillow is 11–13% smaller.

## How it was run

- **The machine and runs:** an M2 Pro, with each case run 3 times and the median kept. Times start from the JPEG files, so decoding counts for every encoder.
- **The modes:**
  - `normal`: frames 1-2-3-4, played once, with no loop block.
  - `loop`: 1-2-3-4-3-2, looping forever.
  - `shimmy`: loop, plus lining up and evening out.
- **The three encoders:**
  - **Pillow default:** what you'd write by hand, `frames[0].save(out, save_all=True, append_images=frames[1:], duration=100, loop=0)`, with the `loop` argument left out for `normal`.
  - **Tuned Pillow:** shimmy's earlier winner. One shared FASTOCTREE palette, Floyd–Steinberg dithering and 4 threads.
  - **giffy:** `make_gif(paths, out, width, mode=...)`.
- **Shimmy mode for the Pillow encoders:** Pillow can't align photos, so both Pillow versions first run giffy's own alignment and lighting code (timed with them), then encode the same frames.
- **Quality** is measured against the frames each encoder was given: the photos at the GIF's size, or, for shimmy, the aligned frames. **Blur** is PSNR after a 1 px blur, which is closer to what the eye sees through dither, so read it as the main quality number. Higher is better for both.

> [!NOTE]
> Plain Pillow often scores the highest *raw* PSNR on the real photos because it doesn't dither. That leaves visible colour banding, which is why its blur scores are 3–5 dB lower than giffy's.

## Synthetic scene, 4 × 2592×1944

| Size | Mode | Time, ms (Pillow / tuned / giffy) | File, MB (Pillow / tuned / giffy) | Blur dB (Pillow / tuned / giffy) | giffy speed-up vs Pillow / tuned |
|---|---|---|---|---|---|
| full | normal | 1546 / 351 / **155** | 12.43 / 11.82 / **11.75** | 33.6 / 38.4 / **42.9** | 10.0× / 2.3× |
| full | loop | 2265 / 458 / **155** | 18.65 / 17.73 / **17.63** | 33.6 / 38.4 / **42.9** | 14.6× / 3.0× |
| full | shimmy | 2156 / 444 / **159** | 17.70 / 16.90 / **16.70** | 33.7 / 38.4 / **42.9** | 13.6× / 2.8× |
| 1080 | normal | 524 / 115 / **62** | 1.72 / 1.25 / **1.44** | 34.1 / 42.7 / **44.0** | 8.5× / 1.9× |
| 1080 | loop | 759 / 133 / **62** | 2.59 / 1.88 / **2.15** | 34.1 / 42.7 / **44.0** | 12.2× / 2.1× |
| 1080 | shimmy | 742 / 118 / **68** | 2.63 / 1.96 / **2.20** | 34.3 / 42.4 / **44.7** | 10.9× / 1.7× |
| 320 | normal | 111 / 36 / **24** | 0.16 / 0.12 / **0.10** | 35.2 / 45.5 / **45.7** | 4.6× / 1.5× |
| 320 | loop | 153 / 38 / **24** | 0.23 / 0.18 / **0.14** | 35.2 / 45.5 / **45.7** | 6.4× / 1.6× |
| 320 | shimmy | 151 / 37 / **32** | 0.24 / 0.16 / **0.14** | 35.2 / 45.5 / **47.0** | 4.7× / 1.2× |

## Real handheld skyline, 3 × 2592×1728

| Size | Mode | Time, ms (Pillow / tuned / giffy) | File, MB (Pillow / tuned / giffy) | Blur dB (Pillow / tuned / giffy) | giffy speed-up vs Pillow / tuned |
|---|---|---|---|---|---|
| full | normal | 925 / 249 / **141** | 7.50 / 6.50 / **5.68** | 43.3 / 47.0 / **47.9** | 6.6× / 1.8× |
| full | loop | 1225 / 303 / **141** | 10.00 / 8.77 / **7.58** | 43.3 / 47.0 / **47.9** | 8.7× / 2.1× |
| full | shimmy | 1153 / 301 / **142** | 9.28 / 8.19 / **7.03** | 43.1 / 46.5 / **47.8** | 8.1× / 2.1× |
| 1080 | normal | 264 / 86 / **45** | 1.35 / 1.12 / **0.98** | 43.8 / 47.3 / **47.9** | 5.9× / 1.9× |
| 1080 | loop | 336 / 96 / **46** | 1.79 / 1.51 / **1.30** | 43.8 / 47.3 / **47.9** | 7.3× / 2.1× |
| 1080 | shimmy | 335 / 84 / **51** | 1.81 / 1.54 / **1.31** | 43.6 / 46.9 / **48.3** | 6.6× / 1.6× |
| 320 | normal | 75 / 20 / **11** | 0.13 / 0.11 / **0.10** | 44.5 / 47.0 / **47.4** | 6.8× / 1.8× |
| 320 | loop | 96 / 21 / **11** | 0.17 / 0.15 / **0.13** | 44.5 / 47.0 / **47.4** | 8.7× / 1.9× |
| 320 | shimmy | 98 / 22 / **17** | 0.18 / 0.15 / **0.13** | 44.6 / 46.5 / **48.6** | 5.8× / 1.3× |

## Real street set, 4 × 1728×2592 (not one moment, so shimmy leaves it unaligned)

| Size | Mode | Time, ms (Pillow / tuned / giffy) | File, MB (Pillow / tuned / giffy) | Blur dB (Pillow / tuned / giffy) | giffy speed-up vs Pillow / tuned |
|---|---|---|---|---|---|
| full | normal | 1000 / 285 / **136** | 8.24 / 5.49 / **5.03** | 42.8 / 42.9 / **45.6** | 7.4× / 2.1× |
| full | loop | 1386 / 365 / **135** | 11.10 / 6.58 / **6.07** | 42.8 / 42.9 / **45.6** | 10.3× / 2.7× |
| full | shimmy | 1394 / 373 / **146** | 11.10 / 6.58 / **6.07** | 42.8 / 42.9 / **45.6** | 9.5× / 2.6× |
| 1080 | normal | 522 / 156 / **98** | 3.60 / 2.22 / **2.04** | 42.8 / 43.2 / **45.8** | 5.3× / 1.6× |
| 1080 | loop | 688 / 190 / **97** | 4.95 / 2.66 / **2.47** | 42.8 / 43.2 / **45.8** | 7.1× / 2.0× |
| 1080 | shimmy | 697 / 196 / **108** | 4.95 / 2.66 / **2.47** | 42.8 / 43.2 / **45.8** | 6.5× / 1.8× |
| 320 | normal | 134 / 34 / **21** | 0.37 / 0.22 / **0.20** | 43.2 / 43.7 / **46.1** | 6.4× / 1.6× |
| 320 | loop | 156 / 37 / **21** | 0.52 / 0.26 / **0.24** | 43.2 / 43.7 / **46.1** | 7.4× / 1.8× |
| 320 | shimmy | 156 / 38 / **26** | 0.52 / 0.26 / **0.24** | 43.2 / 43.8 / **46.3** | 6.0× / 1.5× |

## Takeaways

- **Normal** is the cheapest mode: it writes each photo once, with no loop block, so its files are two-thirds to three-quarters the size of loop's.
- **Loop** costs giffy almost nothing over normal, because frames 2 and 3 are encoded once and written twice. Pillow re-encodes every played frame, so its loop runs take up to 47% longer than its normal ones.
- **Shimmy** adds 1–11 ms over loop in giffy. When it aligns, the PSNR rises slightly, because the encoder is then measured against the aligned frames it was given.
- **The synthetic scene at 1080 px** is the only case where tuned Pillow's file is smaller. Its heavy artificial noise suits Pillow's coarser palette, and giffy's GIF is still sharper.

Raw numbers, including plain PSNR for every case, are in `modes.json` beside this report. Rerun with `uv run python bench/modes.py docs/benchmarks/modes.json PHOTO_DIR ...`.
