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
  and calendar (per their `scan_note` — see steps 2a/2b).
  **Each Slack channel carries an explicit `mode`** (`tasks` or `digest`) that decides
  which pass reads it and under which rules; a channel with no mode is a config error,
  not a channel to guess about. See step 2b.
- `pwc find-refs --ref-type <t> --value <v>` — check whether
  a candidate is already tracked (so a known item isn't proposed again).
- `pwc add-task ...` and `add-ref ...` — create a task and
  attach its identity reference, **only after the user confirms**.
- `pwc log-event --kind new-task --detail "..."` — record the
  promotion.
- `pwc models stale` / `pwc models fetch [--dry-run]` — the model table's refresh
  clock and refresher (step 0).
- `pwc route --domain <d> --reasoning <n> [--verifiability <n>] [--risk <r>]
  [--context-need <tokens>]` — pick the harness+model for a task (step 5).

## Steps

0. **Refresh the model table if it's stale — and PROPOSE the changes, never apply
   them silently.** Run `pwc models stale` (threshold: 7 days). If it reports
   `stale: false`, skip this step entirely and say nothing.

   If stale, run **`pwc models fetch --dry-run`** — this reads OpenRouter's catalog
   and reports what WOULD change **without writing**. Present the diff to the user the
   same way you present task candidates: a short table of `key | field | old → new`,
   grouped so it's scannable (price moves, context-window changes, models that
   vanished from the catalog, availability flips). Then ask whether to apply it. On a
   yes, run `pwc models fetch` (no `--dry-run`) to write it.

   **This is the same "surface, never auto-promote" rule the rest of this skill
   follows**, and it matters more here than it looks: the table decides where *all*
   future work gets routed and what it costs, so a silent refresh could quietly move
   every task onto a different model. The user sees the change before it takes effect.

   Only the **objective** columns are fetched (cost, context window, availability).
   The capability tiers are the user's own calibration and live in the table's
   `overlay` — `fetch` cannot touch them, so a refresh never reverts a judgment they
   made. Say so if they ask.

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

   **Surface human posts; do not pre-judge whether they're "for you."** *(This rule
   governs `mode: tasks` channels. `mode: digest` channels are the deliberate exception
   — see step 2b.)* This is the
   rule the third pass exists to enforce. The coordinator is allowed to drop **bot
   noise** automatically — Jira/Calendar/Rotation/Slackbot posts have no human in
   them and are never the signal. But a *human* post in a configured channel is
   always a candidate, even when it tags someone else. The user decides whether it's
   relevant; the coordinator's job is to put it on the list. The failure mode this
   rule prevents is the coordinator silently filtering out posts addressed to other
   teammates (the "Alison asked Stella for validation, so it's not for Shreyans"
   trap) — which is exactly the kind of work the user wanted to see.

2a. **Email and calendar — the passes that catch what never reaches Slack or Jira.**
   The skill's frontmatter has always claimed email as a source, but the steps never
   said how to scan it; the same was true of calendar. Both are driven by their
   `scan_note` in the sources config (which holds the account and the exact command) —
   what follows is the *mechanism* the config assumes.

   **Email — two targeted passes, never a blind inbox dump:**
   1. **Human/external asks addressed to the user** since `last_scanned`.
   2. **Meeting recaps and action items** (Fathom, Gemini notes, anything titled
      recap / action items / Aufzeichnung). A recap carrying an action item assigned to
      the user is a **real task candidate** even when nothing in Jira or Slack reflects
      it.

   Drop bot noise automatically: GitHub, Jira, CI, Dependabot, and calendar-invite mail
   itself — all are already covered by their own sources.

   **Calendar — scan BOTH directions. Forward alone is not enough.**
   - **Forward (today → ~+3d)** — upcoming meetings that imply prep or carry an agenda
     the user owns (refinements, follow-ups, anything where they are organizer or a
     required attendee). Ignore pure standups / all-hands / social with no prep.
   - **Backward (last ~7d) — meetings the user was INVITED TO BUT DID NOT ATTEND.**
     This is the pass most likely to be missing, and its absence is silent by
     construction: **a meeting the user skipped produces no recap in their inbox**
     (Fathom and Gemini only send to attendees), so the email recap pass above cannot
     see it either. Its decisions and its action items are invisible in *every* source.

     For each past event where the user was an attendee, check their **`responseStatus`**
     (`declined` / `tentative` / `needsAction` / no response) — those are the skipped
     ones. For each, **go and find the notes they never received**: the event
     description and attachments (Fathom / Gemini / Confluence links), a recap posted
     afterwards in the configured Slack channels (teammates routinely post
     "<meeting> recap" threads), or meeting notes in Confluence. Surface a short summary
     of what was decided, and **call out by name any action item assigned to the user** —
     a task candidate like any other.

     If a skipped meeting's notes cannot be found anywhere, **say so** ("skipped
     <meeting> on <date> — no recap found") rather than staying silent. Do not assume
     nothing happened. Recurring meetings the user habitually skips (standups they are
     not on rotation for, all-hands) can be collapsed to one line or dropped once
     confirmed empty.

