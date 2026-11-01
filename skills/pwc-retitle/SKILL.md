---
name: pwc-retitle
description: Restore this session's PWC name and tab title, e.g. after /restart dropped the --name that pwc coord / pwc spawn passed. Any session (coordinator or worker, any harness) runs it when Shreyans asks. Sets the tab title itself and prints the /rename line for him to paste.
---

# /pwc-retitle

Run this when Shreyans tells you to fix your session name or tab title. `/restart`
relaunches a plain `claude` without `--name`, so the session comes back unnamed.

## Steps

1. **Run the command.**
   - Worker: `pwc retitle --task <your task id>` (the task id is in your seed prompt).
   - Coordinator: `pwc retitle --workspace <workspace root>` (no `--task`).
   - Add `--print` to see the names without touching iTerm2.
2. **Read the JSON.** `tab_status` is `set` when the tab title was changed. Any
   `skipped: ...` means iTerm2 was unreachable (not iTerm2, no Python API, or a
   remote `--ssh` worker, whose tab lives on another machine); say so.
3. **Hand Shreyans the rename line.** Print `rename_command` on a line of its own so
   he can copy it, and ask him to paste it. You cannot run `/rename` yourself: it
   is a built-in slash command only the user can type. Tell him the tab title is
   already done, so the paste is the only step left.
4. **codex / opencode:** `rename_command` is `null`. These harnesses have no session
   name; only the tab title applies. Say that and stop.

Never invent a name; use what the command printed.
