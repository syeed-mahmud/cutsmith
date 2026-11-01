# Caption styles

Word-by-word captions are generated as ASS subtitles and burned in by ffmpeg/libass.
Choose with `render --style <name>` (all clips) or `"style"` on a pick (one clip).

| Style | Look | Best for |
|---|---|---|
| `bold` (default) | Poppins ExtraBold, ALL CAPS, 3 words, active word yellow + slight pop, numbers/power words cyan | business, education, podcasts |
| `impact` | Anton (condensed), huge, 1–2 words, yellow active word | hot takes, motivation, fast talkers |
| `bounce` | Bangers cartoon font, 2 words, each chunk a new bright colour, bouncy entrance | entertainment, gaming, reactions, "brainrot" stack layout |
| `karaoke` | Poppins ExtraBold natural case, 4 words, green active word | storytelling, longer sentences |
| `boxed` | Poppins Bold on a dark translucent box, yellow active word | busy/bright footage where outlines aren't enough |
| `neon` | white caps, cyan glowing active word | tech, night/dark footage |
| `clean` | Poppins SemiBold, soft shadow, 6 words, subtle highlight | interviews, professional, calm content |
| `minimal` | small, 7 words, no highlight | when the visuals are the point |

Details shared by all styles:
- Lines break at sentence ends, pauses > 0.6 s, the style's word/character limit, or
  2.4 s — so a caption never lingers over silence.
- Position: lower third (`caption_y`, default 1480 of 1920 — clear of TikTok/Reels/Shorts
  UI). In `split` and `stack` layouts captions sit on the seam between the two halves.
- Captions follow the jump-cut timeline, so they stay in sync after silences are removed.
- Fonts are OFL fonts downloaded by `setup.sh` to `~/.shorts-clipper/fonts`. Missing
  fonts fall back to a system font (still works, looks plainer).

Overrides (per project, `--set key=value`):
- `caption_words=2` — words per caption chunk
- `caption_uppercase=false`
- `caption_y=1350` — move captions up (e.g. to clear burned-in text in the source)
- `emphasis_words=["free","now"]` — words that get the emphasis colour
- `hook_overlay=false`, `hook_seconds=3.0`, `hook_y=230`
- `progress_bar=true`, `progress_color="#FF3B5C"`

To add a style, add an entry to `STYLES` in `scripts/captions.py` (font family, size for
a 1080×1920 canvas, colours, words, chars, pop/enter animation).
