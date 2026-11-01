---
name: shorts-clipper
description: Turn a long video (podcast, interview, talk, stream, webinar, lecture; local file or URL) into N ranked, ready-to-post vertical 9:16 shorts — you pick the most viral, self-contained moments from the transcript, then a local engine snaps cuts to sentences, auto-reframes (face tracking, active-speaker switching, split-screen, blur-fill, gameplay stack), removes dead air, burns animated word-by-word captions, adds a hook title, normalises loudness, and exports thumbnails + titles + post captions. It also captions, reframes or jump-cuts any single video; edits rendered clips (trim, split, reorder, delete a phrase, caption colour/position, text overlays), bleeps profanity, translates captions, designs thumbnails, applies brand kits/logos, outputs 9:16, 1:1, 4:5 or 16:9, builds an HTML gallery, and copies a reference video's style. Use when the user says "clip this", "make shorts/reels/tiktoks from this video", "find the best moments", "auto clip", "add captions/subtitles to this video", "make this vertical/9:16", "cut the silences", "edit/trim this clip", "bleep the swearing", "translate the captions", "make a thumbnail", or gives a long video and wants short clips.
---

# Shorts Clipper

Long video → N scroll-stopping vertical clips. **You are the editor**: the engine
does the mechanical work (transcribe, frame, cut, caption, render); you do the
judgement (which moments, hook text, titles, QA). Run everything locally; nothing is
uploaded anywhere.

`<skill-dir>` below = the directory containing this file (given to you as the skill's
base directory). Use absolute paths and quote them — paths often contain spaces.

## 0. Setup (once per machine)

```bash
SC_PY="${SHORTS_CLIPPER_HOME:-$HOME/.shorts-clipper}/venv/bin/python"
[ -x "$SC_PY" ] || bash "<skill-dir>/setup.sh"      # venv + fonts + face model (~1-3 min)
"$SC_PY" "<skill-dir>/scripts/clipper.py" doctor     # must print "ready": true
```
Define once and reuse: `SC="$SC_PY <skill-dir>/scripts/clipper.py"` (quote the path).
Needs `ffmpeg` with libass. If `doctor` fails, read `references/troubleshooting.md`.
The first transcription downloads the Whisper model (~460 MB for `small`).

## 1. Brief — get only what's missing

Defaults are good; ask **one** AskUserQuestion round only for what the user didn't say
and that changes the result. Skip asking entirely if they said "just do it".

| Setting | Flag | Default |
|---|---|---|
| Number of clips | `prep --clips N` | ~4 per 10 min (3–15) |
| Length | `prep --length short\|medium\|long\|auto` | auto = 20–60 s (short 15–30, medium 30–60, long 60–90) |
| Caption style | `render --style` | `bold` — see `references/caption-styles.md` |
| Two people on screen | `render --two-face speaker\|split\|wide` | `speaker` (cut to whoever talks) |
| Dead-air removal | `render --pace off\|natural\|tight` | `natural` |
| Focus topics | `prep --keywords "pricing,AI"` | none |
| Part of the video | `prep --range 10:00-45:00` | whole video |
| Language | `prep --lang en` | auto-detect |
| Aspect | `prep/render --aspect 9:16\|1:1\|4:5\|16:9` | 9:16 |
| What to look for | `prep --prompt "funny moments" --genre comedy` | none |
| Profanity | `render --censor beep\|mute` | off |
| Brand kit | `render --brand NAME` (see references/editing.md) | none |

Copyright: only clip content the user owns or has permission to use. Say this once,
plainly, when the source is a URL of someone else's content. Don't block on it.

## 2. Prepare

```bash
$SC prep "<video-or-url>" --clips N [--length ...] [--lang xx] [--keywords ...] [--range ...]
```
Prints JSON: `project` dir, duration, `language`, `language_confidence`, `clips_requested`,
and the files to read. Transcripts are cached; re-running is cheap.
- `language_confidence` < 0.75 or the transcript looks like the wrong language →
  re-run with `--lang <code> --force`.
- Speed (CPU): ~5–6× real-time with `small`. For >1 h sources say it will take a while,
  or offer `--model base` (faster, less accurate) / `--range`.

## 3. Choose the moments (the part that matters)

Read, in this order:
0. If `<project-or-sibling>/analytics.json` exists (from `social-publish analytics`), check
   `best_clips` — what actually performed for this audience beats the generic rubric.
