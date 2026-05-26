---
name: pwc-find-work
description: Explore external sources (Jira, GitHub, Slack, email) for things that might be new PWC tasks, and queue the ones you confirm. Surfaces candidates only — never adds a task without your say-so.
---

# /pwc-find-work

Look outward for work that isn't tracked yet. `/pwc-find-work` scans your external
sources for items that look like they could be tasks — and proposes them. Nothing
is added to the task database until you confirm. This is the *inbound* edge of PWC,
deliberately separate from `/pwc-show-work` (which reports on work you're already
tracking): find brings new work in; show tells you where existing work stands.

## Configuration

- **Scripts directory**: `~/work/pwc/scripts` (`$SCRIPTS`).
- **Workspace**: the current directory; task database auto-discovered at
  `<workspace>/.pwc/taskdb.db`.

## Tools

- `python3 $SCRIPTS/sources.py enabled` — the per-workspace config of which sources
  to scan and how (which Jira project + JQL, which GitHub org, which Slack channels,
  etc.). **Read this first** — it tells you what to scan; don't assume.
- **External sources**, via the workspace's already-permissioned tools, driven by
  the config above: Jira (`mcp__atlassian__searchJiraIssuesUsingJql` with the
  configured JQL), GitHub (`gh pr list` / `gh search` scoped to the configured org
  and watch types), Slack (`slack_search_*` in the configured channels), email
  (Gmail MCP).
- `python3 $SCRIPTS/taskdb.py find-refs --ref-type <t> --value <v>` — check whether
  a candidate is already tracked (so a known item isn't proposed again).
- `python3 $SCRIPTS/taskdb.py add-task ...` and `add-ref ...` — create a task and
  attach its identity reference, **only after the user confirms**.
- `python3 $SCRIPTS/taskdb.py log-event --kind new-task --detail "..."` — record the
  promotion.

## Steps

1. **Read the sources config** with `sources.py enabled`. If it's empty (no sources
   configured), tell the user to run `/pwc-setup-workspace` first and stop — there's
   nothing to scan until sources are set up.

2. **Scan each enabled source** using its configured parameters: run the configured
   Jira JQL, list GitHub items for the configured org/watch-types, search the
   configured Slack channels, etc. (Let the user narrow further if they ask — e.g.
   "just Slack.") Only scan sources that are enabled in the config.

3. **Drop anything already tracked.** For each candidate, run `find-refs` on its
   identity reference (Jira key, PR, Slack channel+ts). If a task already carries
   that ref, it's not new — skip it (it'll be handled by `/pwc-show-work`'s
   reconciliation). This prevents duplicates.

4. **Surface the genuinely-new candidates and ask.** Present them as a short list
   with enough context to decide (what it is, where it came from, why it looks
   actionable). For each, ask whether to queue it as a task. **Never auto-add** —
   the user confirms each one.

5. **Queue the confirmed ones.** For each the user approves, first **derive its id
   from the source's `id_convention`** (from the sources config):
   - `jira-key` → use the Jira key verbatim as `--task` (e.g. `SMT-874`).
   - `<prefix>-slug` → `--task <prefix>-<short-slug-of-title>` (e.g. `slack-deploy-window`).
   - multi-source or unclear → use the config's top-level `id_fallback`.
   `taskdb.py` dedups the id automatically if it's taken, so don't worry about
   collisions. Then:
   `add-task --task <derived-id> --type <jira|pr-review|slack|email|...> --title "..." [--workdir <repo>]`,
   then `add-ref --kind identity --ref-type <t> --value <raw-id>` to attach its
   identity reference, then `log-event --kind new-task`. Report back what was queued.

## Notes

- **Surface, never auto-promote** — this is a hard rule (a PWC non-goal is adding
  tasks without confirmation). `/pwc-find-work` proposes; you decide.
- **Whether a candidate is *new* or an *update* to an existing task** is decided by
  `find-refs` on identity references. The automatic matching logic beyond that exact
  check is deliberately left for real cases — when unsure, surface it and let the
  user say "that's the same as t_00xx."
- `/pwc-find-work` does not reconcile or report on existing tasks — that's `/pwc-show-work`.
  Run `/pwc-find-work` to bring new work in, `/pwc-show-work` to see where everything stands.
