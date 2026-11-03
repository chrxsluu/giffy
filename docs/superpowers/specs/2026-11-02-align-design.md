# giffy: alignment and even lighting

2026-11-02 · Christopher Luu

## Intent

A wiggle GIF looks right when one thing holds still and the scene seems to rotate around it, which is the classic wigglegram effect. Without that, everything jumps sideways by the lens spacing each frame and reads as camera shake. Today neither shimmy's old Pillow path nor giffy aligns anything: the four photos are encoded exactly as shot.

This feature makes giffy line the four photos up on one point before encoding. It also evens out their brightness and colour so the wiggle never flickers, which Christopher named as the thing that would make an aligned shimmy look wrong.

## What it does

- **Still point.** By default the centre of the frame holds still, for any subject: a person, an object or a group. Nobody aims at anything when shooting. *(Christopher)*
- **Re-align on tap.** On the result screen you can tap anything (one face in a group, a cup) and the GIF re-makes itself with that spot still. This works on every shot. *(Christopher)* The screen and touch handling are shimmy's job and are out of scope here. giffy has to accept a point and do the rest.
- **Matching by detail.** The photos are lined up by finding the same detail around the point in each photo, not by recognising faces. So any subject works.
- **Slide only.** Frames are shifted, never rotated, stretched or warped (shimmy spec).
- **Crop to the shared area, keeping the photo's shape.** After shifting, all frames are cropped to the area they have in common, so every frame is the same, slightly smaller size. *(Christopher)* The crop keeps the photos' proportions (4:3 on the rig), trimming the top and bottom as much as the sides. *(Christopher)* Concretely, it is the largest rectangle of that shape inside the shared area, centred in it *(placement is Claude's call)*. How much is lost depends on how close the subject is (estimate for the rig's 26 mm lens spacing, ~60° lens, lens 1 to lens 4): about 3% of each side's length at 2 m, 7% at 1 m, and 13% at 0.5 m.
- **Nothing to match.** If the point lands on something plain (a wall, sky, the gap between two people in a group), the nearest detailed thing holds still instead. *(Christopher)* If there is no usable detail anywhere, for example a nearly black shot, the frames stay unaligned rather than lining up on a guess.
- **Unsure matches, all or nothing.** A shot is either aligned in every frame or in none: a mix looks worse than no alignment. If one lens matches poorly while the others agree, its shift is filled in from its neighbours, because the lenses are evenly spaced in a row and the shifts should be too. If that doesn't hold together either, the whole shot stays unaligned. A repeating pattern (brick, tiles) counts as "plain": move to the nearest detail that has only one possible match. "Nearest" means within about 15% of the frame width of the point. Past that, stay unaligned rather than quietly holding something far from the tap still. *(Claude's call.)*
- **Even lighting.** Before encoding, each photo is nudged toward the four photos' average brightness and colour, measured over the shared area, so the wiggle doesn't flicker. *(Christopher: yes; the method is Claude's call.)* This catches what shimmy's exposure-sharing misses: small differences between cheap lenses, and the flash lighting the scene a little differently from each spot.

## Goals

1. The still point stays still: across the frames of a finished GIF, the chosen detail moves by at most 1 px at the GIF's size.
2. No visible flicker. Evening corrects only differences between lenses, never differences in content. After alignment, the frames still show slightly different background (up to ~13% of the width at 0.5 m), so the correction must be measured in a way that ignores that, such as matched pixels with outliers trimmed. The size of the correction is capped. The test: on shots without depth (the synthetic lighting tests), each frame's average per colour channel ends within 1% of the others. On real shots, Christopher sees no flicker in the Fuji before/after GIFs. *(Claude's call: the 1% holds only where the scene allows it, so that it never conflicts with the anti-goal on changing the look.)*
3. Re-aligning feels instant. Alignment and evening together add under 20 ms per shot on the M2 Pro at any size, so a 320 px re-align still comes back well under a second on the Pi.
4. The 320 px preview and the full GIF of the same shot hold the same point still.
5. Everything giffy has today stays true: deterministic output, the same bytes for any thread count, full-size or resized output, every test passing.

## Non-goals

