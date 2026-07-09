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

- **CLI**: `pwc` — on PATH (installed by `install.sh` as `~/.local/bin/pwc`). All task-database access goes through it; never read or write the database directly.
- **Workspace**: the current directory; task database auto-discovered at
  `<workspace>/.pwc/taskdb.db`.

## Tools

- `pwc sources enabled` — the per-workspace config of which sources
  to scan and how (which Jira project + JQL, which GitHub org, which Slack channels,
  etc.). **Read this first** — it tells you what to scan; don't assume.
- **External sources**, via the workspace's already-permissioned tools, driven by
  the config above: Jira (`mcp__atlassian__searchJiraIssuesUsingJql` with the
  configured JQL), GitHub (`gh pr list` / `gh search` scoped to the configured org
  and watch types), Slack (`slack_search_*` in the configured channels), email
  (Gmail MCP).
- `pwc find-refs --ref-type <t> --value <v>` — check whether
  a candidate is already tracked (so a known item isn't proposed again).
- `pwc add-task ...` and `add-ref ...` — create a task and
  attach its identity reference, **only after the user confirms**.
- `pwc log-event --kind new-task --detail "..."` — record the
  promotion.

## Steps

1. **Read the sources config** with `pwc sources enabled`. If it's empty (no sources
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
     window*; pull the `working`/`identity` slack refs via `pwc detail`),
     `slack_read_thread` for replies since the task was last touched. A teammate
     often replies **without re-@-mentioning you** ("done!", "ok let's make a
     follow-up ticket", "looks good"), and a reply can land on a thread whose task
     just finished — a mention-only search misses both. Filter out the Jira/bot
     reply that usually trails each human message.

     **Chase the newest reply to its actual text — never report a thread's state from
     a stale or capped read.** `slack_read_channel` and the thread index often show a
     thread's *latest reply timestamp* (e.g. "23 replies, latest 14:22") while
     `slack_read_thread` returns an older page that does **not** include that newest
     message (the API paginates / can cap below the live tail). When the latest-reply
     timestamp is newer than the message you actually read, you have **not** read the
     thread — page forward (follow the `cursor`, pass `oldest`/`latest`, or
     `slack_search` for that exact `ts`) until you have the real newest human reply in
     hand. Do **not** summarize, reconcile, or decide blocked/unblocked from the last
     message you happened to fetch. If after paging you still cannot retrieve the
     newest reply's text, surface it as *"new reply on <task>'s thread at <time> —
     could not read, unverified"* and flag the task for a manual look — never silently
     report the task as quiet or infer the outcome. (This is the maesn-14:22 failure:
     a live reply existed, the thread read stopped short of it, and the task's state
     was reported from a weeks-old message.)

     **Do not stop at the stored ref's thread — find *sibling* threads too.** A
     teammate frequently starts a **brand-new top-level post** about an
     already-tracked ticket rather than replying in the thread you have on file (e.g.
     a "review ready" thread on Monday, then a separate "blocker resolved, please
     re-approve" post on Wednesday). The stored ref only anchors the *first* thread,
     so following it alone misses the newer conversation entirely. For every tracked
     ticket, **also `slack_search` the configured channels by its ticket key and by
     topic keywords**, and `slack_read_thread` on every distinct human-rooted thread
     that comes back — not just the one whose `ts` matches the ref. Treat the *union*
     of those threads as the ticket's current state. If a newer thread is the one now
     carrying the live discussion, attach it as an additional `working` ref so the
     next sweep sees it directly.

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
     each channel since the last scan. This is the catch-all that picks up
     review/test asks, RCAs, and new-bug reports posted in the channel even when
     they're addressed to *someone else* or carry no `@`-mention at all. Without
     this pass, a "ready for review" post to Stella or a "we found a bug, please
     check" to Alison is invisible to find-work, and you'd only see it by accident.

     **Use the search tool that can actually see the channel — `slack_search_public`
     is blind to private channels.** `slack_search_public` only searches *public*
     channels; run against a private channel it returns "No results found" —
     indistinguishable from a genuinely quiet channel, with no error to tip you off.
     For any channel flagged `private: true` in the config (and whenever unsure), scan
     with `slack_search_public_and_private` (`channel_types` including
     `private_channel`). Treat a `slack_read_channel` result as unreliable if its
     newest message predates the `oldest` you passed — it silently ignores `oldest` on
     some channels and returns stale history, so it is not a safe fallback here either.
     (This is the stb-product-private miss, 2026-07-06: two review asks — Alison's
     SMT-1017 and Alex's SMT-997 re-review — sat unseen because the public search
     returned empty and the channel was reported quiet.)

     **`slack_read_channel` returns the post text, NOT the thread under it — and a
     post's text is not its current state.** A channel post is the *root* of a thread;
     replies (including your own) live under it and routinely change what the post
     means ("ready for review" → already approved; "found a bug" → already fixed). So
     **for any post that looks like a work signal — a review/test ask, a "blocker
     resolved" / "ready to merge", a bug report, a "please check", a question aimed at
     anyone — `slack_read_thread` on that post and read to the latest reply *before*
     you surface it.** Never describe where something stands, or propose a status
     change, from the root text alone. This is the rule whose absence makes you tell
     the user "Alison is waiting on your re-approval" when the thread already shows you
     approved it.

   **Surface human posts; do not pre-judge whether they're "for you."** This is the
   rule the third pass exists to enforce. The coordinator is allowed to drop **bot
   noise** automatically — Jira/Calendar/Rotation/Slackbot posts have no human in
   them and are never the signal. But a *human* post in a configured channel is
   always a candidate, even when it tags someone else. The user decides whether it's
   relevant; the coordinator's job is to put it on the list. The failure mode this
   rule prevents is the coordinator silently filtering out posts addressed to other
   teammates (the "Alison asked Stella for validation, so it's not for Shreyans"
   trap) — which is exactly the kind of work the user wanted to see.

3. **Drop anything already tracked — but only on a `find-refs` *identity* match,
   never on judgement.** For each candidate, run `find-refs` on its identity
   reference (Jira key, PR, Slack channel+ts). A candidate counts as already-tracked
   **only if `find-refs` returns a task whose `task_id` is non-null for that
   identity** — i.e. some task carries it as an *identity* ref. If so, skip it (it'll
   be handled by `/pwc-show-work`'s reconciliation). This prevents duplicates.

   **Two traps that have caused real skips — both forbidden:**
   - **"It's covered by another task."** A ticket appearing as a *working* ref on some
     other task (e.g. listed as prep material, or mentioned in a note) is **NOT
     tracked** — `find-refs` returns `task_id: None` for a bare working ref. Being
     referenced by another task is not the same as being a tracked work item. If it's
     assigned to the user and in a pickup column, it needs its **own** task. Never
     dismiss it as "already covered" because you attached it somewhere else.
   - **Every assigned-to-me Jira ticket the scan returned must pass through this gate
     explicitly.** Do not eyeball the list and decide some are "obviously handled."
     Run `find-refs` on each one. If it's not identity-tracked, it is a candidate and
     must be surfaced in step 4 — full stop. The cost of a redundant surface (user
     says "already on it") is trivial; the cost of a silent skip is a dropped task.

4. **Surface the genuinely-new candidates and ask.** Present them as a short list
   with enough context to decide (what it is, where it came from, why it looks
   actionable). For each, ask whether to queue it as a task. **Never auto-add** —
   the user confirms each one.

   **Before surfacing any Slack-sourced item, reconcile it against the full thread —
   including the user's own replies.** For each candidate or status-change drawn from
   a Slack post, confirm you have read that post's thread to the latest message (step
   2) and that your one-line framing matches the *end state* of the conversation, not
   the root post. In particular, check whether **the user has already replied** —
   approved, answered, pushed back, handed it off. If they have, the ball may no
   longer be with them, and "X is waiting on you" is wrong. When the thread shows the
   user already acted, say so ("you already approved on 06-02; nothing waiting") and
   either drop the item or reframe it as the genuine remaining step — never present a
   resolved ask as still-open. If the threads you found *disagree* (an old "please
   review" and a newer "thanks, merged"), the newest human reply wins; reconcile to
   it. A candidate you can't reconcile because a thread wouldn't resolve gets
   surfaced *as that* ("couldn't read the thread for SMT-877 — unverified"), not as a
   confident claim.

   **When the user's part is done, the status is `done` — not `blocked`.** If the
   reconciliation shows the user has finished their step (review approved/changes
   requested, question answered, PR handed off) and the only thing left is a
   teammate's action (their merge, their reply, their deploy), set/leave the task
   `done`, not `blocked`. `blocked` is reserved for "can't proceed *and the user still
   owns the next step.*" The board is "what needs me," so don't park a task there that
   the user can't act on. Closing it is safe: find-work resurfaces it on the next scan
   if the teammate comes back, and a done task stays visible ~2 days regardless. In
   particular, **a PR-review task is `done` the moment the user submits their review** —
   waiting on the author to merge is not a user-blocked state.

5. **Queue the confirmed ones.** For each the user approves, first **derive its id
   from the source's `id_convention`** (from the sources config):
   - `jira-key` → use the Jira key verbatim as `--task` (e.g. `SMT-874`).
   - `<prefix>-slug` → `--task <prefix>-<short-slug-of-title>` (e.g. `slack-deploy-window`).
   - multi-source or unclear → use the config's top-level `id_fallback`.
   `pwc` dedups the id automatically if it's taken, so don't worry about
   collisions. Then:
   `add-task --task <derived-id> --type <jira|pr-review|slack|email|...> --title "..." [--workdir <repo>] --priority <N>`,
   then `add-ref --kind identity --ref-type <t> --value <raw-id>` to attach its
   identity reference, then `log-event --kind new-task`. Report back what was queued.

   **Set `--priority` by the workspace's configured priority model** (lower number =
   higher priority; `pick-work` sorts ascending, null last). The priority model is
   **workspace policy, not baked into this skill** — it depends on the workspace's Jira
   columns, team conventions, and single- vs multi-user shape. **Read it with
   `pwc sources priority`** and apply the `model` / `tiers` it returns
   to each task you queue. (If a task's band isn't obvious until you've read its Slack
   thread — step 6 — add it at the middle tier and adjust once the cross-link reveals
   the truth; whatever the configured model says about checking real Jira status +
   assignee, do that before committing a tier.)

   **If `pwc sources priority` returns `{}` (no model configured), fall back to this
   generic default** and tell the user the workspace has no priority model set (suggest
   `/pwc-setup-workspace` to add one):
   - **`1`** — someone is actively waiting on the user (review / input / answer), or a
     customer/deadline is at stake.
   - **`2`** — active work that's the user's to drive but blocks no one right now.
   - **`3`** — solo / research with no one waiting.
   Do **not** hardcode any specific workspace's rules (literal Jira column names, On-Duty
   handling, etc.) back into this skill — those belong in the config's `priority` block.

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

7. **Show the coverage ledger — account for *every* scanned item before rendering the
   board.** Before the board, present a short reconciliation table covering **every
   assigned-to-me Jira ticket the scan returned** (and any Slack/PR candidate), one
   row each, with its disposition: `tracked` (identity ref already exists — name the
   task), `queued` (just added this run), or `skipped` (with a one-line reason, e.g.
   "assigned to Stella, not me"). Nothing the scan surfaced may be absent from this
   ledger. The point is that a skipped item becomes **visible and justified**, not
   silently dropped: if a row says `skipped` for a thin reason, the user can catch it
   on the spot. An item that is neither tracked nor queued nor explicitly
   skipped-with-reason is a bug — do not let one fall off the list. This is the
   backstop for the whole skill: even if an earlier step mis-judged something, the
   ledger forces it back into view.

8. **Render the full board at the end.** After queuing (and after reporting what was
   found / linked), always finish by rendering the board exactly as `/pwc-show-work`
   does — run `pwc summary` and present it in that format: a **main table**
   (columns `# | Status | Pri | ID | Dir | Desc`) holding only `pending` / `in-progress` /
   `blocked` rows, sorted by status-band then priority, with the emoji status (⚪
   pending / 🟢 in-progress / ⛔ blocked), the task's `Dir` (its `workdir`: a repo name,
   `/` for the workspace root when empty, `—` when unset), and a short identifying Desc
   per task; then a
   **separate small "✅ Recently finished" table** for the ~2-day `done` set
   (`# | ID | Desc`). The point is that a find-work pass changes the board (new tasks,
   raised priorities, linked threads), so the user should see where everything now
   stands without running `/pwc-show-work` separately. This is a *render only* — do not
   re-run find-work's scans or show-work's worker-status/staleness sweeps; just read
   `summary` and display it.

