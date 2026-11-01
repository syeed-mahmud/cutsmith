#!/usr/bin/env bash
# Remove the skill link and (optionally) the venv/fonts/models in ~/.shorts-clipper.
set -euo pipefail
for skill in shorts-clipper youtube-seo social-publish; do
  DEST="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}/$skill"
  [ -L "$DEST" ] && rm "$DEST" && echo "removed $DEST"
done
HOME_DIR="${SHORTS_CLIPPER_HOME:-$HOME/.shorts-clipper}"
read -r -p "Also delete $HOME_DIR (venv, fonts, face model)? [y/N] " a
[[ "$a" =~ ^[Yy]$ ]] && rm -rf "$HOME_DIR" && echo "removed $HOME_DIR"
echo "Whisper models are cached by Hugging Face in ~/.cache/huggingface (delete manually if wanted)."
