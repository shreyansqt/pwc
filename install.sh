#!/bin/zsh
# Install PWC. Two parts:
#   1. Skills go GLOBAL (~/.claude/skills/) so every Claude Code session sees them —
#      coordinator and spawned workers alike (workers run in a repo, not the
#      workspace root, so workspace-local skills wouldn't resolve for them).
#   2. The task database is per-workspace at <workspace>/.pwc/taskdb.db.
#
# Usage:
#   ./install.sh [workspace-dir]      # default: ~/workspaces/acme
#
# Re-running is safe (idempotent): symlinks are refreshed; the DB is created only if
# absent. PWC source stays here; the skill symlinks point back at it, so `git pull`
# in this repo upgrades every session at once.

set -euo pipefail

PWC_SRC="$(cd "$(dirname "$0")" && pwd)"
WS="${1:-~/workspaces/acme}"

if [[ ! -d "$WS" ]]; then
  echo "pwc: workspace does not exist: $WS" >&2
  exit 1
fi

SKILLS=(pwc-setup-workspace pwc-find-work pwc-show-work pwc-show-task pwc-pick-work pwc-start-work pwc-report-status)
GLOBAL_SKILLS="$HOME/.claude/skills"

# 1. Global skills — visible from any cwd (so workers in a repo can resolve them).
mkdir -p "$GLOBAL_SKILLS"
for skill in "${SKILLS[@]}"; do
  ln -sfn "$PWC_SRC/skills/$skill" "$GLOBAL_SKILLS/$skill"
  echo "linked $skill -> $GLOBAL_SKILLS/$skill"
done

# 1b. The pwc CLI — one named command for the whole deterministic mechanism, on
#     PATH so the coordinator AND any worker (whatever harness, whatever cwd) can
#     run `pwc ...` instead of an opaque python3 path.
BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"
ln -sfn "$PWC_SRC/bin/pwc" "$BIN_DIR/pwc"
echo "linked pwc -> $BIN_DIR/pwc"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "pwc: warning: $BIN_DIR is not on your PATH — add it to use \`pwc\`" >&2 ;;
esac

# 2. Per-workspace task database.
mkdir -p "$WS/.pwc"
python3 "$PWC_SRC/scripts/taskdb.py" --workspace "$WS" init

# 3. Splice the PWC section into the workspace CLAUDE.md so every session there
#    knows PWC is set up (and the coordinator opens with /pwc-show-work). Idempotent;
#    leaves any existing CLAUDE.md content untouched.
python3 "$PWC_SRC/scripts/claude_md.py" --target "$WS/CLAUDE.md"

echo "pwc: installed"
echo "     skills (global): ${SKILLS[*]}  ->  $GLOBAL_SKILLS"
echo "     cli:              $BIN_DIR/pwc"
echo "     task database:    $WS/.pwc/taskdb.db"
echo "     CLAUDE.md:        PWC section added to $WS/CLAUDE.md"
echo "     run /pwc-show-work in a Claude Code session started in $WS"
