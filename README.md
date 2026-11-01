# Cutsmith — AI Auto Shorts Clipper for Claude Code

Turn long videos into ranked, captioned, ready-to-post shorts — free, local, open source.
Cutsmith is an "auto clip" workflow running entirely on your machine, with Claude as the editor.

```
"Clip ~/videos/podcast-ep12.mp4 into 6 shorts, bold captions, 30-60s"
```

Claude reads the transcript, picks the moments with real hooks and payoffs, writes hook
titles and post captions; a local engine does the heavy lifting:

| | |
|---|---|
| 🎙️ **Transcription** | faster-whisper word timestamps, 99 languages, CPU (int8) or CUDA automatically |
| 🧠 **Moment picking** | Claude judges with a 5-part rubric (hook, standalone, emotion, value, payoff) on top of a deterministic signal score (hook phrases, loudness energy, pace, dead air); you choose how many clips |
| ✂️ **Clean cuts** | edges snapped to sentence/word boundaries — never mid-word |
| 🎥 **Auto-reframe 16:9 → 9:16** | shot-aware face tracking, steady framing with eased re-centring, **active-speaker switching** by mouth motion, split-screen, blur-fill for slides/screens |
| ⚡ **Jump-cuts** | audio-driven dead-air removal with frame-exact A/V sync (`off` / `natural` / `tight`) |
| 💬 **Animated captions** | 8 styles (bold, impact, bounce, karaoke, boxed, neon, clean, minimal), active-word highlight & pop, emphasis colours, safe-zone placement |
| 🪝 **Hook title card** | AI-written stop-scroll line for the first seconds, auto-placed so it never covers the face |
| 🎮 **Stack layout** | speaker on top, gameplay/B-roll below ("split gameplay" format) |
| 🔊 **Delivery** | 1080×1920 H.264/AAC, −14 LUFS, thumbnails, titles, captions + hashtags, ranked `REPORT.md` |
| 🖥️ **Screen recordings** | auto-detected; zoomed window follows the mouse cursor |
| 🧹 **Spoken mistakes** | stutters, retakes and false starts proposed for review; cut any range inside a clip |
| 🪓 **Action montages** | no speech? cut on every impact sound (chops, hits, drums) with a visual verify sheet |
| 📦 **Platform exports** | YouTube / TikTok / Instagram re-encodes with size & codec checks |
| ✏️ **Edit ops** | `edit-clip` ops: trim / split / drop / reorder sections, delete or fix phrases, caption colour & position, text overlays, duplicate — free and local |
| 🔇 **Profanity filter** | beep or mute + masked captions |
| 🌍 **Translated captions** | Claude translates, captions re-timed to the speech |
| 🖼️ **Thumbnails** | best close-up face frame + huge headline, 16:9 and 9:16 |
| 🎨 **Brand kits** | saved style presets + logo watermark; `style-scan` to copy a reference's look |
| 📐 **Any aspect** | 9:16, 1:1, 4:5, 16:9 |
| 🗂️ **Gallery** | `preview.html` with players, scores, hooks and post copy |
| 📤 **Publish & schedule** (`social-publish` skill) | post **directly** with your own free developer apps — YouTube, Facebook Page Reels, Instagram Reels, LinkedIn, TikTok (draft to inbox), Bluesky, Threads, Reddit, Telegram, Discord, Mastodon, Pinterest (X optional, pay-per-use). You approve each post in chat or in a local **calendar** (month view, drag to reschedule, click to edit/approve); posting **time slots**; native scheduling on YouTube/Facebook, local cron for the rest; resumable; tracks public URLs |
| 📊 **Analytics** | views/likes/comments/shares per post, platform totals, best clips across platforms, history over time → `analytics.html` |
| 🤖 **Autopilot** | opt-in standing rules ("post every new clip scoring 70+ to YouTube + TikTok at weekday 18:00, max 1/day") — scheduled, approved by the rule, published and tracked automatically; pause/remove anytime |
| 🔎 **YouTube SEO** (`youtube-seo` skill) | keyword research from YouTube autocomplete + live results: demand, competition, opportunity, competitor tags; 0–100 title scores; full description/tag/hashtag/chapter checklist |
| 🛠️ **Single-video tools** | add captions to any video, convert it to vertical, or just cut the silences |

No cloud APIs, no accounts, no watermark. Works on Linux and macOS (Windows via WSL).

## Install

