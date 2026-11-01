"""Snap rough [start, end] picks to natural speech boundaries.

Priority: never cut inside a word > start on a sentence start / end on a sentence
end > land in a pause > respect clip_min/clip_max.
"""
from __future__ import annotations

from analyze import SENT_END


def _sent_starts(words):
    return [i for i, w in enumerate(words) if i == 0 or words[i - 1]["text"].endswith(SENT_END)]


def _sent_ends(words):
    return [i for i, w in enumerate(words) if w["text"].endswith(SENT_END)]


def _closest(idxs, words, key, t, window, prefer=None):
    """Closest word index whose `key` time is within `window` of t.
    prefer='before'/'after' halves the distance on the preferred side."""
    best, best_d = None, None
    for i in idxs:
        d = words[i][key] - t
        if abs(d) > window:
            continue
        score = abs(d)
        if prefer == "before" and d <= 0 or prefer == "after" and d >= 0:
            score *= 0.5
        if best_d is None or score < best_d:
            best, best_d = i, score
    return best


def snap(words: list[dict], start: float, end: float, cfg: dict,
         source_duration: float) -> tuple[float, float, dict]:
    lo, hi = cfg["clip_min"], cfg["clip_max"]
    if not words:
        return start, end, {"note": "no words"}
    starts, ends = _sent_starts(words), _sent_ends(words)
    all_idx = range(len(words))

    # --- start: sentence start near t (prefer slightly earlier = more context)
    i0 = _closest(starts, words, "start", start, 3.0, prefer="before")
    how_s = "sentence"
    if i0 is None:
        i0 = _closest(all_idx, words, "start", start, 1.5, prefer="after")
        how_s = "word"
    if i0 is None:
        i0 = next((i for i, w in enumerate(words) if w["start"] >= start), len(words) - 1)
        how_s = "next-word"

    # --- end: sentence end near t (prefer slightly later = complete payoff)
    i1 = _closest([i for i in ends if i >= i0], words, "end", end, 3.5, prefer="after")
    how_e = "sentence"
    if i1 is None:
        i1 = _closest([i for i in all_idx if i >= i0], words, "end", end, 1.5, prefer="before")
        how_e = "word"
    if i1 is None:
        i1 = max(i0, max((i for i, w in enumerate(words) if w["end"] <= end), default=i0))
        how_e = "prev-word"

    s, e = words[i0]["start"], words[i1]["end"]

    # --- enforce length using sentence ends first, then word ends
    if e - s > hi:
        cands = [i for i in ends if i0 <= i and lo <= words[i]["end"] - s <= hi]
        cands = cands or [i for i in all_idx if i >= i0 and lo <= words[i]["end"] - s <= hi]
        if cands:
            i1 = max(cands)
            e = words[i1]["end"]
            how_e += "+trimmed"
    if e - s < lo:
        cands = [i for i in ends if i > i1 and words[i]["end"] - s <= hi]
        if cands:
            i1 = min(cands, key=lambda i: abs((words[i]["end"] - s) - (lo + hi) / 2) if words[i]["end"] - s >= lo else 1e9)
            e = words[i1]["end"]
            how_e += "+extended"

    # --- breathing room, never eating into neighbouring words
    prev_end = words[i0 - 1]["end"] if i0 > 0 else 0.0
    next_start = words[i1 + 1]["start"] if i1 + 1 < len(words) else source_duration
    s = max(prev_end + 0.02, s - 0.12, 0.0)
    e = min(next_start - 0.03, e + 0.30, source_duration)
    return round(s, 3), round(e, 3), {"start_on": how_s, "end_on": how_e}