2b. **Digest channels — read for what you'd otherwise MISS, never for tasks.**
   Every entry in the Slack config's `channels[]` carries an explicit **`mode`**.
   There is no default: a channel with a missing or unrecognized `mode` is a **config
   error** — stop and ask which pass it belongs to rather than guessing.

   - **`mode: tasks`** — everything in step 2 applies, including "surface human posts,
     do not pre-judge." Posts become task candidates.
   - **`mode: digest`** — an **FYI channel, not a task source.** It never produces a
     task candidate and never auto-queues. This is the one place the "don't pre-judge"
     rule is deliberately inverted: **filtering is the entire point.** A digest channel
     is one the user does not read (too noisy, or not in their language) but which
     occasionally carries something they must not miss.

   For each `mode: digest` channel:
   1. Read it since `last_scanned` (`slack_read_channel`; for a private one use
      `slack_search_public_and_private` — see the blindness warning above).
   2. Apply **that channel's own `surface` / `drop` lists** from the config. The filter
      is workspace policy and lives in the config, deliberately written out as data
      rather than left to judgement — do **not** invent your own criteria, and do not
      fall back to "surface everything human."
   3. **Read the thread** under any post you're about to surface (the step-2 rule that
      a post's root text is not its current state applies here too — an ask may already
      be resolved in-thread).
   4. Report the hits in a **separate `📢 Company / FYI` section at the END of the
      find-work report**, below the task candidates — visually distinct, because these
      are not work to queue.

   Three hard rules for the digest section:
   - **Always report the drop count** — `[skipped 14 Tax Ops posts]`. A filter that
     drops silently is indistinguishable from a broken one, and the user cannot catch a
     bad filter they cannot see.
   - **Tag anything that needs an action FROM the user** — a form, an RSVP, a deadline —
     as **`⚠️ ACTION ON YOU`**, with the deadline. Everything else is plain FYI.
   - **Translate.** If the channel's `language` is not `en`, give each surfaced item a
     **one-line English summary plus the permalink**. The user skips these channels
     *because* of the language, so a same-language digest fixes nothing.

   A digest item becomes a task **only if the user asks for it.**

   (Origin, 2026-07-14: `#stb-all` is smarta's company-wide *and* Tax Ops working
   channel — ~15 posts/week, in German, the large majority not the user's. He therefore
   never read it, and missed the Get2Gether 2026 offsite date **and its RSVP deadline**
   entirely. Scanning it as a `tasks` channel would have buried him in VAT deadlines and
   "who owns this Mandant"; not scanning it lost him the offsite. The digest mode is the
   resolution.)

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

