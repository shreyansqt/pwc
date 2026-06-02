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

   **Slack must be scanned three ways, not one** — they catch different things, and
   missing any one of them silently drops real work:
   - **New mentions/DMs** — search for fresh `<@me>` mentions and direct messages
     since the last scan. This catches brand-new pings *addressed to you*.
   - **Activity on threads you already track** — for every Slack thread linked to a
     task on the board (active tasks *and recently-done ones still in the ~2-day
     window*; pull the `working`/`identity` slack refs via `taskdb.py detail`),
     `slack_read_thread` for replies since the task was last touched. A teammate
     often replies **without re-@-mentioning you** ("done!", "ok let's make a
     follow-up ticket", "looks good"), and a reply can land on a thread whose task
     just finished — a mention-only search misses both. Filter out the Jira/bot
     reply that usually trails each human message.

     **If `slack_read_thread` returns `thread_not_found` (or no parent), do NOT treat
     the task as quiet — recover.** A not-found almost always means the stored ref has
     a bad/fabricated `ts` (the `...000000` tell), not that the thread is silent.
     Fall back to a content search — `slack_search` by the task's ticket key and/or
     title keywords + the known participants — to locate the real thread, read it, and
     then **repair the ref** with the real `thread_ts` (`add-ref` the corrected
     permalink; log a note that the old one was defunct). Surface "couldn't resolve
     thread ref for <task>" in the report rather than letting it pass silently — a
     swallowed not-found is exactly how a teammate's review/answer goes unseen.
   - **Every new message in the configured channels** — `slack_read_channel` on
     each channel since the last scan, top-level posts only (replies are covered by
     the tracked-thread sweep above). This is the catch-all that picks up
     review/test asks, RCAs, and new-bug reports posted in the channel even when
     they're addressed to *someone else* or carry no `@`-mention at all. Without
     this pass, a "ready for review" post to Stella or a "we found a bug, please
     check" to Alison is invisible to find-work, and you'd only see it by accident.

   **Surface human posts; do not pre-judge whether they're "for you."** This is the
   rule the third pass exists to enforce. The coordinator is allowed to drop **bot
   noise** automatically — Jira/Calendar/Rotation/Slackbot posts have no human in
   them and are never the signal. But a *human* post in a configured channel is
   always a candidate, even when it tags someone else. The user decides whether it's
   relevant; the coordinator's job is to put it on the list. The failure mode this
   rule prevents is the coordinator silently filtering out posts addressed to other
   teammates (the "Alison asked Stella for validation, so it's not for Shreyans"
   trap) — which is exactly the kind of work the user wanted to see.

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
   Only create a *standalone* slack task when a thread has no matching ticket. Report
   which threads were linked.

   **NEVER fabricate the timestamp in a Slack ref.** The permalink's trailing
   `p<digits>` IS the message's real `ts` with the dot removed (ts `1780153851.977769`
   → `p1780153851977769`). You MUST take that `ts` from the **actual message object**
   returned by `slack_search_*` / `slack_read_*` (the `Message_ts` / `thread_ts`
   field) — never synthesize it from a wall-clock time by padding unix-seconds with
   zeros. A ref ending in `...000000` is the tell that the ts was made up: it resolves
   to no message, so `slack_read_thread` returns `thread_not_found`, and the
   tracked-thread sweep (step 2) then silently reports the task as "quiet" while real
   replies pile up unseen. If all you have is a human time, do a `slack_search` first
   to fetch the real message and read its `ts` — do not guess. Use the `thread_ts` of
   the *parent* message (not a reply's ts) so the ref anchors the whole thread.

   **While reading each thread, judge whether someone is waiting on the user** — a
   teammate asking for a review/answer, an approach review blocking someone from
   starting, a PR of theirs needing approval, a customer/deadline at risk. If so,
   raise that task to `--priority 1` (`update-task --task <id> --priority 1`) and note
   who/why in a line: `log-event --task <id> --kind note --detail "blocks <who>: <why>"`.
   This is the durable "unblock others first" signal `pick-work` ranks on. find-work
   is the *only* place that reads the sources to set this — `show-work` never re-scans.

7. **Render the full board at the end.** After queuing (and after reporting what was
   found / linked), always finish by rendering the same prioritized briefing that
   `/pwc-show-work` produces — run `taskdb.py summary` and present it in
   `/pwc-show-work`'s format (the uniform grid: `# | ID | Status | Desc | ●`, all
   sections same columns, a short identifying Desc per task). The point is that a
   find-work pass changes the board (new tasks, raised priorities, linked threads), so
   the user should see where everything now stands without having to run
   `/pwc-show-work` separately. This is a *render only* — do not re-run find-work's
   scans or show-work's worker-status/staleness sweeps here; just read `summary` and
   display it so the user ends with the current picture.

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
- **find-work vs. `/pwc-triage-slack`** — both read the same channels; the
  difference is the *output*. find-work's job is to surface posts that look like
  **task candidates** (review asks, bug reports, "please check", new tickets,
  customer issues) and ask whether to queue each. `/pwc-triage-slack` is the
  inbox-sorting pass: it categorizes *every* message into one of four buckets
  (task / reply-needed / FYI / skip) and helps you walk through them. Run
  find-work when you want "what new work is there"; run triage when you want
  "I haven't looked at Slack in two days, sort it for me."
