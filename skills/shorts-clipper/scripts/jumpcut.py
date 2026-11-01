"""Dead-air and filler removal.

Pauses come from the AUDIO (adaptive quiet-run detector), not from Whisper's
word gaps, because Whisper word timings drift by up to ~0.5 s. Each cut keeps a
short breath on both sides so word onsets/tails survive. Fillers ("um", "uh")
are cut from the transcript when Whisper reports them. All keep-window edges are
snapped to the output frame grid so audio and video stay in sync across seams.
"""
from __future__ import annotations

import re


def _norm(t: str) -> str:
    return re.sub(r"[^a-z]", "", t.lower())


def _merge(ivs: list[list[float]]) -> list[list[float]]:
    out: list[list[float]] = []
    for s, e in sorted(ivs):
        if out and s <= out[-1][1] + 1e-3:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out


def plan_keeps(words: list[dict], pauses: list[list[float]], start: float, end: float,
               cfg: dict, extra_cuts: list[list[float]] | None = None) -> list[list[float]]:
    """Keep windows (clip-relative seconds) covering [0, end-start] minus cuts.
    `extra_cuts` are source-time ranges to remove regardless of pace (approved
    stutters/retakes, tangents Claude listed in a pick's "remove")."""
    dur = end - start
    pace = cfg.get("pace", "natural")
    cuts: list[list[float]] = [[max(a, start), min(b, end)] for a, b in (extra_cuts or [])
                               if b > start and a < end and b - a > 0.05]
    if pace == "off" or pace not in cfg["pace_presets"]:
        if not cuts:
            return [[0.0, round(dur, 3)]]
        p = {"gap": 1e9, "keep": 0.0}
        pauses = []
    else:
        p = cfg["pace_presets"][pace]
    fillers = set(cfg["fillers"]) if pace != "off" else set()
    ws = [w for w in words if w["start"] >= start - 0.05 and w["end"] <= end + 0.05]

    for ps, pe in pauses:
        if pe <= start or ps >= end or pe - ps < p["gap"]:
            continue
        a, b = max(ps, start) + p["keep"], min(pe, end) - p["keep"]
        if b - a > 0.08:
            cuts.append([a, b])

    for i, w in enumerate(ws):
        if _norm(w["text"]) not in fillers:
            continue
        prev_end = ws[i - 1]["end"] if i > 0 else start
        next_start = ws[i + 1]["start"] if i + 1 < len(ws) else end
        a = max(prev_end + 0.04, w["start"] - 0.02)
        b = min(next_start - 0.04, w["end"] + 0.02)
        if b - a > 0.06:
            cuts.append([a, b])

    fps = cfg["fps"]
    grid = lambda t: round(round((t - start) * fps) / fps, 4)
    cuts = _merge([[grid(a), grid(b)] for a, b in cuts if grid(b) > grid(a)])
    keeps, cur = [], 0.0
    for a, b in cuts:
        a, b = max(0.0, a), min(dur, b)
        if a > cur:
            keeps.append([cur, a])
        cur = max(cur, b)
    if cur < dur:
        keeps.append([cur, round(round(dur * fps) / fps, 4)])
    keeps = [k for k in keeps if k[1] - k[0] >= cfg["min_keep_window"]]
    return keeps or [[0.0, round(dur, 3)]]


def remap_words(words: list[dict], keeps: list[list[float]], start: float, end: float,
                fillers: set[str]) -> tuple[list[dict], float]:
    """Words on the output timeline (after cuts). Fillers and cut words dropped."""
    offs, acc = [], 0.0
    for a, b in keeps:
        offs.append((a, b, acc))
        acc += b - a

    def to_out(t: float):
        """Clip-relative source time -> output time. Times inside a cut snap to
        the next kept moment (Whisper timings can land inside a removed pause)."""
        for a, b, o in offs:
            if t < a:
                return o
            if t <= b:
                return o + t - a
        return None

    out = []
    for w in words:
        if w["end"] <= start or w["start"] >= end or _norm(w["text"]) in fillers:
            continue
        s = to_out(w["start"] - start)
        if s is None:
            continue
        e = to_out(w["end"] - start)
        if e is None or e <= s:
            e = s + max(0.08, min(0.6, w["end"] - w["start"]))
        out.append({"text": w["text"], "start": round(s, 3), "end": round(min(e, acc), 3)})
    return out, round(acc, 3)


def select_expr(keeps: list[list[float]]) -> str:
    return "+".join(f"between(t,{a:.4f},{b - 0.0005:.4f})" for a, b in keeps)
