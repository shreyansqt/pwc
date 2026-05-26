#!/bin/zsh
# Install PWC. Two parts:
#   1. Skills go GLOBAL (~/.claude/skills/) so every Claude Code session sees them —
#      coordinator and spawned workers alike (workers run in a repo, not the
#      workspace root, so workspace-local skills wouldn't resolve for them).
#   2. The task database is per-workspace at <workspace>/.pwc/taskdb.db.
#
# Usage:
#   ./install.sh [workspace-dir]      # default: ~/work/acme
#
# Re-running is safe (idempotent): symlinks are refreshed; the DB is created only if
# absent. PWC source stays here; the skill symlinks point back at it, so `git pull`
# in this repo upgrades every session at once.

set -euo pipefail

PWC_SRC="$(cd "$(dirname "$0")" && pwd)"
WS="${1:-~/work/acme}"

if [[ ! -d "$WS" ]]; then
  echo "pwc: workspace does not exist: $WS" >&2
  exit 1
fi

SKILLS=(find-work show-work pick-work start-work report-status)
GLOBAL_SKILLS="$HOME/.claude/skills"

# 1. Global skills — visible from any cwd (so workers in a repo can resolve them).
mkdir -p "$GLOBAL_SKILLS"
for skill in "${SKILLS[@]}"; do
  ln -sfn "$PWC_SRC/skills/$skill" "$GLOBAL_SKILLS/$skill"
  echo "linked $skill -> $GLOBAL_SKILLS/$skill"
done

# 2. Per-workspace task database.
mkdir -p "$WS/.pwc"
python3 "$PWC_SRC/scripts/taskdb.py" --workspace "$WS" init

echo "pwc: installed"
echo "     skills (global): ${SKILLS[*]}  ->  $GLOBAL_SKILLS"
echo "     task database:    $WS/.pwc/taskdb.db"
echo "     run /show-work in a Claude Code session started in $WS"
