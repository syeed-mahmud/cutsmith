"""Candidate finder for spoken mistakes.

Scans a word-level transcript for things a speaker would want gone:
  - stutters / immediate word repeats ("the the", "I-I-I", "that that")
  - retakes / duplicate sentences (a line re-recorded; keep the clean take)
  - false starts (an abandoned phrase that restarts)

MECHANICAL detection only: it proposes cut ranges with context, a confidence and
a default recommendation. Emphatic repetition ("never, never") looks identical
to a stutter, so a human/agent must review before cutting.
"""
from __future__ import annotations

import re

STOP = set("a an and are as at be but by for from i if in is it of on or so that this to was we with "
           "you your they their there what when where why how like um uh".split())


def _clean_token(text: str) -> str:
    return re.sub(r"^[^a-z0-9$%]+|[^a-z0-9$%]+$", "", (text or "").lower())


def _display(ws: list[dict]) -> str:
    return re.sub(r"\s+([,.!?;:])", r"\1", " ".join(w["text"] for w in ws))


def _fmt(s: float) -> str:
    m = int(s // 60)
    return f"{m:02d}:{s - m * 60:05.2f}"


def find_candidates(raw_words: list[dict]) -> dict:
    words = [{"index": i, "text": w["text"], "token": _clean_token(w["text"]),
              "start": float(w["start"]), "end": float(w["end"])} for i, w in enumerate(raw_words)]
    if not words:
        return {"candidates": [], "counts": {}, "segment_count": 0, "proposed_cut_seconds": 0.0}

    candidates: list[dict] = []
    cid = 0

    def ctx(i: int, before: int = 6, after: int = 6) -> str:
        return _display(words[max(0, i - before): min(len(words), i + after)])

    # ---- 1) stutters / immediate repeats
    for i in range(1, len(words)):
        a, b = words[i - 1], words[i]
        if not b["token"]:
            continue
        if a["token"] == b["token"] and b["start"] - a["end"] < 0.5:
            cid += 1
            candidates.append({
                "id": f"m{cid:03d}", "type": "stutter", "confidence": "high", "recommend": "cut",
                "cut": {"start": round(a["start"], 3), "end": round(b["start"], 3)},
                "removes": a["text"], "keeps": b["text"], "context": ctx(i),
                "note": f'repeated "{a["token"]}"',
            })

    # ---- build segments (for retake / false-start detection)
    segments, seg_start = [], 0
    for i in range(1, len(words)):
        prev, cur = words[i - 1], words[i]
        gap = cur["start"] - prev["end"]
        elapsed = cur["end"] - words[seg_start]["start"]
        sentence_end = bool(re.search(r"[.!?]$", prev["text"] or ""))
        if gap > 0.85 or (sentence_end and elapsed >= 7.5) or elapsed >= 16:
            segments.append({"id": len(segments) + 1, "start": words[seg_start]["start"], "end": prev["end"],
                             "text": _display(words[seg_start:i])})
            seg_start = i
    segments.append({"id": len(segments) + 1, "start": words[seg_start]["start"], "end": words[-1]["end"],
                     "text": _display(words[seg_start:])})

    def signature(seg):
        toks = re.sub(r"[^a-z0-9\s$%]", " ", seg["text"].lower()).split()
        return [t for t in toks if len(t) > 2 and t not in STOP][:18]

    def jaccard(a, b):
        sa, sb = set(a), set(b)
        return len(sa & sb) / max(1, len(sa | sb))

    sigs = [signature(s) for s in segments]

    # ---- 2) retakes / duplicate sentences (keep the later, cleaner take)
    for i in range(len(segments)):
        for j in range(i + 1, min(len(segments), i + 9)):
            if segments[j]["start"] - segments[i]["end"] > 75:
                break
            score = jaccard(sigs[i], sigs[j])
            same_opening = sum(1 for k, t in enumerate(sigs[i][:4]) if k < len(sigs[j]) and t == sigs[j][k]) >= 2
            if score >= 0.34 or (score >= 0.24 and same_opening):
                first_len = segments[i]["end"] - segments[i]["start"]
                is_false_start = same_opening and first_len < 6 and segments[j]["start"] - segments[i]["end"] < 12
                cid += 1
                candidates.append({
                    "id": f"m{cid:03d}",
                    "type": "false_start" if is_false_start else "retake",
                    "confidence": "high" if score >= 0.45 else "medium" if score >= 0.34 else "low",
                    "recommend": "cut" if score >= 0.34 else "review",
                    "score": round(score, 3),
                    "cut": {"start": round(segments[i]["start"], 3), "end": round(segments[j]["start"], 3)},
                    "removes": segments[i]["text"], "keeps": segments[j]["text"],
                    "gap_between": round(segments[j]["start"] - segments[i]["end"], 2),
                    "note": f'seg {segments[i]["id"]} ~ seg {segments[j]["id"]} (jaccard {score:.2f}'
                            f'{", same opening" if same_opening else ""})',
                })

    candidates.sort(key=lambda c: c["cut"]["start"])
    counts: dict[str, int] = {}
    for c in candidates:
        counts[c["type"]] = counts.get(c["type"], 0) + 1
    proposed = sum(c["cut"]["end"] - c["cut"]["start"] for c in candidates if c["recommend"] == "cut")
    return {"word_count": len(words), "segment_count": len(segments), "counts": counts,
            "proposed_cut_seconds": round(proposed, 2), "candidates": candidates}


def to_markdown(result: dict) -> str:
    lines = ["# Spoken-mistake candidates (stutters, retakes, false starts)", "",
             f"{len(result['candidates'])} candidates · "
             + ", ".join(f"{v} {k}" for k, v in result["counts"].items()),
             "**Review in context** — emphasis ('never, never') looks like a stutter. To remove one "
             "inside a clip, add its cut range to that pick's `\"remove\"` list.", ""]
    for c in result["candidates"]:
        lines += [f"## {c['id']} · {c['type']} · {c['confidence']} · recommend: {c['recommend']}",
                  f"cut {_fmt(c['cut']['start'])}–{_fmt(c['cut']['end'])} "
                  f"({c['cut']['end'] - c['cut']['start']:.2f}s) — {c['note']}", "",
                  f"- removes: {c['removes']}"]
        if c.get("keeps"):
            lines.append(f"- keeps:   {c['keeps']}")
        if c.get("context"):
            lines.append(f"- context: …{c['context']}…")
        lines.append("")
    return "\n".join(lines)
