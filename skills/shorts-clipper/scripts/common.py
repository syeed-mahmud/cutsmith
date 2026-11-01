"""Shared helpers: paths, subprocess, ffprobe, JSON IO, time formatting."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPTS_DIR.parent


# ---------------------------------------------------------------- paths
def home_dir() -> Path:
    """Per-user state dir (venv, fonts, models). Survives plugin updates."""
    return Path(os.environ.get("SHORTS_CLIPPER_HOME", Path.home() / ".shorts-clipper")).expanduser()


def fonts_dir() -> Path:
    return home_dir() / "fonts"


def models_dir() -> Path:
    return home_dir() / "models"


# ---------------------------------------------------------------- logging
def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def die(msg: str, code: int = 1) -> None:
    log(f"ERROR: {msg}")
    sys.exit(code)


# ---------------------------------------------------------------- subprocess
def run(cmd: list[str], cwd: str | Path | None = None, check: bool = True,
        capture: bool = True, timeout: float | None = None) -> subprocess.CompletedProcess:
    p = subprocess.run([str(c) for c in cmd], cwd=cwd, capture_output=capture,
                       text=True, timeout=timeout)
    if check and p.returncode != 0:
        tail = (p.stderr or "")[-2000:]
        raise RuntimeError(f"command failed ({p.returncode}): {' '.join(map(str, cmd[:6]))} ...\n{tail}")
    return p


def require_binary(name: str) -> str:
    path = shutil.which(name)
    if not path:
        die(f"'{name}' not found on PATH. Install it first (see README).")
    return path


# ---------------------------------------------------------------- media probing
def ffprobe(path: str | Path) -> dict:
    """Return display width/height (rotation applied), fps, duration, audio info."""
    p = run(["ffprobe", "-v", "error", "-print_format", "json",
             "-show_streams", "-show_format", str(path)])
    data = json.loads(p.stdout)
    v = next((s for s in data["streams"] if s.get("codec_type") == "video"
              and not s.get("disposition", {}).get("attached_pic")), None)
    a = next((s for s in data["streams"] if s.get("codec_type") == "audio"), None)
    fmt = data.get("format", {})
    meta = {
        "path": str(Path(path).resolve()),
        "duration": float(fmt.get("duration") or (v or a or {}).get("duration") or 0),
        "has_video": v is not None,
        "has_audio": a is not None,
        "size_bytes": int(fmt.get("size") or 0),
    }
    if v:
        w, h = int(v["width"]), int(v["height"])
        rot = 0
        for sd in v.get("side_data_list", []) or []:
            if "rotation" in sd:
                rot = int(float(sd["rotation"]))
        rot = int(v.get("tags", {}).get("rotate", rot) or rot)
        if abs(rot) % 180 == 90:
            w, h = h, w
        num, _, den = (v.get("avg_frame_rate") or v.get("r_frame_rate") or "30/1").partition("/")
        try:
            fps = float(num) / float(den or 1)
        except (ValueError, ZeroDivisionError):
            fps = 30.0
        if not 1 <= fps <= 240:
            fps = 30.0
        meta.update(width=w, height=h, fps=round(fps, 3), vcodec=v.get("codec_name"))
    if a:
        meta.update(sample_rate=int(a.get("sample_rate") or 48000), acodec=a.get("codec_name"),
                    channels=int(a.get("channels") or 2))
    return meta


# ---------------------------------------------------------------- json
def load_json(path: str | Path, default=None):
    path = Path(path)
    if not path.exists():
        if default is not None:
            return default
        die(f"missing file: {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str | Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


# ---------------------------------------------------------------- time
def fmt_ts(sec: float, ms: bool = True) -> str:
    sec = max(0.0, float(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    body = f"{int(m):02d}:{s:04.1f}" if ms else f"{int(m):02d}:{int(s):02d}"
    return f"{int(h)}:{body}" if h >= 1 else body


def parse_ts(text: str | float | int) -> float:
    """'75', '75.5', '1:15', '01:02:03.5' -> seconds."""
    if isinstance(text, (int, float)):
        return float(text)
    parts = str(text).strip().split(":")
    try:
        total = 0.0
        for p in parts:
            total = total * 60 + float(p)
        return total
    except ValueError:
        die(f"bad timestamp: {text!r}")
        return 0.0


def parse_range(text: str | None) -> tuple[float, float] | None:
    """'10:00-25:30' -> (600, 1530)."""
    if not text:
        return None
    m = re.match(r"^\s*([0-9:.]+)\s*-\s*([0-9:.]+)\s*$", text)
    if not m:
        die(f"bad range {text!r}; use START-END like 10:00-25:30")
    a, b = parse_ts(m.group(1)), parse_ts(m.group(2))
    if b <= a:
        die(f"range end must be after start: {text!r}")
    return a, b


def slugify(text: str, maxlen: int = 40) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return (s[:maxlen].rstrip("-")) or "clip"


def is_url(s: str) -> bool:
    return bool(re.match(r"^https?://", s.strip(), re.I))


# ---------------------------------------------------------------- project layout
class Project:
    """<out>/            final clips, thumbnails, report
       <out>/.work/      transcript, analysis, plans, intermediate files"""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.work = self.root / ".work"

    def ensure(self) -> "Project":
        self.work.mkdir(parents=True, exist_ok=True)
        return self

    def p(self, name: str) -> Path:
        return self.work / name

    @property
    def state_path(self) -> Path:
        return self.p("project.json")

    def state(self) -> dict:
        return load_json(self.state_path, default={})

    def save_state(self, st: dict) -> None:
        save_json(self.state_path, st)

    @classmethod
    def find(cls, arg: str) -> "Project":
        """Accept the project dir, its .work dir, or any file inside it."""
        p = Path(arg).expanduser().resolve()
        for cand in (p, p.parent, p.parent.parent):
            if (cand / ".work" / "project.json").exists():
                return cls(cand)
            if cand.name == ".work" and (cand / "project.json").exists():
                return cls(cand.parent)
        die(f"no shorts-clipper project at {arg} (run `prep` first)")
        return cls(p)