- Picking a subject automatically (faces, saliency). The default is simply the centre. *(Christopher)*
- Rotation, scale or perspective correction. Lens mounting slop gets the small vertical search below, nothing more.
- Filling the cropped edges with invented pixels.
- Fixing motion between lenses (someone moving between captures). Sync is shimmy's problem.
- The tap UI itself, and storing re-align choices in the gallery (shimmy).

## Anti-goals (fails even if it "works")

- Brightness or colour visibly shifting between frames: the flicker Christopher named.
- Locking onto the wrong spot with confidence: on a repeating pattern (brick, tiles, a striped shirt) a wrong match is worse than no alignment. Low-confidence matches must fall back, not guess.
- Evening that changes the look: it should only remove differences between lenses, not brighten, darken or recolour the shot as a whole.
- Making the preview noticeably slower.

## Constraints

- Lives in giffy (`~/Documents/dev/code/giffy`), on branch `feat/align`, with the same structure: Python plus the one C file, Pillow for decoding. No new dependencies (no numpy, no OpenCV): giffy is installed on the Pi from source. *(Claude's call, following giffy's existing approach.)*
- Lenses sit in one row, so expect mostly sideways shifts. At full size (2592 px wide), one lens step is about 30 px for a subject at 2 m, 60 px at 1 m and 120 px at 0.5 m. Allow a small up-down search too (a few % of the height) for mounting slop and handheld bursts.
- Shot sizes range from 320 px previews to full-size originals (up to 2592×1944 on the rig; the Fuji test bursts are 2592×1728 and 1728×2592).
- Exposure differences between lenses are expected, so matching must compare patterns of detail, not raw brightness.

## Interface

- A point to hold still, given as fractions of the frame (`0.5, 0.5` = centre), plus which frame the point refers to. A tap lands on whichever frame was on screen at that moment, and the tapped spot is in a different place in each frame. The default refers to a middle lens.
- Alignment and evening are on by default in giffy, since it exists to make wiggle GIFs. A caller can turn them off, and turning them off must give exactly today's output. *(Claude's call.)*
- The caller must be able to learn what happened: the shift used for each frame, the final crop, whether it fell back (nearest detail, or none), and a match confidence. shimmy needs this to map a tap on the shown GIF back to the photos, and to log bad matches. *(Claude's call.)*
- The caller can hand that result back in, so that a later call skips matching and reuses it, scaled to the new size. shimmy makes the 320 px preview first and passes its result to the full GIF, so both hold the same point still (goal 4) and the full GIF does no matching. Matching and evening are worked out on a copy of fixed size, so a fresh call gives the same answer at any output width. *(Claude's call.)*
- `width` is the finished GIF's width, after cropping: `width=1080` gives exactly 1080 px. The crop is worked out on the decoded photos, then the cropped area is resized, with one decode per photo as today. *(Claude's call: shimmy's 1080 px and 640 px floor keep meaning what they say.)*
- `make_gif` stays a drop-in for shimmy's `wiggle.make_gif(paths, out, width, delay_ms)`.

## Tests and checks

- Synthetic shots with known answers. Render a scene with a foreground object at one shift and a background at another, and check the recovered shift is within 1 px when the point is on each. Repeat with known per-lens brightness and colour changes, which must be both ignored by the matching and removed by the evening.
- Plain centre: a blank middle with detail nearby must lock onto the nearby detail. An all-flat or near-black shot must fall back to no shift and say so.
- Repeating pattern: must report low confidence and fall back, not lock onto a wrong copy.
- Determinism and thread counts, as today. Three decoders still agree on every stream.
- Real photos: run the four Fuji bursts (handheld, so they have real shifts) and write before/after GIFs to `~/Documents/dev/output/giffy/` for Christopher to look at. Later, real rig shots once the parts arrive.
- Timing in `bench/stages.py`, checked against goal 3.

## Left to the builder

- How to match. Normalised cross-correlation on a luma patch, coarse-to-fine, is the expected approach, as is how big the patch and search window are.
- How "detail" and "confidence" are measured, and their thresholds, tuned on the test shots.
- Whether the outer lenses match against the middle reference directly or by chaining neighbours.
- How evening is done (per-channel gain, gain plus offset, or curve matching), as long as it meets goal 2 and the anti-goal on changing the look. Watch for clipped highlights.
- Where the work runs (Python with Pillow, or the C core) and the exact names of new parameters.
