---
name: pwc-triage-slack
description: The coordinator's Slack inbox-sorting pass. Sweeps the configured channels (#your-team-channel) and the user's DMs since the last triage, and sorts each actionable message into one of four buckets — queue a task, attach to existing work, reply, or skip. Surfaces and proposes; never auto-posts or auto-queues. Run periodically to keep the Slack inbox sorted.
---

# /pwc-triage-slack

Help the user (a lead engineer) **sort through incoming Slack** — `#your-team-channel`
and their DMs — so nothing actionable slips and the inbox doesn't have to live in
their head. Where `/pwc-find-work` scans sources to *queue tasks*, triage is about
*clearing the inbox*: most messages aren't tasks (they're replies, FYIs, updates,
noise), and each needs sorting, not just task-extraction.

Triage **surfaces and proposes** — it never posts a reply or queues a task without
the user's confirmation.

## Configuration

- **Scripts directory**: `~/work/pwc/scripts` (`$SCRIPTS`).
- **Workspace**: current directory; task DB + config auto-discovered.
- **Triage config**: `python3 $SCRIPTS/sources.py triage` returns the channels, the
  `scan_dms` flag, and the `last_triaged` watermark. If it's empty, tell the user to
  add a `triage` block (via `/pwc-setup-workspace` or by editing the config) and stop.

## Tools

- `python3 $SCRIPTS/sources.py triage` — what to sweep and from when.
- `python3 $SCRIPTS/sources.py set-triaged --at <ISO>` — advance the watermark after a pass.
- Slack reads: `slack_search_public_and_private` (with `after:`), `slack_read_channel`,
  `slack_read_thread`, `slack_read_user_profile`.
- `python3 $SCRIPTS/taskdb.py find-refs --value <ts|key|pr>` — is this already tracked?
- `python3 $SCRIPTS/taskdb.py add-task` / `add-ref` / `log-event` — queue a new task (on confirm).
- `/slack-message` — to draft+send a reply (on confirm).

## Steps

1. **Read the triage config** (`sources.py triage`). Determine the lookback: use
   `last_triaged` as the floor; if absent, fall back to the last ~24h. Note the floor.

2. **Sweep the inbox since the floor.** For the configured channel(s) read all
   messages since the floor (`slack_read_channel`, or `slack_search_public_and_private`
   with `in:#channel after:<floor>`); for DMs, read recent DM messages (`to:me` /
   per-DM). **This is NOT mention-only** — read everything, that's the point. Filter
   out bot/integration posts (Jira, Calendar, Rotation, etc.) — the signal is human
   messages. Expand threads where the surrounding context matters.

3. **Sort each human message into one bucket.** For each, first `find-refs` (on the
   thread ts, a Jira key, or a PR it names) to see if it's already tracked, then bucket:
   - **New task** — a discrete ask/bug/review that warrants tracking and isn't already
     a task. → propose queuing (step 5a).
   - **Update to existing work** — a reply/news on a thread or ticket already tracked.
     → propose attaching to that task (step 5b).
   - **Reply needed** — a question or ask aimed at the user that just needs an answer,
     not a task. → propose drafting a reply (step 5c).
   - **Skip / FYI** — noise, chatter, already-resolved, or not for the user. → note and
     move on.

4. **Present one at a time.** Walk the actionable items individually (skip the noise
   silently, or list it briefly at the end). For each, show who/when/what and your
   proposed bucket + action, and let the user decide (confirm / change bucket / skip).
   Don't batch-confirm; the user wants to see each.

5. **Act only on confirmation:**
   - **5a New task** — `add-task` (derive id per the source's `id_convention`; set
     `--priority` per the unblock-others rule — 1 if someone's waiting on the user),
     `add-ref --kind identity --ref-type slack --value <permalink>`, `log-event
     --kind new-task`. (Same as find-work's queue step.)
   - **5b Update** — `add-ref --kind working --ref-type slack` onto the matching task
     (and a `note` event); raise that task's priority if the update reveals someone's
     now waiting.
   - **5c Reply** — hand off to `/slack-message` (which itself confirms before sending).

6. **Advance the watermark.** After the pass, `sources.py set-triaged --at <now>` so
   the next run starts where this one ended. Report a short summary: N swept, X queued,
   Y replies drafted, Z attached, the rest skipped.

## Notes

- **Surface, never auto-act.** Triage proposes; the user confirms every task, reply,
  and attach. Consistent with the coordinator's role (queue/track + improve, don't
  drive) and confirm-before-posting.
- **Triage vs. find-work.** find-work scans *all* sources (Jira, GitHub, Slack) to
  *queue tasks*; triage is the *Slack inbox* pass that also handles replies/FYIs/updates,
  not just tasks. They overlap on "new task" — if both are run, `find-refs` dedup keeps
  a thing from being queued twice.
- **Incremental by watermark.** Only messages after `last_triaged` are swept, so reruns
  don't re-surface handled items. First run uses a ~24h window.
- **Bots are noise.** Always filter Jira/Calendar/Rotation/etc. integration posts.
