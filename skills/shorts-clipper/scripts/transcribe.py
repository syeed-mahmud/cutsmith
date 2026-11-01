"""Word-level transcription with faster-whisper (CPU int8 or CUDA float16)."""
from __future__ import annotations

import time
from pathlib import Path

from common import die, log


def _device() -> tuple[str, str]:
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", "float16"
    except Exception:
        pass
    return "cpu", "int8"


def transcribe(wav: Path, cfg: dict, model_name: str | None = None,
               language: str | None = None, offset: float = 0.0) -> dict:
    """Return {language, words:[{text,start,end,p}], model, device, seconds}.

    `offset` is added to every timestamp so words are always in SOURCE time
    even when only a range of the source was transcribed.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        die("faster-whisper is not installed. Run setup.sh (see SKILL.md > Setup).")

    device, compute = _device()
    model_name = model_name or (cfg["whisper_model_gpu"] if device == "cuda" else cfg["whisper_model_cpu"])
    log(f"[transcribe] model={model_name} device={device}/{compute} (first run downloads the model)")
    t0 = time.time()
    model = WhisperModel(model_name, device=device, compute_type=compute)
    # Decode the 16 kHz mono wav ourselves: skips PyAV, whose API changes break faster-whisper.
    import wave

    import numpy as np
    with wave.open(str(wav), "rb") as w:
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    segments, info = model.transcribe(
        audio,
        language=language or cfg.get("language"),
        beam_size=cfg["whisper_beam"],
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400, "speech_pad_ms": 150},
        condition_on_previous_text=False,   # avoids long hallucinated loops
        language_detection_segments=4,      # vote over up to 2 min of audio, not just the first 30 s
    )
    if not (language or cfg.get("language")) and info.language_probability < 0.75:
        log(f"[transcribe] WARNING: language guess '{info.language}' is uncertain "
            f"(p={info.language_probability:.2f}). If wrong, re-run with --lang <code> (e.g. --lang en).")
    words = []
    last_report = 0.0
    for seg in segments:
        for w in seg.words or []:
            text = w.word.strip()
            if not text:
                continue
            words.append({"text": text, "start": round(w.start + offset, 3),
                          "end": round(max(w.end, w.start + 0.02) + offset, 3),
                          "p": round(float(w.probability), 3)})
        if seg.end - last_report > 120:
            last_report = seg.end
            log(f"[transcribe] ... {int(seg.end // 60)} min done")
    words.sort(key=lambda w: w["start"])
    for i in range(1, len(words)):                 # no overlapping words
        if words[i]["start"] < words[i - 1]["end"]:
            words[i - 1]["end"] = max(words[i - 1]["start"] + 0.02, words[i]["start"])
    elapsed = time.time() - t0
    log(f"[transcribe] {len(words)} words, language={info.language} in {elapsed:.0f}s")
    return {"language": info.language, "language_probability": round(info.language_probability, 3),
            "model": model_name, "device": device, "seconds": round(elapsed, 1), "words": words}