## Notes

- **Surface, never auto-promote** — this is a hard rule (a PWC non-goal is adding
  tasks without confirmation). `/pwc-find-work` proposes; you decide.
- **Whether a candidate is *new* or an *update* to an existing task** is decided by
  `find-refs` on identity references. The automatic matching logic beyond that exact
  check is deliberately left for real cases — when unsure, surface it and let the
  user say "that's the same as t_00xx."
- **A task's canonical id should be its Jira key whenever it has one.** A Jira key
  (`SMT-954`) is far more recognizable on the board than a generated id
  (`slack-bo-phantom-logs`), and keying the task by it prevents duplicates (find-refs
  matches on it). So: when you queue a task that *is* a Jira ticket, use the key as the
  `--task` id directly (the `jira-key` id_convention already does this). And when a
  **slack/email-typed task later gains a Jira ticket** — a bug-form post that gets a
  ticket filed for it, a thread that becomes SMT-NNN — don't just attach the key as a
  ref or mention it in the title: **`pwc promote --task <old-id> --new-id <KEY>`**
  to re-key the task to the Jira key. Promote keeps the old id as an alias (so
  `start`/refs/history still resolve) and re-points everything, so the board then shows
  the key with no special-casing. (This is `pwc promote` — re-keying an id — NOT
  the "surface, never auto-promote" rule above, which is about not auto-*adding* tasks.)
  Attaching the Jira key as an identity ref is still required either way; promote is how
  the *id itself* follows the key.
- **When two tickets are really one piece of work** (e.g. a backend ticket and its
  frontend ticket that ship together), don't fake the combine with a stray extra ref
  and a notes blob. Queue them, then `pwc merge --from <absorbed> --into <survivor>`:
  the survivor inherits both ids as identity refs (so neither gets re-proposed),
  plus the absorbed task's history and aliases, and the absorbed id still resolves
  via `--task`. Confirm the direction with the user (which id survives) before merging.
- `/pwc-find-work` does not reconcile or report on existing tasks — that's `/pwc-show-work`.
  Run `/pwc-find-work` to bring new work in, `/pwc-show-work` to see where everything stands.