5. **Queue the confirmed ones.**

   **First: read the source's own policy — do NOT assume one.** A source may declare
   two things that change what queueing means, and both are **workspace policy, read
   from the config, never hardcoded in this skill**:

   - **`backlog_note`** — the source holds a BACKLOG (ideas, someday-maybes), and PWC is
     only the SHORTLIST of work happening soon. Where a source says this, **do not
     bulk-import it**: scanning SURFACES candidates, it does not mirror them onto the
     board. Queue only what the user confirms is imminent. A sweep that queues an entire
     backlog swamps the board and destroys the signal it exists to give.
   - **`pairing_note`** — PWC tasks in this workspace are PAIRED with items in that
     tracker (the task id derives from the item's id). Where a source says this, a task
     that *should* have a tracker item but doesn't **needs one filed FIRST**, so the PWC
     task can be keyed to the real id and carry it as an identity ref. Never invent a
     slug id for a task the tracker should own. The `pairing_note` says who files it and
     where — follow it rather than guessing.

   Both are per-workspace by nature: one workspace's tracker items are filed *for* the
   user by a team, another's the user files themselves. Read the note; don't generalize
   one workspace's arrangement onto another.

   Then **derive its id from the source's `id_convention`** (from the sources config):
   - `jira-key` → use the Jira key verbatim as `--task` (e.g. `SMT-874`).
   - `<prefix>-slug` → `--task <prefix>-<short-slug-of-title>` (e.g. `slack-deploy-window`).
   - multi-source or unclear → use the config's top-level `id_fallback`.
   `pwc` dedups the id automatically if it's taken, so don't worry about
   collisions. Then:
   `add-task --task <derived-id> --type <jira|pr-review|slack|email|...> --title "..." [--workdir <repo>] --priority <N> [--harness <h>] [--model <m>]`,
   then `add-ref --kind identity --ref-type <t> --value <raw-id>` to attach its
   identity reference, then `log-event --kind new-task`. Report back what was queued.

   **Set `--harness` / `--model` by profiling the task and asking `pwc route`.**
   Routing is no longer a prose rule you apply by judgment — it's a deterministic
   decision made from a task PROFILE, so the same task always gets the same answer
   and the reasoning is inspectable. For each task you're about to queue, judge four
   things from its title, source, and thread:

   - **`--domain`** — `code-review` | `implementation` | `research-writing` | `ops-comms`.
     What KIND of work is it? (A PR review is `code-review`; a Slack reply is
     `ops-comms`; a spike or a design memo is `research-writing`.)
   - **`--reasoning` 1-5** — how much genuine thinking does it need? A typo fix is 1;
     a tricky concurrency bug or an architecture call is 5. Be honest: inflating this
     is how you end up paying Opus rates for routine work.
   - **`--verifiability` 1-5** — how cheaply would a WRONG answer be caught? Code with
     tests that must pass is high (4-5) — a bad answer fails loudly and costs only a
     retry. A research memo, an architectural recommendation, anything whose wrongness
     is *silent*, is low (1-2). Low verifiability makes `route` demand more capability,
     which is exactly right: you can't afford a plausible-but-wrong answer you won't
     notice.
   - **`--risk`** — `none` | `outward` (a human will read the output as-is: a Slack
     message, a PR comment, a customer email) | `prod-data`.

     **`prod-data` means real customer records will LITERALLY ENTER THE PROMPT** — you
     will paste a customer's transactions in, run a query against production and feed
     back the rows, attach a real export. It does **not** mean "this code runs near a
     database" or "this service touches customer data in production." Reviewing a
     diff, implementing a feature, refining a ticket, writing a migration: the model
     sees *code*, not customer records. Those are `none`.

     Getting this wrong is expensive in both directions. Over-tagging pins routine
     work to the most restricted (and most expensive) models for no privacy benefit —
     the model never saw a customer record either way. Under-tagging leaks real data
     to a surface you didn't choose. So ask the concrete question: *will a customer's
     actual data be in the text I send?*

     When it IS `prod-data`, it's a hard gate: routing is restricted to models marked
     `data_ok` in the table AND the capability floor rises. But note the honest
     position (docs/data-handling.md): with consumer plans there is **no Data
     Processing Agreement with any provider**, so the real answer is usually *don't
     put the customer data in a prompt at all* — work from schemas, anonymized
     fixtures, or a synthetic reproduction. Suggest that before routing it anywhere.
   - **`--context-need`** (optional) — tokens of context the work needs held at once.
     Pass it when a task obviously needs a lot (a big refactor across many files);
     omit otherwise.

   `route` then hard-filters (harness actually installed+authenticated, context window
   big enough, prod-data trust + tier floor), keeps only models whose tier in that
   domain clears the required level, and picks **the cheapest one left**. It returns
   the harness, the model, and a one-line `why`. Pass them straight through as
   `--harness` / `--model`.

   **Show the routing decision with each candidate, always** — not just when it
   deviates from a default. The whole point is that work now flows to cheaper models
   where they're good enough, and the user is choosing to trust that; hiding the
   choice would mean they only discover a task ran on DeepSeek after the fact. One
   line is enough: `→ opencode/deepseek-v4-pro (implementation, tier 3, $0.13/Mtok)`.
   The user can veto or override it in the same confirmation that queues the task — a
   plain `--model` override always wins over the router.

   **If `route` REFUSES** (it exits nonzero when nothing qualifies — there are no
   fallback chains by design), do not silently queue the task unrouted and do not
   quietly pick something yourself. Surface the refusal and its reason: the profile
   may be wrong (did you really mean `prod-data`?), or a harness may be unavailable
   (`opencode` with no credentials), or the context need may exceed every model. Let
   the user decide.

   **`--runhost`** is unchanged and still comes from the workspace's `pwc sources
   routing` policy (it's about WHICH MACHINE, not which model) — set it if a rule
   names one.

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
