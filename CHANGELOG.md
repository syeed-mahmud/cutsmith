# Cutsmith changelog

## 1.0.0 — 2026-11-01

First public release of Cutsmith: three Claude Code skills in one plugin.

### shorts-clipper
- **Prepare:** word-level transcription (faster-whisper, CPU int8 or CUDA), adaptive pause
  detection, loudness energy, compact transcript and signal-ranked candidate windows,
  spoken-mistake finder (stutters, retakes, false starts). `--prompt`, `--genre`,
  `--keywords`, `--range`, length presets, any language.
- **Pick:** Claude scores moments with a five-part rubric; final rank blends judgement with
  a deterministic signal score; optional hook/coherence/connection/trend sub-scores.
- **Render:** sentence/word boundary snapping; shot-aware face framing with active-speaker
  switching and eased re-centring; `crop`, `split`, `fit`, `screen` (cursor tracking),
  `stack` (b-roll) layouts; aspect ratios 9:16, 1:1, 4:5, 16:9; audio-driven jump-cuts with
  frame-exact A/V sync; 8 animated caption styles; face-aware hook card; text overlays;
  progress bar; −14 LUFS loudness; burned-in caption removal; brand kits with logo
  watermark; profanity beep/mute; translated captions; multi-section clips.
- **Edit:** `edit-clip` operations — trim, split, drop and reorder sections, delete or
  fix phrases, caption colour/position/case, overlays, hook, censor, duplicate, dry run.
- **Deliver:** thumbnails and designed thumbnails, contact sheets, validation, ranked
  `REPORT.md`, HTML gallery, per-platform exports.
- **More:** whole-video `edit` (captions / reframe / jump-cut), unattended `auto`,
  impact-sound `action` montages, reference `style-scan`.

### youtube-seo
- Keyword expansion from YouTube autocomplete; demand, competition and opportunity scores
  from live results (YouTube Data API or yt-dlp); competitor tags and title patterns;
  0–100 title scores; full title/description/tag/hashtag/chapter checklist; trending.

### social-publish
- Direct posting with the user's own free developer apps to YouTube, Facebook Pages,
  Instagram, LinkedIn, TikTok, Bluesky, Threads, Reddit, Telegram, Discord, Mastodon and
  Pinterest (X optional, pay-per-use).
- Publish plans from rendered clips, per-post approval in chat, dry runs, resumable runs,
  native scheduling on YouTube/Facebook, recurring posting slots.
- Local calendar (month view, drag to reschedule, edit, approve/reject).
- Analytics per post and per platform with history and an HTML report.
- Autopilot: opt-in standing rules that schedule and publish new clips; one cron tick runs
  autopilot, due posts and analytics refreshes; full audit log.
