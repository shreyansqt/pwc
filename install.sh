#!/bin/zsh
# Install PWC into a workspace: symlink the skills into <workspace>/.claude/skills/
# and initialize the per-workspace ledger at <workspace>/.pwc/ledger.db.
#
# Usage:
#   ./install.sh [workspace-dir]      # default: ~/work/acme
#
# Re-running is safe (idempotent): symlinks are refreshed, the DB is created only
# if absent. PWC source stays here; only symlinks + the .pwc/ ledger live in the
# workspace, so `git pull` in this repo upgrades every installed workspace at once.

set -euo pipefail

PWC_SRC="$(cd "$(dirname "$0")" && pwd)"
WS="${1:-~/work/acme}"

if [[ ! -d "$WS" ]]; then
  echo "pwc: workspace does not exist: $WS" >&2
  exit 1
fi

SKILLS=(brief next dispatch pwc-report)

mkdir -p "$WS/.claude/skills" "$WS/.pwc"

for skill in "${SKILLS[@]}"; do
  ln -sfn "$PWC_SRC/skills/$skill" "$WS/.claude/skills/$skill"
  echo "linked $skill -> $WS/.claude/skills/$skill"
done

python3 "$PWC_SRC/scripts/ledger.py" --workspace "$WS" init

echo "pwc: installed into $WS"
echo "     skills: ${SKILLS[*]}"
echo "     ledger: $WS/.pwc/ledger.db"
echo "     run /brief in a Claude Code session started in $WS"
