"""Before/after GIFs of real shots, for checking the still point and flicker by eye.

    uv run python bench/align_check.py PHOTO_DIR ...

Each PHOTO_DIR holds one shot's photos (sorted by name). Writes
<name>-before.gif (as shot) and <name>-after.gif (aligned + evened) at 1080 px
to ~/Documents/dev/output/giffy/align/, plus report.txt.
"""
import statistics
import sys
import time
from pathlib import Path

import giffy

OUT = Path.home() / "Documents/dev/output/giffy/align"
WIDTH = 1080


def median_ms(fn, runs=3):
    ts = []
    for _ in range(runs):
        t = time.perf_counter()
        r = fn()
        ts.append(time.perf_counter() - t)
    return r, statistics.median(ts) * 1000


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    lines = []
    for d in map(Path, sys.argv[1:]):
        paths = sorted(p for p in d.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
        before, t0 = median_ms(lambda: giffy.encode_shot(paths, WIDTH, align=None, even=False))
        after, t1 = median_ms(lambda: giffy.encode_shot(paths, WIDTH))
        (OUT / f"{d.name}-before.gif").write_bytes(before.gif)
        (OUT / f"{d.name}-after.gif").write_bytes(after.gif)
        al = after.alignment
        w0, h0 = giffy._upright(paths[0], None).size
        lines += [f"{d.name}: {len(paths)} photos {w0}x{h0}",
                  f"  status {al.status}, confidence {al.confidence:.2f}, point ({al.point[0]:.3f}, {al.point[1]:.3f})",
                  "  shifts at full size, px: " + ", ".join(f"({dx * w0:+.0f}, {dy * h0:+.0f})" for dx, dy in al.shifts),
                  f"  crop {tuple(round(c, 3) for c in al.crop)}",
                  "  gains " + ", ".join("(" + ", ".join(f"{v:.3f}" for v in g) + ")" for g in al.gains),
                  f"  1080 px: {t0:.0f} ms as shot, {t1:.0f} ms aligned; {len(before.gif) / 1e6:.2f} -> {len(after.gif) / 1e6:.2f} MB"]
    (OUT / "report.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
