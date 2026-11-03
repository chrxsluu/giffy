"""giffy's three modes vs Pillow doing the same job.

    uv run python bench/modes.py OUT_JSON PHOTO_DIR ...

Each PHOTO_DIR holds one shot's photos (sorted by name). Prints one JSON row per
case and writes them all to OUT_JSON; GIFs go to bench/out/modes/.
"""
import json, math, statistics, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat
import giffy
from giffy import align as A, even as E

RUNS, WIDTHS = 3, [None, 1080, 320]
TMP = Path(__file__).parent / "out" / "modes"; TMP.mkdir(parents=True, exist_ok=True)


def order_for(mode, n):
    return list(range(n)) if mode == "normal" else giffy.wiggle_order(n)


def load(paths, width):  # what a Pillow user does: open, upright, resize (draft decode like tuned Pillow)
    def one(p):
        im = Image.open(p)
        if width:
            im.draft("RGB", (width * 2, width * 2))
        im = ImageOps.exif_transpose(im).convert("RGB")
        return im.resize((width, round(im.height * width / im.width)), Image.Resampling.LANCZOS) if width and width != im.width else im
    with ThreadPoolExecutor(4) as ex:
        return list(ex.map(one, paths))


def aligned(paths, width):  # shimmy mode's frames, made with giffy's alignment code (Pillow has none)
    with ThreadPoolExecutor(4) as ex:
        fr = list(ex.map(lambda p: giffy._upright(p, width), paths))
        sm = list(ex.map(A.small, fr))
        al = A.find(fr, A.CENTER, sm)
        boxes = A.crop_boxes(fr[0].size, A.refine(fr, al))
        fr = list(ex.map(lambda fb: fb[0].crop(fb[1]), zip(fr, boxes)))
        if al.status != "none":
            sw, sh = sm[0].size
            sb = A.crop_boxes((sw, sh), [(round(dx * sw), round(dy * sh)) for dx, dy in al.shifts])
            g = E.gains([s.crop(b) for s, b in zip(sm, sb)])
            fr = list(ex.map(lambda fg: E.apply(*fg), zip(fr, g)))
        if width and width != fr[0].width:
            size = (width, max(1, round(fr[0].height * width / fr[0].width)))
            fr = list(ex.map(lambda f: f.resize(size, Image.Resampling.LANCZOS), fr))
    return fr


def save(frames, out, mode):
    seq = [frames[i] for i in order_for(mode, len(frames))]
    kw = {} if mode == "normal" else {"loop": 0}   # no loop key = plays once
    seq[0].save(out, save_all=True, append_images=seq[1:], duration=100, **kw)


def pillow_default(frames, out, mode):
    save(frames, out, mode)


def pillow_tuned(frames, out, mode):
    w, h = frames[0].size
    mos = Image.new("RGB", (256 * len(frames), max(1, round(h * 256 / w))))
    for i, f in enumerate(frames):
        mos.paste(f.resize((256, mos.height), Image.Resampling.BILINEAR), (i * 256, 0))
    pal = mos.quantize(256, method=Image.Quantize.FASTOCTREE)
    with ThreadPoolExecutor(4) as ex:
        q = list(ex.map(lambda f: f.quantize(palette=pal, dither=Image.Dither.FLOYDSTEINBERG), frames))
    save(q, out, mode)


def mse(a, b):
    return sum(ImageStat.Stat(ImageChops.difference(a, b)).sum2) / (a.width * a.height * 3)


def quality(gif, refs):
    p, pb = [], []
    with Image.open(gif) as im:
        for i, ref in enumerate(refs):
            im.seek(i)
            f = im.convert("RGB")
            p.append(mse(f, ref)); pb.append(mse(f.filter(ImageFilter.GaussianBlur(1)), ref.filter(ImageFilter.GaussianBlur(1))))
    db = lambda m: 10 * math.log10(255 ** 2 / max(statistics.mean(m), 1e-9))
    return round(db(p), 2), round(db(pb), 2)


def timed(fn):
    ts = []
    for _ in range(RUNS):
        t = time.perf_counter(); fn(); ts.append(time.perf_counter() - t)
    return statistics.median(ts) * 1000


rows = []
for d in map(Path, sys.argv[2:]):
    paths = sorted(p for p in d.iterdir() if p.suffix.lower() in (".jpg", ".jpeg"))
    for width in WIDTHS:
        for mode in ("normal", "loop", "shimmy"):
            prep = (lambda: aligned(paths, width)) if mode == "shimmy" else (lambda: load(paths, width))
            refs = prep()
            label = "full" if not width else str(width)
            row = {"shot": d.name, "n": len(paths), "size": label, "mode": mode, "gif": f"{refs[0].width}x{refs[0].height}"}
            for enc, fn in [("pillow default", lambda o: pillow_default(prep(), o, mode)),
                            ("tuned pillow", lambda o: pillow_tuned(prep(), o, mode)),
                            ("giffy", lambda o: giffy.make_gif(paths, o, width, mode=mode))]:
                out = TMP / f"{d.name}-{label}-{mode}-{enc.replace(' ', '-')}.gif"
                t = timed(lambda: fn(out))
                q, qb = quality(out, refs)
                with Image.open(out) as g:
                    loops = g.info.get("loop")
                row[enc] = {"ms": round(t), "mb": round(out.stat().st_size / 1e6, 2), "psnr": q, "blur": qb, "loop": loops}
            rows.append(row)
            print(json.dumps(row), flush=True)
Path(sys.argv[1]).write_text(json.dumps(rows, indent=1))
