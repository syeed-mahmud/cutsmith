# Editing rendered clips

Every clip is described by its entry in `<project>/.work/picks.json`. Rendering is
deterministic from that entry, so editing a clip = changing its pick and re-rendering
it (`render --only N`). `edit-clip` does both for you with named operations, executed locally and free.

```bash
$SC edit-clip "<project>" --clip 2                       # list what can be edited (sections, overlays, style…)
$SC edit-clip "<project>" --clip 2 --op OP[:k=v,k=v] [--op …] [--dry-run]
```
Ops apply in order; one re-render at the end. `--dry-run` shows the resulting pick
without saving or rendering. The previous picks.json is kept as `picks.json.bak`.
**Times are seconds in the rendered clip** (what the user sees in the player); the clip's
timemap converts them to source time, so this stays correct after jump-cuts and reorders.
Quote values that contain commas: `--op 'delete_phrase:phrase="um, you know"'`.

| Op | Parameters | Effect |
|---|---|---|
| `trim` | `fromSec`, `toSec` | keep only this part of the clip |
| `trim_section` | `sectionIndex`, `keepFromSec`, `keepToSec` | shorten one section (seconds from that section's start) |
| `split_section` | `atClipSec` | split the section playing at that moment in two |
| `drop_section` | `sectionIndex` | remove a section |
| `reorder_sections` | `order=1,0,2` | reorder sections (full permutation) — e.g. move the payoff first as a cold-open hook |
| `delete_phrase` | `phrase`, `occurrence` (1-based, default all) | cut spoken words from video **and** captions |
| `replace_phrase` | `phrase`, `replacement`, `occurrence` | fix caption text only (typo, name spelling); timing untouched |
| `set_style` | `captionColor`, `highlightColor` (#RRGGBB), `captionPosition` (top/middle/bottom), `uppercase`, `size` (×, e.g. 1.2), `style` (preset name) | caption look for this clip |
| `add_text_overlay` | `text`, `atClipSec`, `durationSec` (5), `position` (top/middle/bottom) | title card / CTA / lower third over the video |
| `set_text_overlay` | `overlayIndex`, `text`, `position`, `atClipSec`, `durationSec` | edit one overlay |
| `remove_text_overlay` | `overlayIndex` | remove one (the hook card is overlay 0) |
| `set_hook` | `text` | replace the hook card text |
| `censor` | `mode=beep\|mute\|off` | profanity filter: beep/mute the audio, mask captions (`f***`) |
| `set_layout` | `layout` | crop / split / fit / screen / stack / center |
| `set_title` | `title` | rename (also the file name) |
| `duplicate` | – | copy the clip to a new number (then edit the copy) |

## Pick fields (picks.json)

```jsonc
{
  "start": "04:26.0", "end": "05:10.6",            // or "sections": [["04:48","05:10"], ["04:26","04:48"]]
  "title": "...", "hook": "...", "judge": 80,
  "scores": {"hook": 85, "coherence": 80, "connection": 70, "trend": 60},   // optional breakdown
  "reason": "...", "caption": "...", "hashtags": ["#a"],
  "posts": {"youtube": {"title": "...", "description": "..."}, "tiktok": "...", "instagram": "...",
            "linkedin": "...", "x": "..."},          // optional per-platform copy
  "thumbnail_text": "40°C. THOUSANDS DIED.",       // optional: designed thumbnail instead of a plain frame
  "layout": "crop", "style": "bold",
  "remove": [["04:35.1", "04:36.5"]],              // source ranges cut out of the middle
  "overlays": [{"text": "...", "at": 0, "duration": 2.6, "position": "auto"}],
  "caption_look": {"color": "#FFFFFF", "highlight": "#39FF14", "position": "middle", "uppercase": true},
  "caption_fixes": [{"phrase": "prooduct", "replacement": "product"}],
  "censor": "beep",
  "translation": "translate_02_es.json"            // translated captions, see below
}
```
- `sections` (any order) makes a multi-section clip: each section is framed and
  jump-cut separately, then joined and captioned as one clip.
- `judge` missing but `scores` present → judge = 0.35·hook + 0.25·coherence +
  0.2·connection + 0.2·trend.

## Translated captions

1. `$SC translate "<project>" --clip N --lang es` → `.work/translate_NN_es.json` with the
   clip's sentences (source-timed).
2. Fill every `text_translated` (natural, concise, same meaning; keep names/numbers).
3. Set `"translation": "translate_NN_es.json"` on that pick, `render --only N`.
Captions show the translation spread across each sentence's timing (karaoke highlight
follows it approximately). Audio stays original — dubbing is not supported.

## Thumbnails

`$SC thumbnail "<project>" [--clip N] [--text "..."] [--aspect 16:9|9:16|both]` picks the
sharpest close-up face frame from the source (no burned captions), crops it with the
face off-centre, boosts colour and sets a huge Anton headline on the empty side. Keep the
text ≤ 5 words, different from the title, readable at phone size. `--at <source sec>`
forces a frame. Setting `thumbnail_text` on a pick makes `render` produce it automatically.

## Brand kits

```bash
$SC brand save mybrand --logo ~/logo.png --set caption_style=karaoke \
    --set progress_bar=true --set 'progress_color="#E11D48"' --set logo_position=top-right
$SC render "<project>" --brand mybrand
$SC brand list | show mybrand | delete mybrand
```
A kit is any set of config overrides (fonts via style, colours, caption position,
aspect, pace, loudness…) plus an optional PNG logo watermark (`logo_scale`,
`logo_opacity`, `logo_position`). Kits live in `~/.shorts-clipper/brands/`.

## Copying a reference video's style

`$SC style-scan <reference.mp4>` measures cuts/min, shot length, loudness and aspect,
and writes `sheet.jpg` (16 frames) + `captions_band.jpg` (lower half of 12 frames).
Read both images, then map what you see onto settings: caption style/colours/position
(`--style`, `caption_look`, `caption_y`), pace (`--pace tight` for fast cutters),
`progress_bar`, hook cards, aspect, layout. Borrow the treatment, never the reference's
footage, music or graphics.

## Gallery

`$SC preview "<project>" [--open]` writes `preview.html`: clips ranked by score with
players, hooks, reasons, score bars, post copy and download links.
It first copies text-only pick fields (`posts`, `caption`, `hashtags`, `reason`, `judge`,
`scores`) from picks.json into clips.json and rewrites REPORT.md, so copy added after
`render` shows up without re-rendering. Title, hook and timing changes still need `render`.
