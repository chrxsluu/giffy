"""giffy vs tuned Pillow (shimmy's current winner) on 4-lens shots.

    uv run python bench/bench.py [PHOTO_DIR ...]

Each PHOTO_DIR holds one shot's lens photos (sorted by name). Without any, a
synthetic 4x 2592x1944 parallax scene is made in bench/out/. Reports median time,
file size, PSNR and blurred PSNR (sigma 1: roughly what the eye sees through
dither noise) against the photos at the GIF's size.
"""
import math
import random
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps, ImageStat

import giffy

OUT = Path(__file__).parent / "out"
RUNS = 5
WIDTHS = [None, 1080, 320]  # None = full size


def synthetic():
    paths = [OUT / f"lens{i + 1}.jpg" for i in range(4)]
    if all(p.exists() for p in paths):
        return paths
    OUT.mkdir(exist_ok=True)
    W, H, SHIFT = 2592, 1944, 24  # OV5640 full size, lenses ~24 px apart
    rnd = random.Random(7)
    big = Image.new("RGB", (W + SHIFT * 4, H))
    d = ImageDraw.Draw(big)
    for y in range(H):
        t = y / H
        c = (int(120 + 100 * t), int(170 - 40 * t), int(230 - 160 * t)) if t < 0.55 else \
            (int(60 + 40 * t), int(110 - 20 * t), int(40 + 10 * t))
        d.line([(0, y), (big.width, y)], fill=c)
    for _ in range(400):
        x, y, r = rnd.randrange(big.width), rnd.randrange(H // 3, H), rnd.randrange(15, 220)
        d.ellipse([x - r, y - r * 2, x + r, y + r], fill=tuple(rnd.randrange(256) for _ in range(3)))
    big = big.filter(ImageFilter.GaussianBlur(3))
    for i, p in enumerate(paths):
        f = big.crop((i * SHIFT, 0, i * SHIFT + W, H))
        f = ImageChops.add(f, Image.effect_noise((W, H), 18).convert("RGB"), scale=1.0, offset=-128)
        f.save(p, quality=88)
    return paths


def tuned_pillow(paths, out, width=None, threads=4):
    """shimmy's tuned Pillow: draft decode, one shared FASTOCTREE palette, Floyd-Steinberg, 4 threads."""
    def load(p):
        im = Image.open(p)
        if width:
            h = round(im.height * width / im.width)
            im.draft("RGB", (width, h))
            return im.convert("RGB").resize((width, h), Image.Resampling.LANCZOS)
        return im.convert("RGB")

    with ThreadPoolExecutor(threads) as ex:
        frames = list(ex.map(load, paths))
        w, h = frames[0].size
        mos = Image.new("RGB", (256 * len(frames), round(h * 256 / w)))
        for i, f in enumerate(frames):
            mos.paste(f.resize((256, mos.height), Image.Resampling.BILINEAR), (i * 256, 0))
        pal = mos.quantize(256, method=Image.Quantize.FASTOCTREE)
        q = list(ex.map(lambda f: f.quantize(palette=pal, dither=Image.Dither.FLOYDSTEINBERG), frames))
    seq = [q[i] for i in giffy.wiggle_order(len(q))]
    seq[0].save(out, save_all=True, append_images=seq[1:], duration=100, loop=0)


def mse(a, b):
    return sum(ImageStat.Stat(ImageChops.difference(a, b)).sum2) / (a.width * a.height * 3)


def quality(gif, refs):
    """(PSNR, blurred PSNR) averaged over the unique frames."""
    p, pb = [], []
    with Image.open(gif) as im:
        for i, ref in enumerate(refs):
            im.seek(i)
            f = im.convert("RGB")
            p.append(mse(f, ref))
            pb.append(mse(f.filter(ImageFilter.GaussianBlur(1)), ref.filter(ImageFilter.GaussianBlur(1))))
    db = lambda m: 10 * math.log10(255 ** 2 / statistics.mean(m))
    return db(p), db(pb)


def timed(fn):
    ts = []
    for _ in range(RUNS):
        t = time.perf_counter()
        fn()
        ts.append(time.perf_counter() - t)
    return statistics.median(ts)


def run(name, paths):
    full = [ImageOps.exif_transpose(Image.open(p)).convert("RGB") for p in paths]
    print(f"\n{name}: {len(paths)} x {full[0].width}x{full[0].height}")
    print(f"{'size':>6} {'encoder':13} {'ms':>6} {'MB':>6} {'PSNR':>6} {'blur':>6}")
    for width in WIDTHS:
        refs = full if not width else [f.resize((width, round(f.height * width / f.width)), Image.Resampling.LANCZOS) for f in full]
        label = "full" if not width else str(width)
        rows = {}
        # quality is measured unaligned (same frames as Pillow); "aligned" adds line-up, crop and evening
        for enc, fn in [("tuned Pillow", lambda o: tuned_pillow(paths, o, width)),
                        ("giffy", lambda o: giffy.make_gif(paths, o, width, align=None, even=False)),
                        ("giffy aligned", lambda o: giffy.make_gif(paths, o, width))]:
            out = OUT / f"{name}-{label}-{enc.replace(' ', '-').lower()}.gif"
            t = timed(lambda: fn(out))
            rows[enc] = t
            q, qb = quality(out, refs) if enc != "giffy aligned" else (float("nan"),) * 2
            print(f"{label:>6} {enc:13} {t * 1000:6.0f} {out.stat().st_size / 1e6:6.2f} {q:6.2f} {qb:6.2f}")
        print(f"{'':>6} {'speed-up':13} {rows['tuned Pillow'] / rows['giffy']:5.1f}x unaligned, "
              f"{rows['tuned Pillow'] / rows['giffy aligned']:.1f}x aligned")


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    run("synthetic", synthetic())
    for d in sys.argv[1:]:
        run(Path(d).name, sorted(p for p in Path(d).iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png")))
