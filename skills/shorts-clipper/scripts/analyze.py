"""Speech analysis: pauses, loudness energy, sentences, and the compact
transcript Claude reads to pick moments."""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

from common import fmt_ts

SENT_END = (".", "?", "!", "…")


# ---------------------------------------------------------------- pauses
def _read_wav(wav: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(wav), "rb") as w:
        sr = w.getframerate()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    return data, sr


def detect_silences(wav: Path, min_dur: float, offset: float = 0.0,
                    fixed_db: float | None = None) -> list[list[float]]:
    """Pauses = runs of 20 ms frames quieter than an ADAPTIVE threshold
    (noise floor + a share of the speech-to-floor range), so room tone,
    music beds and hiss don't hide real pauses the way a fixed -dB gate does."""
    data, sr = _read_wav(wav)
    n = int(sr * 0.02)
    frames = len(data) // n
    if frames < 10:
        return []
    db = 20 * np.log10(np.sqrt(np.mean(data[: frames * n].reshape(frames, n) ** 2, axis=1)) + 1e-6)
    if fixed_db is not None:
        thr = fixed_db
    else:
        floor = float(np.percentile(db, 5))
        speech = float(np.percentile(db, 70))
        thr = floor + max(6.0, 0.45 * (speech - floor))
    quiet = db < thr
    out, i = [], 0
    while i < frames:
        if quiet[i]:
            j = i
            while j < frames and quiet[j]:
                j += 1
            if (j - i) * 0.02 >= min_dur:
                out.append([round(i * 0.02 + offset, 3), round(j * 0.02 + offset, 3)])
            i = j
        else:
            i += 1
    return out


# ---------------------------------------------------------------- energy
def energy_curve(wav: Path, hop: float = 0.5, offset: float = 0.0) -> dict:
    """RMS loudness (dBFS) every `hop` seconds + robust z-score per bin."""
    data, sr = _read_wav(wav)
    n = int(sr * hop)
    bins = len(data) // n
    if bins == 0:
        return {"hop": hop, "offset": offset, "db": [], "z": []}
    frames = data[: bins * n].reshape(bins, n)
    db = 20 * np.log10(np.sqrt(np.mean(frames ** 2, axis=1)) + 1e-6)
    voiced = db[db > -45]
    med = float(np.median(voiced)) if voiced.size else float(np.median(db))
    mad = float(np.median(np.abs(voiced - med))) if voiced.size else 1.0
    z = (db - med) / (1.4826 * mad + 1e-6)
    return {"hop": hop, "offset": offset, "db": [round(float(x), 1) for x in db],
            "z": [round(float(x), 2) for x in z]}


def energy_between(energy: dict, start: float, end: float) -> float:
    """Mean z-score of loudness between two source times."""
    z = energy.get("z") or []
    if not z:
        return 0.0
    hop, off = energy["hop"], energy.get("offset", 0.0)
    a = max(0, int((start - off) / hop))
    b = min(len(z), max(a + 1, int((end - off) / hop)))
    seg = z[a:b]
    return float(np.mean(seg)) if seg else 0.0


# ---------------------------------------------------------------- sentences
def sentences(words: list[dict], pause_break: float, max_words: int) -> list[dict]:
    """Group words into sentence-like units: punctuation, long pauses, or length."""
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(i)
        nxt = words[i + 1] if i + 1 < len(words) else None
        gap = (nxt["start"] - w["end"]) if nxt else 99
        if w["text"].endswith(SENT_END) or gap >= pause_break or len(cur) >= max_words or nxt is None:
            out.append({"i0": cur[0], "i1": cur[-1], "start": words[cur[0]]["start"],
                        "end": words[cur[-1]]["end"],
                        "text": " ".join(words[k]["text"] for k in cur),
                        "gap_after": round(gap, 2) if nxt else None})
            cur = []
    return out


# ---------------------------------------------------------------- transcript for Claude
def write_transcript_md(path: Path, sents: list[dict], energy: dict, meta: dict,
                        words_count: int, language: str) -> None:
    """One line per sentence: `[mm:ss.s] text`. Marks loud passages and long pauses.
    Compact on purpose: an hour of speech stays readable in a few chunks."""
    lines = [
        f"# Transcript — {Path(meta['path']).name}",
        f"duration {fmt_ts(meta['duration'])} · {words_count} words · language {language}",
        "Legend: [start] text · ⚡ = louder/high-energy than usual · ⏸ = pause after (s)",
        "",
    ]
    for s in sents:
        z = energy_between(energy, s["start"], s["end"])
        flag = " ⚡" if z >= 1.0 else ""
        pause = f" ⏸{s['gap_after']:.1f}" if s.get("gap_after") and s["gap_after"] >= 1.5 else ""
        lines.append(f"[{fmt_ts(s['start'])}] {s['text']}{flag}{pause}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
