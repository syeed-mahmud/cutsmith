"""Designed thumbnails.

1. Sample the clip's SOURCE (no burned captions) at 2 fps.
2. Score frames: biggest, most confident face x sharpness (Laplacian variance) —
   picks an in-focus, close, front-facing moment.
3. Crop to the target size with the face placed off-centre, punch up colour,
   and set a huge outlined headline on the empty side (top for vertical).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

import captions as C
from common import fonts_dir, run

SIZES = {"16:9": (1280, 720), "9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350)}


def _frames(src: str, meta: dict, ranges: list[list[float]], width: int = 640):
    h = int(round(width * meta["height"] / meta["width"] / 2) * 2)
    for a, b in ranges:
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", src,
               "-an", "-vf", f"fps=2,scale={width}:{h}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
        raw = subprocess.run(cmd, capture_output=True).stdout
        n = width * h * 3
        for k in range(len(raw) // n):
            yield a + k / 2.0, np.frombuffer(raw[k * n:(k + 1) * n], np.uint8).reshape(h, width, 3)


def best_frame(src: str, meta: dict, ranges: list[list[float]], cfg: dict) -> tuple[float, dict | None]:
    import cv2
    import faces as F
    w = 640
    h = int(round(w * meta["height"] / meta["width"] / 2) * 2)
    det = F._detector(w, h, cfg["face_score"])
    best = (-1.0, ranges[0][0] + 0.5, None)
    for t, img in _frames(src, meta, ranges, w):
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, found = det.detect(img)
        if found is None or len(found) == 0:
            score, face = 0.05 * cv2.Laplacian(gray, cv2.CV_64F).var() ** 0.5, None
        else:
            f = max(found, key=lambda f: f[3])
            x, y, fw, fh = (int(max(0, v)) for v in f[:4])
            roi = gray[y:y + fh, x:x + fw]
            sharp = cv2.Laplacian(roi, cv2.CV_64F).var() if roi.size else 0
            score = (fh / h) * float(f[-1]) * (sharp ** 0.5 + 1)
            face = {"cx": (x + fw / 2) / w, "cy": (y + fh / 2) / h, "h": fh / h}
        if score > best[0]:
            best = (score, t, face)
    return best[1], best[2]


def design(src: str, meta: dict, clip: dict, cfg: dict, out_jpg: Path, text: str, work: Path,
           aspect: str = "16:9", at: float | None = None) -> dict:
    W, H = SIZES.get(aspect, SIZES["16:9"])
    ranges = clip.get("sections") or [[clip["start"], clip["end"]]]
    if at is None:
        t, face = best_frame(src, meta, ranges, cfg)
    else:
        t, face = at, None
    sw, sh = meta["width"], meta["height"]
    cx = face["cx"] if face else 0.5
    # crop the source to the target aspect, face at 1/3 (horizontal) or centred (vertical)
    if sw / sh > W / H:
        cw, ch = int(sh * W / H) // 2 * 2, sh
        anchor = 0.5 if W <= H else (0.66 if cx > 0.5 else 0.34)
        x = int(np.clip(cx * sw - cw * anchor, 0, sw - cw))
        y = 0
    else:
        cw, ch = sw, int(sw * H / W) // 2 * 2
        cy = face["cy"] if face else 0.4
        x, y = 0, int(np.clip(cy * sh - ch * 0.4, 0, sh - ch))
    text_left = (W > H) and cx > 0.5            # headline on the side without the face
    lines = C._wrap(C._clean(text.upper()), 12 if W > H else 14)
    size = int(H * (0.16 if W > H else 0.075))
    if W > H:
        an, px, py = (4 if text_left else 6), (int(W * 0.05) if text_left else int(W * 0.95)), H // 2
    else:
        an, px, py = 8, W // 2, int(H * 0.08)
    body = r"\N".join(f"{{\\c&H0000E6FF&}}{ln}{{\\c&H00FFFFFF&}}" if i == len(lines) - 1 and len(lines) > 1 else ln
                      for i, ln in enumerate(lines))
    ass = work / f"thumb_{clip['id']:02d}.ass"
    ass.write_text(
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {W}\nPlayResY: {H}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: T,Anton,{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,1,0,1,"
        f"{max(4, size // 11)},{max(2, size // 22)},5,40,40,40,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        f"Dialogue: 0,0:00:00.00,0:00:10.00,T,,0,0,0,,{{\\an{an}\\pos({px},{py})}}{body}\n", encoding="utf-8")
    fonts = f":fontsdir='{fonts_dir()}'" if fonts_dir().exists() else ""
    vf = (f"crop={cw}:{ch}:{x}:{y},scale={W}:{H}:flags=lanczos,eq=saturation=1.25:contrast=1.08:brightness=0.02,"
          f"unsharp=5:5:0.6,subtitles={ass.name}{fonts}")
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", src, "-frames:v", "1",
         "-vf", vf, "-q:v", "2", str(out_jpg)], cwd=work)
    return {"thumbnail": str(out_jpg), "frame_time": round(t, 2), "face": face, "size": f"{W}x{H}"}
