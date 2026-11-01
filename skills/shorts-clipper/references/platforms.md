# Output and platforms

Every clip is exported once in a format all three major platforms accept:

| Property | Value |
|---|---|
| Resolution | 1080 × 1920 (9:16), 30 fps, square pixels |
| Video | H.264 High@4.2, CRF 20 (x264 `fast`) capped at 14 Mbit/s; NVENC / VideoToolbox used automatically when available |
| Audio | AAC 192 kbit/s, 48 kHz, loudness-normalised to −14 LUFS, true peak −1.5 dBTP |
| Container | MP4 with `+faststart` |

Platform duration limits change often — check the current rules before promising a
length. As a rule of thumb, 15–60 s clips are accepted everywhere as Shorts/Reels/TikToks.

## Safe zones (1080 × 1920)

Platform UI (buttons, captions, username) covers parts of the frame. Community
measurements, roughly:

| Zone | Keep important content out of |
|---|---|
| Top | ~0–200 px (status bar, search, tabs) |
| Bottom | ~1600–1920 px (caption, username, music) |
| Right | ~930–1080 px (like/comment/share column) |

Defaults respect this: captions at y≈1480, the hook card at y≈230 (or under the
speaker's face when it would cover it), centred text with 70 px side margins.

## Changing the encode

`--set crf=18` (bigger, sharper), `--set x264_preset=slow`, `--set encoder=libx264`
(force CPU), `--set audio_bitrate=128k`, `--set loudnorm=false`.
