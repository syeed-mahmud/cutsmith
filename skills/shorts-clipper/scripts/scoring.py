"""Deterministic 'signal' score for a candidate window + candidate generation.

The signal score is a cheap, explainable prior. Claude's own rubric judgement
(the `judge` field in picks.json) is blended on top of it for the final rank.
"""
from __future__ import annotations

import math
import re

from analyze import energy_between

NUM = re.compile(r"(\$|€|£|%|\b\d[\d,.]*\b|\b(two|three|four|five|six|seven|eight|nine|ten|"
                 r"hundred|thousand|million|billion)\b)", re.I)
QUESTION = re.compile(r"\?")
YOU = re.compile(r"\b(you|your|you're|yourself)\b", re.I)
OPENER = re.compile(r"^(here'?s|this is|the (one|biggest|real|only|secret|truth|problem|reason|mistake)|"
                    r"why|how|what|stop|never|nobody|no one|everyone|imagine|listen|look|if you|"
                    r"most people|i (quit|lost|made|built|spent|tried|tested|learned|was wrong)|"
                    r"don'?t|you (need|should|have|can'?t|won'?t))\b", re.I)
CURIOSITY = re.compile(r"\b(secret|nobody (tells|talks)|no one (tells|knows)|the truth|the real reason|"
                       r"mistake|wrong|myth|surprising|shocking|crazy|insane|weird|actually|"
                       r"turns out|here'?s the thing|the problem is|what (nobody|most people))\b", re.I)
CONTRAST = re.compile(r"\b(but|however|instead|except|the difference|versus|vs\.?|rather than)\b", re.I)
LISTY = re.compile(r"\b(first|second|third|step (one|two|three|\d)|number (one|two|\d)|"
                   r"(three|two|five|\d) (things|ways|reasons|steps|rules|mistakes))\b", re.I)
EMOTION = re.compile(r"\b(love|hate|scared|afraid|angry|crazy|insane|amazing|terrible|honestly|"
                     r"shocked|cried|laughed|never forget|changed my life|hilarious)\b|haha|\(laugh", re.I)
CONNECTOR_START = re.compile(r"^(and|so|but|because|which|that|also|then|or|plus|anyway)\b", re.I)
PRONOUN_START = re.compile(r"^(he|she|they|it|this|that|those|these|him|her|them)\b", re.I)
BACKREF = re.compile(r"\b(as i (said|mentioned)|like i said|going back to|earlier (i|we)|"
                     r"we (talked|discussed) about|the (previous|last) (point|question))\b", re.I)


def _bell(x: float, mu: float, sigma: float) -> float:
    return math.exp(-((x - mu) ** 2) / (2 * sigma ** 2))


def _sig(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def window_words(words: list[dict], start: float, end: float) -> list[dict]:
    return [w for w in words if w["start"] >= start - 0.01 and w["end"] <= end + 0.01]


def silence_ratio(pauses: list[list[float]], start: float, end: float) -> float:
    dur = max(0.1, end - start)
    sil = sum(max(0.0, min(e, end) - max(s, start)) for s, e in pauses if e > start and s < end)
    return min(1.0, sil / dur)


def signal_score(words: list[dict], pauses: list, energy: dict, start: float, end: float,
                 cfg: dict, keywords: list[str] | None = None) -> tuple[float, dict]:
    ws = window_words(words, start, end)
    dur = max(0.1, end - start)
    if len(ws) < 4:
        return 0.0, {}
    text = " ".join(w["text"] for w in ws)
    head = " ".join(w["text"] for w in ws if w["start"] < start + 3.0)
    first = ws[0]["text"]

    hook = 0.25
    hook += 0.20 if OPENER.search(head) else 0
    hook += 0.15 if QUESTION.search(head) else 0
    hook += 0.12 if NUM.search(head) else 0
    hook += 0.10 if YOU.search(head) else 0
    hook += 0.18 if CURIOSITY.search(head) else 0
    hook -= 0.25 if CONNECTOR_START.match(first) else 0
    hook = max(0.0, min(1.0, hook))

    standalone = 1.0
    standalone -= 0.35 if PRONOUN_START.match(first) else 0
    standalone -= 0.25 if CONNECTOR_START.match(first) else 0
    standalone -= 0.35 if BACKREF.search(text) else 0
    standalone = max(0.0, standalone)

    last = ws[-1]["text"]
    payoff = 1.0 if last.endswith((".", "!", "?", "…")) else 0.4

    e_mean = energy_between(energy, start, end)
    e_head = energy_between(energy, start, min(end, start + 4))
    energy_s = 0.6 * _sig(e_mean * 1.2) + 0.4 * _sig(e_head * 1.2)

    cues = sum(bool(rx.search(text)) for rx in (NUM, CURIOSITY, CONTRAST, LISTY, EMOTION, QUESTION))
    info = min(1.0, cues / 4.0)

    wpm = len(ws) / dur * 60
    pace = _bell(wpm, cfg["ideal_wpm"], 50)
    alive = 1.0 - silence_ratio(pauses, start, end)

    parts = {"hook": hook, "standalone": standalone, "payoff": payoff, "energy": energy_s,
             "info": info, "pace": pace, "alive": alive}
    weights = {"hook": 0.30, "standalone": 0.15, "payoff": 0.15, "energy": 0.15,
               "info": 0.10, "pace": 0.08, "alive": 0.07}
    score = sum(weights[k] * parts[k] for k in weights)
    score *= 0.75 + 0.25 * _bell(dur, cfg["clip_ideal"], 12)
    if keywords:
        hit = any(re.search(rf"\b{re.escape(k)}\b", text, re.I) for k in keywords)
        score *= 1.2 if hit else 0.85
        parts["keyword"] = 1.0 if hit else 0.0
    return round(min(100.0, 100 * score), 1), {k: round(v, 2) for k, v in parts.items()}


def generate(words: list[dict], sents: list[dict], pauses: list, energy: dict, cfg: dict,
             top: int, keywords: list[str] | None = None,
             rng: tuple[float, float] | None = None) -> list[dict]:
    """Sentence-aligned windows within [clip_min, clip_max], best end per start,
    then greedy non-overlapping selection by score."""
    lo, hi = cfg["clip_min"], cfg["clip_max"]
    best_per_start = []
    for a in range(len(sents)):
        s = sents[a]["start"]
        if rng and not (rng[0] <= s < rng[1]):
            continue
        best = None
        for b in range(a, len(sents)):
            e = sents[b]["end"]
            if rng and e > rng[1]:
                break
            d = e - s
            if d > hi:
                break
            if d < lo:
                continue
            sc, parts = signal_score(words, pauses, energy, s, e, cfg, keywords)
            if best is None or sc > best[0]:
                best = (sc, s, e, parts, a, b)
        if best:
            best_per_start.append(best)
    best_per_start.sort(key=lambda x: -x[0])

    gap = cfg["min_gap_between_clips"]
    chosen = []
    for sc, s, e, parts, a, b in best_per_start:
        if all(e + gap <= cs or s >= ce + gap for _, cs, ce, *_ in chosen):
            chosen.append((sc, s, e, parts, a, b))
        if len(chosen) >= top:
            break
    chosen.sort(key=lambda x: x[1])
    out = []
    for k, (sc, s, e, parts, a, b) in enumerate(chosen, 1):
        text = " ".join(x["text"] for x in sents[a:b + 1])
        out.append({"id": f"c{k:02d}", "start": round(s, 2), "end": round(e, 2),
                    "duration": round(e - s, 1), "signal": sc, "parts": parts,
                    "opening": " ".join(text.split()[:22]),
                    "ending": " ".join(text.split()[-14:])})
    return out