Requirements: [Claude Code](https://claude.com/claude-code), `ffmpeg` (with libass — the
normal distro/Homebrew build), Python ≥ 3.10, ~2 GB disk (Python deps + Whisper model).

**As a plugin (recommended)** — in Claude Code:
```
/plugin marketplace add syeed-mahmud/cutsmith
/plugin install cutsmith@cutsmith
```
The first time you use it, Claude runs `setup.sh` (creates `~/.shorts-clipper/venv`,
downloads OFL fonts and the face model).

**Manually:**
```bash
git clone https://github.com/syeed-mahmud/cutsmith.git
cd cutsmith && ./install.sh        # symlinks into ~/.claude/skills and runs setup
```

Then restart Claude Code and just ask:
- *"Make 5 shorts from ~/Downloads/interview.mp4"*
- *"Clip this podcast into 8 short (15-30s) clips about pricing, impact captions"*
- *"Add karaoke captions to talk.mp4"* · *"Make demo.mp4 vertical with face tracking"*

## How it works

```
video/URL ─▶ prep ──▶ transcript.md + candidates.md ──▶ Claude picks N moments ──▶ picks.json
                │                                                                     │
                └─ faster-whisper words · adaptive pauses · loudness energy           ▼
                                                                                    render
   snap to sentences ─▶ shot-aware face plan ─▶ jump-cut plan ─▶ ASS captions ─▶ one ffmpeg pass
                                                                                      │
                     NN_title.mp4 · NN_title.jpg · clips.json · REPORT.md ◀───────────┘
                                         │
                       Claude reviews contact sheets, fixes, re-renders (--only N)
```

The engine is a plain CLI, so you can also use it without Claude:

```bash
PY=~/.shorts-clipper/venv/bin/python
CLI=skills/shorts-clipper/scripts/clipper.py
$PY $CLI prep  talk.mp4 --clips 5 --lang en     # -> talk-shorts/.work/{transcript,candidates}.md
# write talk-shorts/.work/picks.json (see SKILL.md for the format)
$PY $CLI render talk-shorts --style bold
$PY $CLI auto  talk.mp4 --clips 5               # fully automatic (signal score only)
$PY $CLI edit  talk.mp4 --vertical --captions karaoke --pace natural
$PY $CLI export talk-shorts --platform all       # per-platform encodes
$PY $CLI action chopping.mp4 --render --vertical # montage cut on every impact
$PY $CLI edit-clip talk-shorts --clip 2 --op 'delete_phrase:phrase=you know' --op censor:mode=beep
$PY $CLI thumbnail talk-shorts --aspect both && $PY $CLI preview talk-shorts --open
python3 skills/youtube-seo/scripts/ytseo.py keywords "home workout" --top 15   # keyword research
python3 skills/youtube-seo/scripts/ytseo.py title "Title A" "Title B" -k "home workout"
$PY $CLI doctor
```

## Performance (16-core laptop CPU, no GPU)

| Step | 10-minute 1080p interview |
|---|---|
| Transcribe (`small`, int8) | ~110 s |
| Render one 40 s clip (framing + captions + encode) | ~20–30 s |

## Layout

```
.claude-plugin/          plugin + marketplace manifests
skills/shorts-clipper/
  SKILL.md               the workflow Claude follows
  setup.sh               venv, fonts, face model
  references/            scoring rubric, caption styles, layouts, platforms, troubleshooting
  scripts/               clipper.py CLI + modules (transcribe, analyze, scoring, snap,
                         faces, framing, jumpcut, captions, render, config)
  scripts/lib/           helper modules (impact detection, spoken-mistake finder, cursor tracking)
skills/youtube-seo/      keyword research + metadata scoring (ytseo.py, stdlib only)
skills/social-publish/   direct posting: publish.py + providers/ (one module per platform), calendar, analytics, autopilot — stdlib only
THIRD_PARTY_NOTICES.md   licence notices
install.sh / uninstall.sh
```

All tunables live in `skills/shorts-clipper/scripts/config.py` and can be overridden per
project with `--set key=value`.

## Responsible use

Only clip videos you own or have permission to reuse. Downloading from platforms may
violate their terms of service.

## Built with

[faster-whisper](https://github.com/SYSTRAN/faster-whisper),
[OpenCV YuNet](https://github.com/opencv/opencv_zoo), [FFmpeg](https://ffmpeg.org)/libass,
fonts Poppins, Anton and Bangers (SIL OFL).

## License

MIT — see [LICENSE](LICENSE).
