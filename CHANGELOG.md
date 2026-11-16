# Changelog

giffy uses MAJOR.MINOR.PATCH version numbers. Each release is a git tag `vX.Y.Z` on `main`, cut with `tools/release.sh`.

- **MAJOR:** a change that can break callers, such as a renamed or removed argument or a different default.
- **MINOR:** something new, or a change to the GIFs giffy makes from the same photos and options. Existing calls keep working.
- **PATCH:** a fix, a speed-up or a docs change that leaves the GIFs byte-for-byte the same.

While the version starts with 0, a MINOR bump may also break callers. The notes say so when it does.

## v0.1.1 (2026-11-15)

- The repo is public, so installing needs no GitHub login.
- Versions and this changelog: every release is a `vX.Y.Z` tag, so a `pip install` of a project that pins giffy picks up the new release.

## v0.1.0 (2026-11-04)

First version, as used by shimmy.

- Encoder: shared Wu palette, serpentine dither with lossy LZW, frames that repeat are encoded once, deterministic for any thread count.
- Three modes: `normal`, `loop` and `shimmy` (the default: loop, lined up on the centre, lighting evened between lenses).
- `encode_shot` returns the alignment, so the full-size GIF can reuse the preview's.
- Photos over 50 MP are rejected from their header, before decoding.
