"""Screen-recording reframing: follow the mouse cursor with a zoomed window."""
from __future__ import annotations

import subprocess

import numpy as np


def detect_cursor_positions(src: str, start: float, dur: float, src_w: int, src_h: int,
                            sample_interval: float = 0.5, work_w: int = 960) -> list[dict]:
    """Cursor positions via frame differencing: small changed regions between
    consecutive frames are cursor-sized movement. Returns [{t, x_norm}]."""
    import cv2

    if dur < sample_interval * 2:
        return []
    wh = int(round(work_w * src_h / src_w / 2) * 2)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
           "-i", src, "-an", "-vf", f"fps={1.0 / sample_interval},scale={work_w}:{wh},format=gray",
           "-f", "rawvideo", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    size = work_w * wh
    frames = [(i * sample_interval, np.frombuffer(raw[i * size:(i + 1) * size], np.uint8).reshape(wh, work_w))
              for i in range(len(raw) // size)]
    if len(frames) < 2:
        return []
    # area thresholds assume full-resolution frames; scale them to the working size
    scale = (work_w / src_w) ** 2
    lo_area, hi_area = 50 * scale, 5000 * scale

    positions, last_x = [], 0.5
    for i in range(1, len(frames)):
        ts, curr = frames[i]
        _, prev = frames[i - 1]
        diff = cv2.absdiff(prev, curr)
        _, thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cands = []
        for c in contours:
            area = cv2.contourArea(c)
            x, y, w, h = cv2.boundingRect(c)
            if lo_area < area < hi_area and 0.2 < (w / max(h, 1)) < 5.0:
                cx = (x + w / 2) / work_w
                cands.append({"x_norm": cx, "dist": abs(cx - last_x)})
        if cands:
            last_x = min(cands, key=lambda c: c["dist"])["x_norm"]
        positions.append({"t": round(ts, 2), "x_norm": round(last_x, 4), "moved": bool(cands)})
    positions.insert(0, {"t": 0.0, "x_norm": positions[0]["x_norm"], "moved": False})
    return positions


def smooth_positions(positions: list[dict], window: int = 5) -> list[dict]:
    if len(positions) < window:
        return positions
    x = np.array([p["x_norm"] for p in positions])
    sm = np.convolve(x, np.ones(window) / window, mode="same")
    half = window // 2
    sm[:half], sm[-half:] = x[:half], x[-half:]
    return [{**p, "x_norm": round(float(sm[i]), 4)} for i, p in enumerate(positions)]


def crop_keyframes(src_w: int, positions: list[dict], zoom: float) -> tuple[int, list[list[float]]]:
    """(crop_w, [[t, x], ...]) — keyframes dropped when x moves < 2% of crop width."""
    crop_w = min(src_w, int(src_w * zoom)) // 2 * 2
    kf = [[p["t"], int(max(0, min(p["x_norm"] * src_w - crop_w / 2, src_w - crop_w)))] for p in positions]
    if len(kf) > 2:
        out = [kf[0]]
        for k in kf[1:-1]:
            if abs(k[1] - out[-1][1]) > crop_w * 0.02:
                out.append(k)
        out.append(kf[-1])
        kf = out
    return crop_w, kf


def classify(frames: list[dict]) -> str:
    """talking-head | podcast | screen, from per-frame face lists (detect_content.classify)."""
    if not frames:
        return "screen"
    counts = [len(f["faces"]) for f in frames]
    sizes = [fc["w"] * fc["h"] * 100 for f in frames for fc in f["faces"]]
    xs = [fc["cx"] for f in frames for fc in f["faces"]]
    avg_count = float(np.mean(counts))
    avg_size = float(np.mean(sizes)) if sizes else 0.0
    center_bias = 1.0 - float(np.mean([abs(x - 0.5) for x in xs])) * 2 if xs else 0.0
    face_ratio = sum(1 for c in counts if c) / len(counts)
    if avg_count < 0.5 or (avg_size < 5 and face_ratio < 0.5):
        return "screen"
    if avg_count >= 1.8 and face_ratio > 0.7:
        return "podcast"
    if avg_size < 8 and center_bias < 0.4:      # webcam overlay in a corner / presenter in slides
        return "screen"
    if avg_count >= 0.5 and avg_size >= 5:
        return "talking-head"
    return "screen"
