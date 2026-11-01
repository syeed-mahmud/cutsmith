# Troubleshooting

| Symptom | Fix |
|---|---|
| `doctor`: `ffmpeg_filter_subtitles: false` | ffmpeg was built without libass. Install a full build (`apt install ffmpeg`, `brew install ffmpeg`, or a static build from ffmpeg.org). |
| `faster-whisper is not installed` | Run `bash <skill-dir>/setup.sh`, and call scripts with `~/.shorts-clipper/venv/bin/python`. |
| Transcript is in the wrong language / gibberish | `prep ... --lang en --force` (any ISO code). Auto-detect is unreliable on short or accented audio; `language_confidence` < 0.75 is a warning sign. |
| Transcription is slow | CPU runs ~5× real-time with `small`. Use `--model base` (≈2× faster), `--range` to process part of the video, or a CUDA GPU (used automatically, model `large-v3-turbo`). |
| `yt-dlp failed` | Update it: `~/.shorts-clipper/venv/bin/python -m pip install -U yt-dlp`. Some sites need cookies (`--cookies-from-browser`) — download manually and pass the file. |
| Face cut off / wrong person framed | Set `"layout"` on that pick (`split`, `fit`, `center`) and `render --only N`. Lower `--set min_face_height=0.04` for wide shots. |
| Speaker switching too twitchy / too slow | `--set speaker_margin=1.5` (harder to switch) or `1.1`; `--set speaker_min_dwell=2.0`. |
| Captions overlap burned-in text from the source | `--set caption_y=1300` or `--layout fit`. |
| Captions in a plain font | Fonts missing: re-run `setup.sh` (needs internet). `doctor` lists installed fonts. |
| Jump-cuts feel choppy | `--pace natural` (default) or `off`. `tight` trims every pause ≥ 0.35 s. |
| Audio pops at cuts | Use `--pace off` for music-heavy sources. |
| Clip starts/ends a bit off | Adjust `start`/`end` in picks.json; snapping moves edges ≤ 3.5 s to the nearest sentence/word. |
| `path not usable inside an ffmpeg filter` | The fonts dir path contains `'` or `:` — set `SHORTS_CLIPPER_HOME` to a simple path and re-run setup. |
| Out of disk | Everything temporary lives in `<project>/.work/`; delete it after delivery (keep `picks.json` if you may re-render). |

Debugging a render: the exact ffmpeg command for clip N is saved in
`<project>/.work/clip_NN.cmd.txt` and its subtitles in `clip_NN.ass`.
