#!/usr/bin/env bash
# One-time setup for the shorts-clipper skill.
# Creates a private venv + downloads open-licence fonts and the face model into
# $SHORTS_CLIPPER_HOME (default ~/.shorts-clipper) so plugin updates never wipe them.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOME_DIR="${SHORTS_CLIPPER_HOME:-$HOME/.shorts-clipper}"
VENV="$HOME_DIR/venv"
mkdir -p "$HOME_DIR/fonts" "$HOME_DIR/models"

say() { printf '\033[1;36m[shorts-clipper]\033[0m %s\n' "$*"; }

command -v ffmpeg >/dev/null || {
  echo "ffmpeg is required. Install it first:"
  echo "  Ubuntu/Debian: sudo apt install ffmpeg"
  echo "  macOS:         brew install ffmpeg"
  echo "  Windows:       use WSL, or winget install Gyan.FFmpeg"
  exit 1
}

PY="${PYTHON:-python3}"
if [ ! -x "$VENV/bin/python" ]; then
  say "creating venv at $VENV"
  if command -v uv >/dev/null; then uv venv -q "$VENV"; else "$PY" -m venv "$VENV"; fi
fi
say "installing python packages (faster-whisper, opencv-headless, numpy, yt-dlp)"
if command -v uv >/dev/null; then
  uv pip install -q --python "$VENV/bin/python" -r "$SKILL_DIR/requirements.txt"
else
  "$VENV/bin/python" -m pip install -q --upgrade pip
  "$VENV/bin/python" -m pip install -q -r "$SKILL_DIR/requirements.txt"
fi

fetch() { # url dest
  [ -s "$2" ] && return 0
  curl -fsSL --retry 3 -o "$2.part" "$1" && mv "$2.part" "$2" || { rm -f "$2.part"; echo "  (skipped $(basename "$2"))"; }
}
say "downloading fonts (SIL Open Font License, from github.com/google/fonts)"
GF="https://github.com/google/fonts/raw/main/ofl"
for f in Poppins-ExtraBold Poppins-Bold Poppins-SemiBold Poppins-Medium; do
  fetch "$GF/poppins/$f.ttf" "$HOME_DIR/fonts/$f.ttf"
done
fetch "$GF/anton/Anton-Regular.ttf" "$HOME_DIR/fonts/Anton-Regular.ttf"
fetch "$GF/bangers/Bangers-Regular.ttf" "$HOME_DIR/fonts/Bangers-Regular.ttf"

say "downloading YuNet face detector (Apache-2.0, opencv_zoo)"
fetch "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx" \
      "$HOME_DIR/models/face_detection_yunet_2023mar.onnx"

say "checking environment"
"$VENV/bin/python" "$SKILL_DIR/scripts/clipper.py" doctor
say "done. Run clips with:  $VENV/bin/python $SKILL_DIR/scripts/clipper.py --help"
