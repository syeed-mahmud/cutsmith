# Framing / layouts

`render` decides a layout per clip (`--layout auto`). Override for all clips with
`--layout X`, or per clip with `"layout": "X"` in picks.json.

## How auto decides

1. Sample the clip at 6 fps; detect faces (OpenCV YuNet, CPU). For each face, measure
   **mouth-region motion** frame-to-frame — that's the "who is talking" signal.
2. Split the clip into camera **shots** (ffmpeg scene detection), so multi-camera
   podcasts are handled shot by shot.
3. Per shot:
   - one person → a 9:16 window centred on them, **held still**; it only re-centres if
     the face drifts > 12 % of the window for 1.5 s, and then with a smooth 0.6 s pan;
   - two people close together → one window on their midpoint;
   - two people far apart → hard-cut between them following mouth activity (min 1.2 s
     per speaker), or `split` if `--two-face split`;
   - no clear face → keep the previous position; small faces in wide shots are still followed.
4. Faces visible < 40 % of the clip, or the clip classifies as a screen recording
   (few/small/off-centre faces, e.g. a webcam in a corner) and
   no shot has a large face → `screen`: a window of 55 % of the width follows the mouse
   cursor (frame-differencing cursor detector). No cursor movement → `fit`.
   Source already vertical/square → `fit`.

## Layouts

| Layout | What it does | Use when |
|---|---|---|
| `crop` | 9:16 window that follows the subject (static per shot, speaker switching) | talking heads, interviews, podcasts (default) |
| `split` | two cells stacked (1080×960 each), one face per cell, captions on the seam. Cells are fixed for the whole clip | one static wide two-shot where both people react a lot. Not for multi-camera clips — close-up shots would show the wrong region; use `crop` there |
| `fit` | whole frame scaled to width over a blurred, darkened copy of itself | screen shares, slides, wide group shots, gameplay, already-vertical sources |
| `screen` | 55 %-wide window following the mouse cursor, over a blurred background | tutorials, demos, slides, coding streams (`--set screen_zoom=0.7` for more context, `cursor_track=false` → plain fit) |
| `center` | static centre crop | auto framing fails and the subject is centred |
| `stack` | speaker on top, `--broll` video (muted, looped) on the bottom | "split gameplay"/brainrot format, retention for calm talkers |

Plans are cached in `clips.json`; changing `start/end`, `layout` or `--broll`
recomputes them. Bump `PLAN_VERSION` in `framing.py` after changing the algorithm.

## Tunables (`--set key=value`)

`detect_fps`, `min_face_height` (0.07 = 7 % of frame height), `min_presence`,
`scene_threshold` (0.32; lower = more cuts detected), `deadband` (0.12),
`drift_hold` (1.5 s), `drift_ease` (0.6 s), `two_face_mode` (`speaker|split|wide`),
`speaker_margin` (1.25 = the other face must be 25 % more active to switch),
`speaker_min_dwell` (1.2 s), `face_headroom` (split cells: face centre height),
`screen_zoom` (0.55), `cursor_track`, `cursor_interval` (0.5 s),
`strip_source_captions` + `caption_band_frac` (0.74: keep the top 74 % of the frame to hide
captions burned into the source).
