"""Every tunable in one place.

Values here are defaults. A project can override any key through
`.work/settings.json` (written by `clipper.py prep/render --set key=value`).
"""
from __future__ import annotations

import copy
import json

from common import home_dir, load_json, save_json

DEFAULTS: dict = {
    # ---------------- transcription
    "whisper_model_cpu": "small",           # good speed/quality on CPU (multilingual)
    "whisper_model_gpu": "large-v3-turbo",  # used automatically when CUDA is available
    "whisper_beam": 5,
    "language": None,                       # None = auto-detect

    # ---------------- clip length + count
    "clip_min": 20.0,
    "clip_max": 60.0,
    "clip_ideal": 35.0,                     # sweet spot used by the signal score
    "clips_per_10min": 4,                   # default count when the user gives none
    "clips_default_min": 3,
    "clips_default_max": 15,
    "min_gap_between_clips": 5.0,           # seconds of separation between picks

    # ---------------- speech analysis
    "silence_noise_db": None,               # None = adaptive to the noise floor; or a fixed dBFS gate
    "silence_min": 0.30,                    # shortest pause that counts
    "sentence_pause": 0.9,                  # a pause this long also ends a "sentence"
    "max_sentence_words": 45,

    # ---------------- jump-cut (dead-air removal)
    "pace": "natural",                      # off | natural | tight
    "pace_presets": {
        "natural": {"gap": 0.60, "keep": 0.16},  # trim pauses >= gap down to ~2*keep
        "tight":   {"gap": 0.35, "keep": 0.09},
    },
    "fillers": ["um", "uh", "umm", "uhh", "uhm", "er", "erm", "ah", "hmm", "mm", "mhm"],
    "min_keep_window": 0.20,
    "auto_cut_stutters": False,             # auto-remove high-confidence stutters

    # ---------------- framing
    "aspect": "9:16",                       # 9:16 | 1:1 | 4:5 | 16:9 (sets out_w/out_h)
    "out_w": 1080,
    "out_h": 1920,
    "fps": 30,
    "detect_fps": 6,                        # face-detection sampling rate per clip
    "detect_width": 640,
    "face_score": 0.6,
    "min_face_height": 0.07,                # face box height / frame height to be "dominant"
    "min_presence": 0.35,                   # share of a shot's frames a face must appear in
    "scene_threshold": 0.32,                # ffmpeg scene score for a camera cut
    "min_shot": 0.8,
    "deadband": 0.12,                       # re-center only if face drifts > this * crop width
    "drift_hold": 1.5,                      # ...for at least this many seconds
    "drift_ease": 0.6,                      # re-centring is a smooth pan of this length (s)
    "two_face_mode": "speaker",             # speaker (hard-cut to who talks) | split | wide
    "speaker_margin": 1.25,                 # activity ratio needed to switch speaker
    "speaker_min_dwell": 1.2,               # seconds before another switch is allowed
    "face_headroom": 0.42,                  # face centre sits at this height in split cells
    "cursor_track": True,                   # screen recordings: follow the mouse
    "cursor_interval": 0.5,                 # seconds between cursor samples
    "screen_zoom": 0.55,                    # share of the source width shown for screen content
    "strip_source_captions": False,         # source already has burned-in captions at the bottom
    "caption_band_frac": 0.74,              # ...keep only the top share of the frame

    # ---------------- captions
    "caption_style": "bold",
    "caption_y": 1480,                      # baseline for bottom captions (safe zone)
    "caption_words": None,                  # None = style default
    "caption_uppercase": None,              # None = style default
    "emphasis_words": ["never", "always", "nobody", "everyone", "secret", "free", "money",
                       "million", "billion", "thousand", "best", "worst", "first", "biggest",
                       "stop", "mistake", "truth", "why", "how", "huge", "insane", "crazy"],
    "hook_overlay": True,                   # big title card for the first seconds
    "hook_seconds": 2.6,
    "hook_y": 230,
    "progress_bar": False,
    "progress_color": "#FFD400",

    # ---------------- audio + encoding
    "loudnorm": True,
    "loudnorm_i": -14,
    "loudnorm_tp": -1.5,
    "loudnorm_lra": 11,
    "encoder": "auto",                      # auto | libx264 | h264_nvenc | h264_videotoolbox
    "x264_preset": "fast",
    "crf": 20,
    "audio_bitrate": "192k",

    # ---------------- brand kit (see `clipper.py brand`)
    "brand": None,                          # name of a saved brand kit in ~/.shorts-clipper/brands
    "logo": None,                           # PNG watermark path
    "logo_position": "top-right",           # top-left | top-right | bottom-left | bottom-right
    "logo_scale": 0.16,                     # logo width as a share of the frame width
    "logo_opacity": 0.9,

    # ---------------- censoring
    "censor": None,                         # None | "beep" | "mute" — profanity filter for every clip
    "censor_extra_words": [],

    # ---------------- ranking
    "judge_weight": 0.7,                    # final = judge*w + signal*(1-w)
    "ideal_wpm": 165,
}

LENGTH_PRESETS = {
    "short": (15.0, 30.0, 24.0),
    "medium": (30.0, 60.0, 42.0),
    "long": (60.0, 90.0, 72.0),
    "auto": (20.0, 60.0, 35.0),
}


ASPECTS = {"9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350), "16:9": (1920, 1080)}


def brands_dir():
    return home_dir() / "brands"


def load_brand(name: str) -> dict:
    path = brands_dir() / f"{name}.json"
    if not path.exists():
        raise SystemExit(f"brand kit '{name}' not found in {brands_dir()} (create it with `clipper.py brand save`)")
    return load_json(path)


def load(work_dir) -> dict:
    """defaults < brand kit < project settings; then derive output size from aspect."""
    cfg = copy.deepcopy(DEFAULTS)
    settings = load_json(work_dir / "settings.json", default={})
    if settings.get("brand"):
        cfg.update(load_brand(settings["brand"]).get("set", {}))
    cfg.update(settings)
    if cfg.get("aspect") not in ASPECTS:
        raise SystemExit(f"aspect must be one of {', '.join(ASPECTS)}")
    if "out_w" not in settings:
        cfg["out_w"], cfg["out_h"] = ASPECTS[cfg["aspect"]]
    return cfg


def update(work_dir, changes: dict) -> dict:
    current = load_json(work_dir / "settings.json", default={})
    current.update({k: v for k, v in changes.items() if v is not None})
    save_json(work_dir / "settings.json", current)
    return load(work_dir)


def parse_set(pairs: list[str] | None) -> dict:
    """--set key=value (value parsed as JSON when possible)."""
    out = {}
    for pair in pairs or []:
        key, sep, raw = pair.partition("=")
        if not sep or key not in DEFAULTS:
            raise SystemExit(f"--set expects key=value with a known key; got {pair!r}. "
                             f"Known keys: {', '.join(sorted(DEFAULTS))}")
        try:
            out[key] = json.loads(raw)
        except json.JSONDecodeError:
            out[key] = raw
    return out


def apply_length(cfg_changes: dict, length: str | None) -> dict:
    if length:
        if length not in LENGTH_PRESETS:
            raise SystemExit(f"--length must be one of {', '.join(LENGTH_PRESETS)}")
        lo, hi, ideal = LENGTH_PRESETS[length]
        cfg_changes.update(clip_min=lo, clip_max=hi, clip_ideal=ideal)
    return cfg_changes


def default_clip_count(cfg: dict, duration: float) -> int:
    n = round(duration / 600.0 * cfg["clips_per_10min"])
    return int(max(cfg["clips_default_min"], min(cfg["clips_default_max"], n)))
