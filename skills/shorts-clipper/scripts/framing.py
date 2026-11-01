"""Decide how each clip is framed in 9:16 and build the ffmpeg filter for it.

Layouts
  crop    a 9:16 window that follows the subject. Static per camera shot,
          re-centres only on real drift, hard-cuts between speakers in a
          two-person shot (who is talking = mouth-motion activity).
  split   two people far apart -> stacked top/bottom cells, one face each.
  fit     no usable face (screen recording, slides, wide shot) or an already
          vertical source -> whole frame over a blurred copy of itself.
  stack   main video on top, user-supplied b-roll (e.g. gameplay) below.
  screen  screen recording / slides (incl. webcam-in-corner) -> zoomed window that
          follows the mouse cursor over a blurred background.
  audio   audio-only source -> dark canvas with a waveform.
"""
from __future__ import annotations

import numpy as np

import faces as F
from lib import cursor as CUR

PLAN_VERSION = 6   # bump when plan() output changes so cached plans are recomputed
LAYOUTS = ("auto", "crop", "split", "fit", "screen", "stack", "center", "audio", "native")


def _even(x: float) -> int:
    v = int(round(x))
    return v - (v % 2)


def crop_width(meta: dict, cfg: dict) -> int:
    return _even(meta["height"] * cfg["out_w"] / cfg["out_h"])


def _shots(cuts: list[float], dur: float, min_shot: float) -> list[tuple[float, float]]:
    bounds = [0.0] + [c for c in cuts if 0 < c < dur] + [dur]
    shots: list[list[float]] = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        if shots and b - a < min_shot:
            shots[-1][1] = b
        else:
            shots.append([a, b])
    return [tuple(s) for s in shots]


def _dominant(tracks: list[dict], cfg: dict) -> list[dict]:
    dom = [t for t in tracks if t["presence"] >= cfg["min_presence"] and t["med_h"] >= cfg["min_face_height"]]
    return sorted(dom, key=lambda t: -(t["med_h"] * t["presence"]))


def _series(track: dict, n: int, key: str, fill: float | None = None) -> np.ndarray:
    arr = np.full(n, np.nan)
    for i, v in zip(track["idx"], track[key]):
        arr[i] = v
    good = ~np.isnan(arr)
    if fill is not None:
        arr[~good] = fill
    elif good.any():
        arr = np.interp(np.arange(n), np.flatnonzero(good), arr[good])
    return arr


