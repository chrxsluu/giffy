"""Where the time goes, per stage (median ms). Usage: python bench/stages.py [PHOTO_DIR]"""
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import bench  # noqa: E402
import giffy  # noqa: E402
from giffy import _giffy, _load  # noqa: E402

paths = sorted(Path(sys.argv[1]).glob("*.jpg")) if len(sys.argv) > 1 else bench.synthetic()
for width in bench.WIDTHS:
    ts = {k: [] for k in ["load", "palette", "frames (parallel)", "one frame cold", "one frame warm",
                          "align + even"]}
    for _ in range(5):
        with ThreadPoolExecutor(4) as ex:
            t = time.perf_counter()
            L = list(ex.map(lambda p: _load(p, width), paths))
            ts["load"].append(time.perf_counter() - t)
            (w, h), fr = L[0][0], [b for _, b in L]
            t = time.perf_counter()
            pal, _ = _giffy.palette(fr, w, h, 256)
            ts["palette"].append(time.perf_counter() - t)
            lut = bytearray(b"\xff\xff") * _giffy.LUT_SIZE
            t = time.perf_counter()
            list(ex.map(lambda b: _giffy.encode(b, w, h, pal, lut, 8, giffy.LOSSY, giffy.STRENGTH), fr))
            ts["frames (parallel)"].append(time.perf_counter() - t)
            lut = bytearray(b"\xff\xff") * _giffy.LUT_SIZE
            for k in ("one frame cold", "one frame warm"):
                t = time.perf_counter()
                _giffy.encode(fr[0], w, h, pal, lut, 8, giffy.LOSSY, giffy.STRENGTH)
                ts[k].append(time.perf_counter() - t)
        t = time.perf_counter()
        giffy.encode(paths, width, align=None, even=False)
        t_off = time.perf_counter() - t
        t = time.perf_counter()
        giffy.encode(paths, width)
        ts["align + even"].append(time.perf_counter() - t - t_off)   # what aligning adds to a whole encode
    print(f"{w}x{h}:", {k: round(statistics.median(v) * 1000, 1) for k, v in ts.items()})
