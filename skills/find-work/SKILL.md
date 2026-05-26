---
name: find-work
description: Explore external sources (Jira, GitHub, Slack, email) for things that might be new PWC tasks, and queue the ones you confirm. Surfaces candidates only — never adds a task without your say-so.
---

# /find-work

Look outward for work that isn't tracked yet. `/find-work` scans your external
sources for items that look like they could be tasks — and proposes them. Nothing
is added to the task database until you confirm. This is the *inbound* edge of PWC,
deliberately separate from `/show-work` (which reports on work you're already
tracking): find brings new work in; show tells you where existing work stands.

## Configuration

- **Scripts directory**: `~/work/pwc/scripts` (`$SCRIPTS`).
- **Workspace**: the current directory; task database auto-discovered at
  `<workspace>/.pwc/taskdb.db`.

## Tools

- **External sources**, via the workspace's already-permissioned tools:
  Jira (`mcp__atlassian__searchJiraIssuesUsingJql` for issues assigned to me),
  GitHub (`gh pr list`, `gh search` for review requests / mentions),
  Slack (`slack_search_*` for threads that mention me), email (Gmail MCP for unread
  that needs action).
- `python3 $SCRIPTS/taskdb.py find-refs --ref-type <t> --value <v>` — check whether
  a candidate is already tracked (so a known item isn't proposed again).
- `python3 $SCRIPTS/taskdb.py add-task ...` and `add-ref ...` — create a task and
  attach its identity reference, **only after the user confirms**.
- `python3 $SCRIPTS/taskdb.py log-event --kind new-task --detail "..."` — record the
  promotion.

## Steps

1. **Scan the sources** for candidate work: Jira tickets assigned to me, PR review
   requests, Slack threads mentioning me, unread email that needs action. (Let the
   user narrow the scope if they ask — e.g. "just Slack.")

2. **Drop anything already tracked.** For each candidate, run `find-refs` on its
   identity reference (Jira key, PR, Slack channel+ts). If a task already carries
   that ref, it's not new — skip it (it'll be handled by `/show-work`'s
   reconciliation). This prevents duplicates.

3. **Surface the genuinely-new candidates and ask.** Present them as a short list
   with enough context to decide (what it is, where it came from, why it looks
   actionable). For each, ask whether to queue it as a task. **Never auto-add** —
   the user confirms each one.

4. **Queue the confirmed ones.** For each the user approves:
   `add-task --type <jira|pr-review|slack|email|...> --title "..." [--workdir <repo>]`,
   then `add-ref --kind identity --ref-type <t> --value <raw-id>` to attach its
   identity reference, then `log-event --kind new-task`. Report back what was queued.

## Notes

- **Surface, never auto-promote** — this is a hard rule (a PWC non-goal is adding
  tasks without confirmation). `/find-work` proposes; you decide.
- **Whether a candidate is *new* or an *update* to an existing task** is decided by
  `find-refs` on identity references. The automatic matching logic beyond that exact
  check is deliberately left for real cases — when unsure, surface it and let the
  user say "that's the same as t_00xx."
- `/find-work` does not reconcile or report on existing tasks — that's `/show-work`.
  Run `/find-work` to bring new work in, `/show-work` to see where everything stands.
