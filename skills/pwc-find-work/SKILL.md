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

   **Slack must be scanned two ways, not one** — they catch different things:
   - **New mentions/DMs** — search for fresh `<@me>` mentions and direct messages
     since the last scan. This catches brand-new pings.
   - **Activity on threads you already track** — for every Slack thread linked to a
     task (active *and recently-archived* tasks; pull the `working`/`identity` slack
     refs via `taskdb.py detail`), `slack_read_thread` for replies since the task was
     last touched. A teammate often replies **without re-@-mentioning you** ("done!",
     "ok let's make a follow-up ticket", "looks good"), and a reply can land on a
     thread whose task you just archived — a mention-only search misses both. This
     thread sweep is how you catch follow-ups, agreements, and new asks on work
     already in flight. Filter out the Jira/bot reply that usually trails each human
     message.

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
   `add-task --task <derived-id> --type <jira|pr-review|slack|email|...> --title "..." [--workdir <repo>] --priority <N>`,
   then `add-ref --kind identity --ref-type <t> --value <raw-id>` to attach its
   identity reference, then `log-event --kind new-task`. Report back what was queued.

   **Set `--priority` to encode urgency, where the dominant signal is "is someone
   waiting on me?"** (lower number = higher priority; `pick-work` sorts ascending,
   null last):
   - **`1` — blocks others:** a teammate is waiting on your review/input/answer, or
     a customer/deadline is at stake. The thing whose absence stalls someone else.
   - **`2` — active work** that's yours to drive but blocks no one right now.
   - **`3` — solo / research** with no one waiting.
   You usually can't tell which band a task is in until you've looked at its Slack
   thread (step 6), so it's fine to add it at `2` and raise it to `1` once the
   cross-link reveals someone waiting.

6. **Cross-link related Slack threads onto each Jira task.** A Jira ticket and its
   Slack discussion are one piece of work — track them together, don't spin up a
   separate Slack task for a thread that's really about an existing ticket. For each
   queued Jira task, search the configured Slack channels for related thread(s):
   - by the **ticket key** (e.g. search `SMT-917`), and
   - by **topic keywords** from the title, to catch threads that discuss it without
     naming the key.
   Filter out bot posts (Jira/Calendar/Rotation app messages) — the signal is human
   discussion and `@`-mentions. For each genuine match, attach the thread's permalink
   as a working ref:
   `add-ref --task <id> --kind working --ref-type slack --value <thread-permalink> --label "<channel> thread"`.
   (Build the permalink from the thread's `thread_ts`.) Only create a *standalone*
   slack task when a thread has no matching ticket. Report which threads were linked.

   **While reading each thread, judge whether someone is waiting on the user** — a
   teammate asking for a review/answer, an approach review blocking someone from
   starting, a PR of theirs needing approval, a customer/deadline at risk. If so,
   raise that task to `--priority 1` (`update-task --task <id> --priority 1`) and note
   who/why in a line: `log-event --task <id> --kind note --detail "blocks <who>: <why>"`.
   This is the durable "unblock others first" signal `pick-work` ranks on. find-work
   is the *only* place that reads the sources to set this — `show-work` never re-scans.

## Notes

- **Surface, never auto-promote** — this is a hard rule (a PWC non-goal is adding
  tasks without confirmation). `/pwc-find-work` proposes; you decide.
- **Whether a candidate is *new* or an *update* to an existing task** is decided by
  `find-refs` on identity references. The automatic matching logic beyond that exact
  check is deliberately left for real cases — when unsure, surface it and let the
  user say "that's the same as t_00xx."
- **When two tickets are really one piece of work** (e.g. a backend ticket and its
  frontend ticket that ship together), don't fake the combine with a stray extra ref
  and a notes blob. Queue them, then `taskdb.py merge --from <absorbed> --into <survivor>`:
  the survivor inherits both ids as identity refs (so neither gets re-proposed),
  plus the absorbed task's history and aliases, and the absorbed id still resolves
  via `--task`. Confirm the direction with the user (which id survives) before merging.
- `/pwc-find-work` does not reconcile or report on existing tasks — that's `/pwc-show-work`.
  Run `/pwc-find-work` to bring new work in, `/pwc-show-work` to see where everything stands.
- **Sorting the Slack inbox is `/pwc-triage-slack`'s job.** find-work scans Slack
  narrowly (new mentions + replies on tracked threads) to *queue tasks*; triage
  sweeps the whole `#your-team-channel` + DMs to *sort every message* (tasks,
  replies, FYIs, updates). For "what's piled up in Slack," run `/pwc-triage-slack`.