1. `references/scoring-rubric.md` (once per session)
2. `<project>/.work/candidates.md` — signal-ranked windows; a prior, not the answer
3. `<project>/.work/transcript.md` — one sentence per line `[mm:ss.s] text`, ⚡ = louder
   than usual, ⏸ = long pause. For long transcripts read it in chunks; read all of it.

Pick **exactly N** clips (the user's number). You may use candidate windows, adjust
them, or choose moments the prior missed — the transcript is the truth. Rules:
- **Hook in the first 3 s.** Start on the line that creates the question, claim, or
  tension — not on throat-clearing ("so", "yeah", "and then"). Cut setup that the
  hook makes unnecessary.
- **Standalone.** A stranger must follow it with zero context. No "as I said", no
  unresolved "he/it/that" at the start.
- **Payoff.** End right after the punchline / answer / reveal, not mid-thought and not
  trailing into the next topic.
- Within the length range; no overlaps; spread across topics (don't take 3 clips of the
  same point); prefer ⚡ passages when content is equal.
- Interview tip: a strong interviewer question + the answer often makes the best clip.

Write `<project>/.work/picks.json` (times as seconds or `mm:ss.s`; the engine snaps
them to sentence/word boundaries, so roughly right is fine):
```json
[
  {"start": "04:26.0", "end": "05:10.6",
   "title": "Why we keep building in danger zones",
   "hook": "We love living in danger",
   "judge": 80,
   "reason": "relatable contradiction + concrete 'do not build here' payoff",
   "caption": "Living by the river is pretty... until it isn't.",
   "hashtags": ["#flooding", "#urbanplanning", "#climate"]}
]
```
- `title`: 3–8 words, used for the filename and report.
- `hook`: ≤ 7 words, burned as a title card for the first ~2.6 s. Must add curiosity, not
  repeat the first spoken words. Omit → no card. (`--no-hook` disables all cards.)
- `judge`: your honest 0–100 virality score per the rubric. Final rank =
  0.7 × judge + 0.3 × signal. Don't inflate — it's how clips get ordered. Optionally also
  `"scores": {"hook", "coherence", "connection", "trend"}` (0–100 each) for the gallery.
- `caption` + 3–6 `hashtags`: ready-to-paste post text in the speaker's language.
  Optional `"posts": {"youtube": {"title","description","tags"}, "tiktok", "instagram", "linkedin", "x"}`
  when the user wants per-platform copy (for YouTube, use the `youtube-seo` skill to research
  the keyword and score title/description/tags), and `"thumbnail_text"` (≤ 5 words) for a designed thumbnail.
- Optional `"sections": [[s, e], ...]` instead of start/end to stitch non-adjacent moments
  in any order — e.g. the punchline first as a cold open, then the setup.
- Optional per clip: `"layout": "crop|split|fit|screen|stack|center"`, `"style": "<caption style>"`,
  `"remove": [["04:35.1", "04:36.5"], ...]` — source-time ranges cut out of the middle of the
  clip (a tangent, a retake, a stutter). Captions re-time automatically.

**Spoken mistakes.** `prep` also writes `.work/mistakes.md`: stutters, retakes and false
starts found mechanically. Review the ones inside your picks in context — emphasis like "very, very" or "never, never" is NOT a mistake — and add the
real ones to that pick's `remove`. (`--set auto_cut_stutters=true` cuts every
high-confidence stutter without review; only use it when the user asks.)

## 4. Confirm (unless the user wants it fully automatic)

Show a compact table — `# · time · length · judge · title · hook · why` — and ask
"render these?" in one line. Adjust picks on feedback; don't re-transcribe.

## 5. Render

```bash
$SC render "<project>" [--style bold] [--two-face speaker] [--pace natural] \
    [--progress-bar] [--broll gameplay.mp4]
```
One ffmpeg pass per clip (~20–30 s each on a modern CPU). Writes to `<project>/`:
`NN_<title>.mp4` (1080×1920, H.264/AAC, −14 LUFS), `NN_<title>.jpg` thumbnail,
`clips.json` (all metadata), `REPORT.md` (ranked, with post captions). Flags given to
`render` are remembered in the project; per-clip fields in picks.json win over them.
Re-render specific clips with `--only 2,5` (framing is cached).

## 6. QA — look before you deliver

