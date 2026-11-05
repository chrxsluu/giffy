# Hardening + LZW speed-up (from the 2026-11-04 Fable review)

Branch: `feat/hardening`. Review: `~/Documents/dev/output/giffy/2026-11-04-fable-review.md`.

**Goal:** close the review's security findings and land its byte-identical LZW speed-up. Output for valid inputs must not change by a single byte.

1. **Pixel limit (Medium).** `MAX_PIXELS = 50_000_000` (module constant; covers 48 MP phone photos, lenses are 5 MP). `_load` and `_upright` check the header size right after `Image.open`, before `draft` or decode, and raise `ValueError` above the limit or for an empty (0-pixel) image. Test: a small JPEG with a forged 13000×13000 header raises in both the plain and the aligned path.
2. **Early argument checks.** `width` must be 1..65535, and `lossy` must be a finite number ≥ 0. Both are checked before any decode. Tests: `width=65536`, `lossy=nan`, `lossy="4"`.
3. **LZW child-list walk + LUT guard (Fable's patch, without its STATS instrumentation).** The lossy path walks `w`'s child list once instead of hash probing. `lut_get` recomputes any slot ≥ the palette size. Test: `_giffy.encode` with a stale LUT gives the same bytes as a fresh one.
4. **Clamp match search window.** `rx` ≤ w and `ry` ≤ h in `py_match`. Test: a huge window on a small frame returns quickly.
5. **Verify.**
   - Full suite, including the golden hashes.
   - The suite under ASan+UBSan.
   - Byte-identical GIFs vs main on the three photo sets (full, 1080, 320; 1 and 4 threads).
   - Timing vs main.

Skipped from the review: the shared preview pipeline. It would make the preview appear later (it would wait for the half-size decode instead of its own ⅛-size one), and it saves only ~0.05% of the battery per hour. BICUBIC resize is skipped because it changes the output. The Pi-only ideas wait for measurements on the Pi.
