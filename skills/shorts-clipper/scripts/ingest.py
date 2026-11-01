"""Get the source onto disk (local path or URL) and extract analysis audio."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from common import Project, die, ffprobe, is_url, log, run


def download(url: str, proj: Project) -> Path:
    """Download with yt-dlp (<=1080p, mp4). Only use on content you have rights to."""
    out_tmpl = str(proj.root / "source.%(ext)s")
    cmd = [sys.executable, "-m", "yt_dlp", "--no-playlist", "--restrict-filenames",
           "-f", "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b",
           "--merge-output-format", "mp4", "-o", out_tmpl,
           "--print", "after_move:filepath", url]
    log(f"[ingest] downloading {url}")
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        die(f"yt-dlp failed:\n{p.stderr[-1500:]}")
    lines = [l for l in p.stdout.strip().splitlines() if l.strip()]
    path = Path(lines[-1]) if lines else None
    if not path or not path.exists():
        cands = sorted(proj.root.glob("source.*"))
        if not cands:
            die("yt-dlp finished but no file was found")
        path = cands[0]
    return path


def resolve_source(source: str, proj: Project) -> Path:
    if is_url(source):
        existing = [p for p in proj.root.glob("source.*") if p.suffix != ".part"]
        return existing[0] if existing else download(source, proj)
    path = Path(source).expanduser().resolve()
    if not path.exists():
        die(f"source not found: {path}")
    return path


def extract_audio(src: Path, out_wav: Path, rng: tuple[float, float] | None) -> None:
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    if rng:
        cmd += ["-ss", f"{rng[0]:.3f}", "-t", f"{rng[1] - rng[0]:.3f}"]
    cmd += ["-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out_wav)]
    run(cmd)


def probe_source(src: Path) -> dict:
    meta = ffprobe(src)
    if not meta["has_audio"]:
        die("source has no audio track; the clipper needs speech to find moments")
    if not meta["has_video"]:
        log("[ingest] audio-only source: clips get a dark canvas with an animated waveform")
    return meta