Read every contact sheet listed in the render output (`.work/sheet_NN.jpg`, 8 frames).
Check: the speaker's face is in frame and not cut off · captions and hook don't cover
eyes/mouth · no black or frozen frames · the right person is on screen while talking ·
`issues` in the output is empty. Fix, then `render --only N`:
- face cut off / wrong person → set `"layout"` on that pick (`split` for two people far
  apart, `screen` for screen-shares/slides (follows the cursor), `fit` for wide shots,
  `center` as last resort)
- source already has burned-in subtitles (double captions) → `--set strip_source_captions=true`
- captions clash with on-screen text → different `style`, or `--set caption_y=1350`
- weak opening → move `start` to the hook line; too long → tighten `end`
Don't claim a clip is good without looking at its sheet.

## 7. Edits after review

When the user asks for changes ("cut the bit where she says X", "start at 0:05",
"make captions green and at the top", "add 'Follow for part 2' at the end", "bleep the
swearing", "put the payoff first"), use `edit-clip` ops — see `references/editing.md`:
```bash
$SC edit-clip "<project>" --clip 2 --op 'delete_phrase:phrase=you know what I mean' \
    --op 'set_style:highlightColor=#39FF14,captionPosition=top' --op censor:mode=beep
```
Run `--dry-run` first when an op's effect is uncertain. Re-check the clip's contact sheet.

## 8. Deliver

Reply with the leaderboard (rank, score, length, title, file name), the project folder,
and point to `REPORT.md` for captions/hashtags. Mention anything you couldn't fix.
Offer `$SC preview "<project>" --open` (HTML gallery of the ranked clips).
For YouTube-style thumbnails: `$SC thumbnail "<project>" --aspect both` (headline from
`thumbnail_text` → hook → title; or `--text`).
For captions in another language: `$SC translate "<project>" --clip N --lang xx`, fill
`text_translated`, set the pick's `"translation"`, re-render (details in references/editing.md).
To post or schedule the clips, use the `social-publish` skill (direct, free, approval-gated).
If the user wants platform-specific files: `$SC export "<project>" --platform all|youtube|tiktok|instagram`
(writes `<project>/export/*_yt|_tt|_ig.mp4` with each platform's bitrate/size limits checked).

## Other jobs

- **Unattended batch:** `$SC auto "<video>" --clips N` (picks by signal score only,
  no hook cards — lower quality than steps 3–6; use only when asked for no review).
- **Re-rank** after changing length/topics: `$SC candidates "<project>" --length short --keywords "x,y"`.
- **One whole video** (not clipping):
  `$SC edit "<video>" [--vertical] [--captions bold|none|...] [--pace off|natural|tight] [--lang en]`
  — e.g. add captions to a finished video, convert 16:9 → 9:16 with face tracking,
  or just remove silences. Output next to the input (`*.edited.mp4` / `*.vertical.mp4`).
- **Action montage** (no speech: woodchopping, sports, drumming, boxing):
  `$SC action "<video>"` detects every impact sound and writes `strikes_sheet.jpg` (one
  numbered frame per strike). **Read the sheet** and note strikes where the action isn't
  visible (off-frame, thumb on lens, wrong sound). Then
  `$SC action "<video>" --render --drop 3,7 [--vertical]` → `*.montage.mp4`, ~0.9 s per hit
  (`--pre 0.4 --post 0.5`). Tune detection with `--band 200:2000` (thuds/kicks),
  `--band 4000:10000` (clinks/snares), `--min-flux 20|40`, `--min-gap 0.2` (fast drums).
- **Copy a reference video's style:** `$SC style-scan <reference.mp4>`, Read the two images it
  writes, then map the look onto `--style`/`caption_look`/`--pace`/`progress_bar`/aspect
  (references/editing.md). Save it as a brand kit if the user will reuse it.
- **Visual check of any output:** `$SC sheet "<clip.mp4>"`, then Read the jpg.
- **Brainrot / split-gameplay format:** `render --broll <gameplay.mp4>` puts the speaker
  on top and the b-roll (muted, looped, random offset) on the bottom, captions on the seam.

Every tunable (thresholds, colours, fonts, caption position, loudness, encoder) lives in
`scripts/config.py`; override per project with `--set key=value` (JSON values).
Layout logic: `references/layouts.md`. Platform specs & safe zones: `references/platforms.md`.
