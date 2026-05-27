---
name: pwc-triage-slack
description: The coordinator's Slack inbox-sorting pass. Sweeps the configured channels (#your-team-channel) and the user's DMs since the last triage, clusters the activity into TOPICS (not individual messages), and for each topic proposes one of four buckets — queue a task, attach to existing work, reply, or skip. Presents one topic at a time as a short summary (raw messages only on request). Surfaces and proposes; never auto-posts or auto-queues.
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

2. **Sweep the inbox since the floor — completely.** Read *all* activity since the
   floor, not just a flat search (a single `in:#channel after:` search misses thread
   replies, which is where this channel actually lives). So:
   - Enumerate the **threads active in the window** and `slack_read_thread` each in
     full (root + replies). Also catch new top-level messages.
   - For DMs, read recent DM messages (`to:me` / per-DM).
   Filter out bot/integration posts (Jira, Calendar, Rotation, etc.). **This is NOT
   mention-only** — read everything; that's the point. (A flat keyword search alone is
   not sufficient coverage — it dropped real messages in testing.)

3. **Cluster into TOPICS, not messages.** The user triages *topics*, not individual
   messages. Group the swept messages **semantically by subject** — usually one Slack
   thread is one topic, but merge threads/DMs that are about the same thing (e.g. two
   threads both about the SevDesk sync = one topic), and split a thread if it genuinely
   covers two unrelated subjects. For each topic, `find-refs` (on its thread ts / Jira
   key / PR) to see if it's already tracked, then assign a bucket:
   - **New task** — a discrete ask/bug/review worth tracking, not already a task. → 5a.
   - **Update to existing work** — news on a thread/ticket already tracked. → 5b.
   - **Reply needed** — a question/ask aimed at the user, just needs an answer. → 5c.
   - **Skip / FYI** — noise, chatter, resolved, the user's own outbound, or not for
     them. → drop.

4. **Present one TOPIC at a time — summary, not raw messages.** Walk the actionable
   topics individually. For each, give a **one-to-two-line summary** of the topic (what
   it's about, who's involved, latest state) + your proposed bucket, and let the user
   decide (confirm / change bucket / skip / **drill in**). **Do NOT show the raw
   messages by default** — only expand a topic's actual messages if the user asks to
   see them. Skip the noise topics silently (a brief end-of-pass tally is fine).

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
   the next run starts where this one ended. Report a short topic-level summary: N
   topics surfaced, X queued, Y replies drafted, Z attached, the rest skipped.

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
- **Topics, not messages.** The user thinks in topics ("the SevDesk thing", "the
  refinement reschedule"), not individual Slack messages. Triage groups + presents at
  the topic level and keeps raw messages hidden until asked. Never walk message-by-message.
- **Cover threads, not just a search.** A single `in:#channel after:` keyword search
  silently drops thread replies (observed in testing — it missed real messages). Always
  enumerate active threads and read each in full; treat a flat search as a starting
  index, not complete coverage.
