"""Face detection (OpenCV YuNet, CPU) + per-face mouth-motion activity.

Frames are streamed from ffmpeg at a low rate, so a 60 s clip costs a few
seconds of CPU and almost no memory.
"""
from __future__ import annotations

import os
import re
import subprocess
import urllib.request

import numpy as np

from common import die, log, models_dir, run

os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")

YUNET_FILE = "face_detection_yunet_2023mar.onnx"
YUNET_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/"
             "face_detection_yunet/face_detection_yunet_2023mar.onnx")


def model_path(download: bool = True):
    path = models_dir() / YUNET_FILE
    if not path.exists() and download:
        path.parent.mkdir(parents=True, exist_ok=True)
        log(f"[faces] downloading YuNet model -> {path}")
        tmp = path.with_suffix(".part")
        urllib.request.urlretrieve(YUNET_URL, tmp)
        tmp.rename(path)
    return path


def _detector(w: int, h: int, score: float):
    try:
        import cv2
    except ImportError:
        die("opencv-python-headless is not installed. Run setup.sh.")
    return cv2.FaceDetectorYN.create(str(model_path()), "", (w, h), score, 0.3, 50)


def scene_cuts(src: str, start: float, dur: float, threshold: float) -> list[float]:
    """Clip-relative times of hard camera cuts."""
    p = run(["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
             "-i", src, "-an", "-vf", f"scale=320:-2,select='gt(scene,{threshold})',showinfo",
             "-f", "null", "-"], check=False)
    return sorted(float(t) for t in re.findall(r"pts_time:([0-9.]+)", p.stderr)
                  if 0.25 < float(t) < dur - 0.25)


def sample(src: str, meta: dict, start: float, dur: float, cfg: dict) -> dict:
    """Return {fps, frames:[{t, faces:[{cx,cy,w,h,score,act}]}]} with coords
    normalised to 0..1 of the (rotation-corrected) source frame."""
    import cv2

    fps = cfg["detect_fps"]
    dw = cfg["detect_width"]
    dh = int(round(dw * meta["height"] / meta["width"] / 2) * 2)
    det = _detector(dw, dh, cfg["face_score"])
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
           "-i", src, "-an", "-vf", f"fps={fps},scale={dw}:{dh}", "-f", "rawvideo",
           "-pix_fmt", "bgr24", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size = dw * dh * 3
    frames, prev_gray, k = [], None, 0
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            img = np.frombuffer(buf, np.uint8).reshape(dh, dw, 3)
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            _, found = det.detect(img)
            faces = []
            for f in (found if found is not None else []):
                x, y, w, h = (float(v) for v in f[:4])
                if w < 8 or h < 8:
                    continue
                act = 0.0
                if prev_gray is not None:
                    # mouth + chin region: lower 45% of the box, inner 70% width
                    x0 = int(max(0, x + 0.15 * w)); x1 = int(min(dw, x + 0.85 * w))
                    y0 = int(max(0, y + 0.55 * h)); y1 = int(min(dh, y + 1.0 * h))
                    if x1 - x0 > 3 and y1 - y0 > 3:
                        a = gray[y0:y1, x0:x1].astype(np.int16)
                        b = prev_gray[y0:y1, x0:x1].astype(np.int16)
                        act = float(np.mean(np.abs(a - b))) / 255.0
                faces.append({"cx": (x + w / 2) / dw, "cy": (y + h / 2) / dh, "w": w / dw,
                              "h": h / dh, "score": float(f[-1]), "act": act})
            frames.append({"t": round(k / fps, 3), "faces": faces})
            prev_gray = gray
            k += 1
    finally:
        proc.stdout.close()
        proc.wait()
    return {"fps": fps, "frames": frames}


def tracks_in(frames: list[dict], fps: float, max_jump: float = 0.12) -> list[dict]:
    """Greedy nearest-centre linking of faces across frames into tracks."""
    tracks: list[dict] = []
    for i, fr in enumerate(frames):
        used = set()
        for f in sorted(fr["faces"], key=lambda f: -f["h"]):
            best, best_d = None, max_jump
            for ti, t in enumerate(tracks):
                if ti in used or i - t["idx"][-1] > fps * 1.5:
                    continue
                d = abs(t["cx"][-1] - f["cx"]) + 0.5 * abs(t["cy"][-1] - f["cy"])
                if d < best_d:
                    best, best_d = ti, d
            if best is None:
                tracks.append({"idx": [i], "cx": [f["cx"]], "cy": [f["cy"]], "h": [f["h"]],
                               "act": [f["act"]]})
                used.add(len(tracks) - 1)
            else:
                t = tracks[best]
                t["idx"].append(i); t["cx"].append(f["cx"]); t["cy"].append(f["cy"])
                t["h"].append(f["h"]); t["act"].append(f["act"])
                used.add(best)
    for t in tracks:
        t["presence"] = len(t["idx"]) / max(1, len(frames))
        t["med_cx"] = float(np.median(t["cx"]))
        t["med_cy"] = float(np.median(t["cy"]))
        t["med_h"] = float(np.median(t["h"]))
    return tracks