def _smooth(x: np.ndarray, win: int) -> np.ndarray:
    if win <= 1 or len(x) < 2:
        return x
    k = np.ones(win) / win
    return np.convolve(np.pad(x, (win // 2, win - 1 - win // 2), mode="edge"), k, mode="valid")


def _follow_single(cx: np.ndarray, t0: float, fps: float, cfg: dict, cw_frac: float) -> list[list[float]]:
    """Static x per stretch; re-centre only after sustained drift."""
    n = len(cx)
    hold = max(1, int(cfg["drift_hold"] * fps))
    band = cfg["deadband"] * cw_frac
    segs, cur_start, ease = [], 0, 0.0          # first stretch of a shot starts with a hard cut
    cur = float(np.median(cx[: max(1, int(fps))]))
    drift = 0
    for i in range(n):
        drift = drift + 1 if abs(cx[i] - cur) > band else 0
        if drift >= hold:
            j = i - hold + 1
            segs.append([t0 + cur_start / fps, t0 + j / fps, cur, ease])
            cur_start, ease = j, cfg["drift_ease"]
            cur = float(np.median(cx[j: min(n, j + int(fps))]))
            drift = 0
    segs.append([t0 + cur_start / fps, None, cur, ease])
    return segs


def _speaker_switch(a: dict, b: dict, n: int, t0: float, fps: float, cfg: dict) -> list[list[float]]:
    """Hard-cut between two far-apart faces following mouth activity."""
    act_a = _smooth(_series(a, n, "act", fill=0.0), max(1, int(0.8 * fps)))
    act_b = _smooth(_series(b, n, "act", fill=0.0), max(1, int(0.8 * fps)))
    xa, xb = a["med_cx"], b["med_cx"]
    first = slice(0, max(1, int(fps * 1.5)))
    cur = 0 if act_a[first].mean() >= act_b[first].mean() else 1
    dwell = int(cfg["speaker_min_dwell"] * fps)
    margin = cfg["speaker_margin"]
    segs, start, last_switch = [], 0, 0
    for i in range(n):
        mine, other = (act_a[i], act_b[i]) if cur == 0 else (act_b[i], act_a[i])
        if other > mine * margin + 1e-4 and i - last_switch >= dwell:
            segs.append([t0 + start / fps, t0 + i / fps, xa if cur == 0 else xb, 0.0])
            cur, start, last_switch = 1 - cur, i, i
    segs.append([t0 + start / fps, None, xa if cur == 0 else xb, 0.0])
    return segs


def plan(src: str, meta: dict, start: float, end: float, cfg: dict,
         force: str | None = None, broll: str | None = None) -> dict:
    """Return a framing plan (times are clip-relative)."""
    result = _plan(src, meta, start, end, cfg, force, broll)
    result["v"] = PLAN_VERSION
    return result


def _plan(src: str, meta: dict, start: float, end: float, cfg: dict,
          force: str | None, broll: str | None) -> dict:
    dur = end - start
    if not meta.get("has_video"):
        return {"layout": "audio"}
    W, H = meta["width"], meta["height"]
    cw = crop_width(meta, cfg)
    if force in ("fit", "center", "audio"):
        return {"layout": force, "cw": cw}
    if W / H < 0.9 or cw >= W:
        return {"layout": "fit", "reason": "source already vertical/narrow"}

    data = F.sample(src, meta, start, dur, cfg)
    frames, fps = data["frames"], data["fps"]
    if not frames:
        return {"layout": "center", "cw": cw, "reason": "no frames decoded"}
    cuts = F.scene_cuts(src, start, dur, cfg["scene_threshold"])
    shots = _shots(cuts, dur, cfg["min_shot"])
    cw_frac = cw / W

    shot_info, face_time, two_far_time, big_face_time = [], 0.0, 0.0, 0.0
    for a, b in shots:
        i0, i1 = int(a * fps), max(int(a * fps) + 1, int(b * fps))
        sub = frames[i0:i1]
        tracks = F.tracks_in(sub, fps)
        dom = _dominant(tracks, cfg)
        if dom:
            big_face_time += b - a
        if not dom:   # wide shot: faces too small to be "dominant" — still follow the biggest one
            dom = sorted([t for t in tracks if t["presence"] >= cfg["min_presence"]],
                         key=lambda t: -(t["med_h"] * t["presence"]))[:1]
        far = len(dom) >= 2 and abs(dom[0]["med_cx"] - dom[1]["med_cx"]) > 0.8 * cw_frac
        shot_info.append({"a": a, "b": b, "i0": i0, "n": len(sub), "dom": dom[:2], "far": far})
        if dom:
            face_time += b - a
        if far:
            two_far_time += b - a

    content = CUR.classify(frames)
    # classifier says "screen" (e.g. webcam in a corner) only counts if no shot has a big face
    screenish = face_time < 0.4 * dur or (content == "screen" and big_face_time < 0.4 * dur)
    if force == "screen" or (not force and not broll and screenish):
        return _screen_plan(src, meta, start, dur, cfg, content, face_time / dur)

    portrait = cfg["out_h"] > cfg["out_w"]
    if (force == "stack" or broll) and portrait:
        layout = "stack"
    elif force == "crop" or (force == "split" and portrait):
        layout = force
    elif portrait and cfg["two_face_mode"] == "split" and two_far_time >= 0.5 * dur:
        layout = "split"
    else:
        layout = "crop"

    if layout == "split":
        best = max((s for s in shot_info if s["far"]), key=lambda s: s["b"] - s["a"], default=None)
        if best is None:
            layout = "crop"
        else:
            pair = sorted(best["dom"][:2], key=lambda t: t["med_cx"])
            return _split_plan(pair, meta, cfg)

    segs: list[list[float]] = []
    last_x = 0.5
    for s in shot_info:
        dom, n = s["dom"], s["n"]
        if not dom:
            segs.append([s["a"], s["b"], last_x, 0.0])
            continue
        if len(dom) >= 2 and s["far"] and cfg["two_face_mode"] != "wide":
            part = _speaker_switch(dom[0], dom[1], n, s["a"], fps, cfg)
        elif len(dom) >= 2 and not s["far"]:
            part = [[s["a"], None, (dom[0]["med_cx"] + dom[1]["med_cx"]) / 2, 0.0]]
        elif len(dom) >= 2:                                   # far apart but mode=wide
            part = [[s["a"], None, (dom[0]["med_cx"] + dom[1]["med_cx"]) / 2, 0.0]]
        else:
            part = _follow_single(_series(dom[0], n, "cx"), s["a"], fps, cfg, cw_frac)
        part[-1][1] = s["b"]
        segs.extend(part)
        last_x = part[-1][2]

    # face centre (0..1) -> crop x in source pixels, merge near-identical neighbours
    out: list[list[float]] = []
    for a, b, cx, ease in segs:
        x = int(np.clip(cx * W - cw / 2, 0, W - cw))
        if out and abs(out[-1][2] - x) <= cfg["deadband"] * cw * 0.6:
            out[-1][1] = b
        else:
            out.append([round(a, 3), round(b, 3), x, round(min(ease, (b - a) * 0.8), 3)])
    out[0][0] = 0.0
    out[-1][1] = round(dur, 3)
    result = {"layout": layout, "cw": cw, "segments": out, "face": _opening_face(frames, fps, cfg),
              "shots": len(shots), "faces_share": round(face_time / dur, 2)}
    if layout == "stack":
        result["broll"] = broll
    return result


def _screen_plan(src: str, meta: dict, start: float, dur: float, cfg: dict,
                 content: str, face_share: float) -> dict:
    """Cursor-following zoom for screen content; plain fit when nothing moves."""
    why = f"content={content}, faces {face_share:.0%} of the clip"
    if not cfg.get("cursor_track", True):
        return {"layout": "fit", "reason": why}
    W, H = meta["width"], meta["height"]
    pos = CUR.smooth_positions(CUR.detect_cursor_positions(src, start, dur, W, H, cfg["cursor_interval"]))
    if sum(1 for p in pos if p.get("moved")) < 3:
        return {"layout": "fit", "reason": why + ", no cursor movement"}
    crop_w, kf = CUR.crop_keyframes(W, pos, cfg["screen_zoom"])
    segs = [[round(t, 3), round(kf[i + 1][0] if i + 1 < len(kf) else dur, 3), x,
             0.0 if i == 0 else min(0.4, t - kf[i - 1][0])] for i, (t, x) in enumerate(kf)]
    segs[-1][1] = round(dur, 3)
    return {"layout": "screen", "cw": crop_w, "segments": segs, "reason": why}


def _opening_face(frames: list[dict], fps: float, cfg: dict) -> dict | None:
    """Median box of the biggest face during the hook window (0..1 of frame height)."""
    n = max(1, int(cfg["hook_seconds"] * fps) + 1)
    best = [max(f["faces"], key=lambda x: x["h"]) for f in frames[:n] if f["faces"]]
    if not best:
        return None
    return {"cy": round(float(np.median([b["cy"] for b in best])), 3),
            "h": round(float(np.median([b["h"] for b in best])), 3)}


def hook_position(plan_: dict, cfg: dict) -> int:
    """Hook card y (1920 space): top safe zone unless the face is there, then under the chin."""
    top = cfg["hook_y"]
    face = plan_.get("face") if plan_["layout"] in ("crop", "center") else None
    if not face:
        return top
    H = 1920                                               # hook/caption positions live in 1920 space
    face_top, face_bot = (face["cy"] - face["h"] * 0.6) * H, (face["cy"] + face["h"] * 0.75) * H
    card_h = 300                                           # two lines of hook text + padding
    if face_top > top + card_h:
        return top
    below = face_bot + 40
    if below + card_h < cfg["caption_y"] - 170:
        return int(below)
    return 120                                             # squeeze into the very top


def _split_plan(pair: list[dict], meta: dict, cfg: dict) -> dict:
    W, H = meta["width"], meta["height"]
    cell_w, cell_h = cfg["out_w"], cfg["out_h"] // 2
    cells = []
    for t in pair:
        ch = min(H, max(0.5 * H, t["med_h"] * H * 3.4))
        cwid = ch * cell_w / cell_h
        if cwid > W:
            cwid, ch = W, W * cell_h / cell_w
        x = np.clip(t["med_cx"] * W - cwid / 2, 0, W - cwid)
        y = np.clip(t["med_cy"] * H - cfg["face_headroom"] * ch, 0, H - ch)
        cells.append({"x": _even(x), "y": _even(y), "w": _even(cwid), "h": _even(ch)})
    return {"layout": "split", "top": cells[0], "bottom": cells[1]}


# ---------------------------------------------------------------- filters
def _seg_value(i: int, segs: list[list[float]]) -> str:
    """crop-x expression while segment i is active (eased pan from the previous x)."""
    a, _, x = segs[i][:3]
    ease = segs[i][3] if len(segs[i]) > 3 else 0.0
    if i == 0 or ease <= 0:
        return str(x)
    px = segs[i - 1][2]
    return f"({px}+({x - px})*min(1,(t-{a:.3f})/{ease:.3f}))"


def _x_expr(segs: list[list[float]]) -> str:
    expr = _seg_value(len(segs) - 1, segs)
    for i in range(len(segs) - 2, -1, -1):
        expr = f"if(lt(t,{segs[i][1]:.3f}),{_seg_value(i, segs)},{expr})"
    return expr


def video_filter(plan_: dict, meta: dict, cfg: dict, inp: str = "[0:v]", broll_inp: str = "[1:v]") -> str:
    """Filter chain ending in label [rf] at out_w x out_h, clip-relative timeline."""
    OW, OH = cfg["out_w"], cfg["out_h"]
    lay = plan_["layout"]
    if lay == "audio":
        return (f"[0:a]showwaves=s={OW}x420:mode=cline:colors=white@0.85,format=yuva420p[wv];"
                f"color=c=0x101418:s={OW}x{OH}:r={cfg['fps']}[bg];"
                f"[bg][wv]overlay=0:(H-h)/2:shortest=1[rf]")
    if lay == "native":                                   # keep the source framing
        return f"{inp}scale={OW}:{OH},setsar=1[rf]"
    if lay == "fit":
        return (f"{inp}split=2[bgs][fgs];"
                f"[bgs]scale={OW}:{OH}:force_original_aspect_ratio=increase,crop={OW}:{OH},"
                f"scale={OW // 8}:{OH // 8},gblur=sigma=6,scale={OW}:{OH},eq=brightness=-0.08[bgb];"
                f"[fgs]scale={OW}:{OH}:force_original_aspect_ratio=decrease[fgf];"
                f"[bgb][fgf]overlay=(W-w)/2:(H-h)/2-120,setsar=1[rf]")
    if lay == "screen":
        cw, H = plan_["cw"], meta["height"]
        return (f"{inp}split=2[bgs][fgs];"
                f"[bgs]scale={OW}:{OH}:force_original_aspect_ratio=increase,crop={OW}:{OH},"
                f"scale={OW // 8}:{OH // 8},gblur=sigma=6,scale={OW}:{OH},eq=brightness=-0.08[bgb];"
                f"[fgs]crop=w={cw}:h={H}:x='{_x_expr(plan_['segments'])}':y=0,"
                f"scale={OW}:-2:flags=lanczos[fgf];"
                f"[bgb][fgf]overlay=(W-w)/2:(H-h)/2-120,setsar=1[rf]")
    if lay in ("center", "crop"):
        cw, H = plan_.get("cw") or crop_width(meta, cfg), meta["height"]
        segs = plan_.get("segments") or [[0.0, 1.0, (meta["width"] - cw) // 2]]
        ch = H
        if cfg.get("strip_source_captions"):
            # crop above the source's burned-in caption band so only our captions show
            ch = _even(H * cfg["caption_band_frac"])
            new_cw = _even(ch * OW / OH)
            segs = [[g[0], g[1], int(np.clip(g[2] + (cw - new_cw) / 2, 0, meta["width"] - new_cw)), *g[3:]]
                    for g in segs]
            cw = new_cw
        return (f"{inp}crop=w={cw}:h={ch}:x='{_x_expr(segs)}':y=0,"
                f"scale={OW}:{OH}:flags=lanczos,setsar=1[rf]")
    if lay == "split":
        t, b = plan_["top"], plan_["bottom"]
        half = OH // 2
        return (f"{inp}split=2[sa][sb];"
                f"[sa]crop={t['w']}:{t['h']}:{t['x']}:{t['y']},scale={OW}:{half}:flags=lanczos[top];"
                f"[sb]crop={b['w']}:{b['h']}:{b['x']}:{b['y']},scale={OW}:{half}:flags=lanczos[bot];"
                f"[top][bot]vstack=inputs=2,setsar=1[rf]")
    if lay == "stack":
        half = OH // 2
        cw = _even(meta["height"] * OW / half)            # 9:8 cell from the main video
        cw = min(cw, meta["width"] - meta["width"] % 2)
        segs = plan_.get("segments") or [[0, 1, (meta["width"] - cw) // 2, 0.0]]
        segs = [[g[0], g[1], int(np.clip(g[2] + (plan_.get("cw", cw) - cw) / 2, 0, meta["width"] - cw)),
                 *g[3:]] for g in segs]
        return (f"{inp}crop=w={cw}:h={meta['height']}:x='{_x_expr(segs)}':y=0,"
                f"scale={OW}:{half}:flags=lanczos,setsar=1[top];"
                f"{broll_inp}scale={OW}:{half}:force_original_aspect_ratio=increase,"
                f"crop={OW}:{half},setsar=1[bot];"
                f"[top][bot]vstack=inputs=2,setsar=1[rf]")
    raise ValueError(f"unknown layout {lay}")


def caption_anchor(plan_: dict, cfg: dict) -> tuple[int, int]:
    """(alignment, y) for captions: seam of split/stack layouts, else lower third."""
    if plan_["layout"] in ("split", "stack"):
        return 5, cfg["out_h"] // 2
    return 2, cfg["caption_y"]
