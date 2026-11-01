#!/usr/bin/env bash
# Manual install (without the plugin marketplace): link the skill into
# ~/.claude/skills and run its one-time setup.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_DIR="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"
mkdir -p "$SKILLS_DIR"
for skill in shorts-clipper youtube-seo social-publish; do
  DEST="$SKILLS_DIR/$skill"
  if [ -e "$DEST" ] && [ ! -L "$DEST" ]; then
    echo "$DEST exists and is not a symlink; remove it first." >&2; exit 1
  fi
  ln -sfn "$REPO/skills/$skill" "$DEST"
  echo "linked $DEST -> $REPO/skills/$skill"
done
bash "$REPO/skills/shorts-clipper/setup.sh"
echo "Restart Claude Code, then ask: \"clip /path/to/video.mp4 into 5 shorts\""
