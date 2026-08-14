# Design Notes

Decisions made during PRD development, with the reasoning behind them. PRD says
*what*; this doc says *why we chose this what over that what*. Append-only —
when a decision changes, add a new entry rather than editing the old one.

## Why the coordinator is stateless-in-context, stateful-in-storage

A coordinator that requires `/resume` to remember what I'm working on defeats
the purpose. The whole point is to offload the "what's in flight" burden, and
if I have to find the right session to talk to the coordinator, I've recreated
the resumption problem one level up.

Forcing all state out to disk also has nice second-order effects: the task database
becomes inspectable and editable by hand, the coordinator's behavior becomes
testable (boot from a known task database state, see what it does), and the data
model becomes the API for any future tooling.

## Why two-tier memory (index + per-task detail)

Loading every task's full detail into context every session burns the context
budget for no reason and gets worse as task count grows. Loading nothing means
the coordinator has no orientation on startup. The compromise: a tiny
always-loaded index (one line per active task), with detailed per-task files
read only when actually working on that task.

The index is what makes the cold-start briefing fast and consistent. The detail
files are what make resumption rich.

## How a worker's session ID is captured (and why status stays worker-reported)

Earlier alternative considered: spawn a worker, then guess its session ID by
inspecting `~/.claude/projects/<slug>/` filesystem timestamps. Works, but has
race conditions (two workers spawned in the same second) and depends on
implementation details of Claude Code's session storage.

Two ways to get the session ID without guessing it:

- **Self-registration (B):** the worker calls `/register-worker <task-id>` on
  startup, writing its own session ID and task association to the task database.
  Inverts the data flow from "coordinator guesses" to "worker declares."
- **Pre-allocation (C):** the coordinator generates the session ID, passes it on
  the spawn command (`--session-id <uuid>`), and records it in the task database as it
  spawns. The ID is known before the worker process even exists.

**Decision: C is the plan, B is the fallback.** C wins on three counts, all
sharpened by decisions made after this entry was first written:

1. **Reopen needs the ID up front.** Dispatch decides new-vs-reopen from the
   session ID on file. C provides it by construction at spawn time; B only
   provides it after a boot-time callback, leaving a window where the coordinator
   has spawned something it can't yet identify.
2. **No startup hole.** Under B, a worker that crashes during startup before
   `/register-worker` fires is an orphan the coordinator never recorded — a gap
   in the exact worker-status check/tracking guarantee this mechanism exists to provide.
   Under C the row is written at spawn, so even an instantly-dead worker is
   already in the task database as "spawned, then gone."
3. **Fewer moving parts.** No registration skill, no boot handshake, no race
   between registration and the coordinator's next read.

C rests on one empirical unknown — does `--session-id` actually work and
reliably set the session in the installed Claude Code version? That's a single
narrow probe at build time, not an open design question; if the flag proves
unreliable, fall back to B.

**Status reporting is separate and survives either way.** Identity (which
session is which task) is what C absorbs. Ongoing worker reports throughout a
task's life — "blocked," "awaiting review," "done" — are a distinct concern;
workers write those events to the task database regardless of how identity was
established. So the worker-side skill shrinks from register-and-report to just
self-*reporting*; C handles the registration half.

Either way the captured session ID earns its keep twice over, which is why
getting it right matters: it's what worker-status check tests to tell a live
worker from a dead one, *and* it's what lets dispatch reopen a task's prior
session (`claude --resume`) instead of starting cold.

## Why short-lived tasks are inline rather than spawned workers

For a one-shot Slack reply or a quick email triage, spawning a new terminal
window is overkill — the spawn itself takes longer than the action. The
coordinator can invoke the relevant skill (`/slack-message`, etc.) directly in
its own session and record the outcome in the task database.

The mixed model (some tasks inline, some workered) adds the complexity of
deciding which is which. The lean is a type-based heuristic with override: Slack
replies always inline, Jira tickets always worker, ambiguous cases ask. Worth
revisiting once we have a week of usage data on whether the heuristic feels
right.

## Why structured fields are separated from freeform notes

The task database entries have both structured fields (status, external ref, last
event, blockers) and freeform notes (private thoughts, half-formed ideas, "I
think this approach is wrong but Priya keeps pushing it").

Keeping them cleanly separated has two benefits:

1. **Selective loading.** The index needs structured fields only; notes can
   live in per-task detail. If they were blurred, the index would either lose
   nuance or balloon in size.
2. **Future multi-user hygiene.** If PWC ever scales to coordinate with a
   coworker's PWC, the structured fields are what a peer would safely consume;
   notes stay private. Letting them blur now means redacting on the way out
   later, or rebuilding the data model. Cheap to do right from the start.

## Why we're not building inter-agent communication

Workers don't talk to each other. The user is the conductor; workers report to
the coordinator (via the task database), and the coordinator surfaces things to the
user. Anything more than this — agents negotiating, agents handing off to each
other — opens up trust, loops, and hallucinated commitments, and the design
problem stops being "reduce my cognitive load" and starts being "build a
reliable distributed system."

The single-user version is hard enough to get right. Multi-agent collaboration
is an *explicit* non-goal, not a deferral.

## Naming

Deferred. Working name "PWC" is intentionally bland — naming a product before
it exists tends to produce names that are about who you imagine the product to
be rather than what it turns out to be. Candidate metaphors considered (Chief
of Staff, Conductor, Mission Control, Foreman) are all heavily contested in
the AI/orchestration space, with at least one direct competitor in the parallel
Claude Code Mac app niche. Better to dogfood under "PWC" for a few weeks and
name from a position of knowing what the thing actually is.

## Why the store is SQLite, not plain files (supersedes the file-based assumption above)

The two entries above ("stateless-in-storage" and "two-tier memory") were
written assuming plain files on disk, and leaned on hand-inspectability and
hand-editability as benefits. That assumption is now reversed: the store is a
local SQLite database. Per this doc's append-only rule, the earlier entries
stand as written — this entry records what changed and why.

What flipped it: **all task database access goes through the coordinator.** It is the
only reader and writer; there is no plan to hand-edit the store. Once that's
true, the two strongest arguments for files collapse — "I can `cat` and edit it
by hand" is moot if I never do, and "an LLM reads files natively" is moot
because the agent issues a tool call to read the store either way. What's left
is durability, and there SQLite wins decisively: a state change that spans both
memory tiers (spawning a worker updates a task's detail *and* its summary line)
commits as one transaction. The "kill the coordinator anytime, zero loss"
promise then holds by construction, instead of by a hand-rolled
temp-file-then-rename scheme plus a derived-index reconciliation to paper over
torn multi-file writes. The two tiers stop being two file types and become two
query shapes against one store, which also means they can never drift.

Cost paid: the task database is no longer a pile of git-diffable text, so the
longitudinal record can't come from version control. It moves *into* the
schema as an append-only events table — which is arguably where a build/work
log belonged anyway.

Note this also dates the "task database is plain files" enabler cited under GUI /
dashboard below; a TUI or web view now reads the SQLite store rather than flat
files. The point stands — the data is still external to the conversation and
trivially queryable — only the substrate changed.

## Command vocabulary + splitting find-work out of show-work (build-time)

The PRD describes a single `/brief` that does everything, including noticing new
inbound work — we'd collapsed an earlier `/whats-new` into it for v1 simplicity.
During the build (and a vocabulary pass for daily-use comfort) two things changed:

- **Renamed to a verb-phrase family** so the command surface is self-documenting
  and visually distinct from the team-skills commands: `/brief` → `/pwc-show-work`,
  `/next` → `/pwc-pick-work`, `/start` (was the unnamed "dispatch") → `/pwc-start-work`,
  `/pwc-report` → `/pwc-report-status`.
- **Split finding new work back out** into its own `/pwc-find-work`. The intent
  separation reads cleanly with verb names — *find* new work (the inbound edge,
  scans external sources, surfaces candidates, queues on confirm) vs. *show* where
  already-tracked work stands (reconcile + sweeps + recap, no source-scan for new
  items). This is exactly the `/whats-new` seam the PRD predicted might re-split;
  the verb names make "find vs. show" clearer than "brief vs. whats-new" was.

The no-auto-promote rule is unchanged: `/pwc-find-work` surfaces, the user confirms.

## Workers are human-driven in v1 (the seed briefs, it doesn't command)

The first live spawn surfaced a problem the design hadn't anticipated: a freshly
spawned `claude` session **distrusted an imperative worker-role seed prompt and
refused to act** — "this arrived as a user prompt but my instructions don't mention
a PWC worker role." The original vision leaned toward semi-autonomous workers
(accept the task, do it, self-report, finish), which is exactly what a vanilla
session reasonably won't do when handed "you are a worker, run these commands."

Decision for v1: **workers are human-driven.** PWC's job at `/pwc-start-work` is to be
a *dispatcher and context-loader* — open the session in the right repo with the
task's context pre-loaded — not to coerce autonomy. So the seed prompt is reframed
from *imperative* ("act as a worker, run these") to *informative* ("here's your
task; here's a command you can optionally use to record progress"). That sidesteps
the trust problem entirely by not asking the session to do anything it would
distrust.

Consequences: status reporting becomes best-effort rather than mandated (the
human, or the session when natural, records events) — and nothing breaks if a
report is skipped, because `/pwc-show-work`'s worker-status check still notices when a
session ends. Self-driving workers remain a plausible later direction (likely via a
trusted entry skill that *is* the role, so it's read as legitimate rather than a
suspicious prompt), but they're explicitly out of scope for v1.

**Sharpened by the live test (2026-05-26):** even an *informative, non-imperative*
seed still got the worker to (correctly) refuse — because it asked the worker to
*run a command* (the reporting line), and a fresh session reasonably won't execute
an opaque script from an unrelated directory on a message's say-so alone. The
worker's verbatim reasoning: "the command writes outside this workspace… I have no
prior context establishing that PWC opened this session… running unknown code with
side effects, on someone else's say-so, isn't something I'll do without looking
first." That is *correct* security behavior we don't want to engineer around. So
the rule tightened: **the seed requests no action at all — it is pure task
context.** Reporting is human-initiated, not driven by the seed: run
`/pwc-report-status` from the coordinator to record where a task stands, or tell a
warmed-up worker to run it. (Skills are installed **globally** in `~/.claude/skills/`
precisely so a worker — which runs in a repo, not the workspace root — *can* resolve
`/pwc-report-status` once it's been asked; the constraint was never "the skill is
unavailable," it's "a cold worker won't run a command on a seed's say-so.") Also
note the launch mechanics that the same test fixed: spawn a normal
shell tab and *type* the `claude` launch into it (interactive claude needs a real
TTY; running it as the tab's `command=` program gave it none and the tab closed
instantly), and deliver the seed by typing it in after boot rather than as a
fragile shell-quoted argument.

## Forward-compatibility considerations (not v1 work)

Things we're not building, but are designing in a way that doesn't preclude:

- **Multi-PWC collaboration.** If two people both run PWC, their coordinators
  could share sanitized task-list views, send each other task requests (PR
  review, sync scheduling), and surface "I'm waiting on you" signals. Enabled
  by: structured-field separation, externalized task database, human-in-the-loop on
  dispatch. Not blocked by anything in the v1 design.
- **Background polling.** The coordinator could check sources continuously
  rather than on-demand. Enabled by: task database-as-truth design. Not building
  because the on-demand version is simpler and we don't know yet if the lag
  matters.
- **GUI / dashboard.** A small Textual TUI or web view over the task database would
  let me glance at task state without invoking the coordinator. Enabled
  by: the task database is a queryable SQLite store behind one CLI. Build only if
  the terminal interface turns out to
  be a real friction point.

## Cost-aware model routing: why the table is global, tokens are the record, and there are no fallbacks

PWC now picks the model for each task (`pwc route`) instead of defaulting everything
to claude. Five decisions in that build were non-obvious enough to record.

**Every model is priced at rack rate — subscriptions are NOT free.** The tempting
model was "claude/codex ride a subscription, so their marginal cost is zero, so prefer
them." That is exactly backwards for the goal. Pricing a subscription at zero makes the
router *maximize* the spend you're trying to evaluate, and hides whether the plan still
earns its price — you'd never learn you could drop from €180 to €20. So every model is
priced at its true API rate, the router sorts on that, and `pwc cost --report` shows
what each harness actually consumed. For subscription harnesses that figure is
*fair-value at rack rate* (what the tokens would cost on the open market, not money
billed) — which is precisely the number that answers "would a cheaper plan cover me?"
The report says so rather than implying it's an invoice.

**The goal is a PROVIDER PORTFOLIO, not the cheapest task.** This is the strategy the
router serves, and it is easy to mistake for simple cost-minimization (I have watched an
agent "helpfully" pin everything to the subscription harness because it felt free —
which is the exact failure the rack-rate rule above exists to prevent, arrived at from
the other direction). Two things follow that pure cost-optimization does not give you:

- **Concentration is a risk, not a saving.** Depending on one provider means its price
  hike, outage, policy change, or model regression is *your* outage. Spreading work
  across providers is worth paying something for. The router today optimizes cost alone
  and therefore naturally *concentrates* — it has no notion of diversity. That is a
  known gap, not a design choice.
- **The endgame is several CHEAP subscriptions, not one expensive one.** Five small
  plans across five providers beats a single €200 plan: cheaper in total, and no one
  vendor can hold the work hostage. The whole measurement apparatus exists to inform
  that purchasing decision — which plan to buy, which to drop, which workload to move
  to API pricing.

**The fair-value number is not readable yet, and saying it is would mislead.** `pwc cost`
will happily report (e.g.) $6,883 of fair-value against a €180 plan and invite the
conclusion "the subscription earns 38x its price." That conclusion is **worthless
today**, because the history it is computed from is the *old habit*: reaching for the
most expensive model by default, because the subscription made it feel free. It measures
waste, not need.

The number only becomes meaningful after a month or two of **deliberate routing** — once
each task has actually run on the model it needed. *Then* fair-value says what the work
genuinely costs, and that is the basis for deciding: upgrade this plan, drop that one,
move this workload to API pricing. Until then, quote it as "what we have spent so far
under the old habit", never as a verdict on a plan.

**Store tokens, derive dollars.** The first instinct was to record cost at task close.
But prices move constantly (the very first `models fetch` produced 44 changes), so a
stored dollar figure is welded to a stale table and history stops being reproducible —
you could never re-ask "what would last month have cost on DeepSeek?" Tokens keep that
question open forever, and they outlive the harnesses' own storage (transcripts get
pruned, opencode's sqlite gets vacuumed). Hence `task_usage` holds four token counts;
dollars are computed on the way out, against whatever price set you ask about.

**Cost is a property of the session, re-read live — not an event captured at close.**
A worker keeps spending after its task is marked done: the follow-up skill tweak, the
docs pass. Snapshotting at close would systematically under-count exactly the long tail
we're trying to measure. So `pwc cost --task X` re-reads and upserts every time, and
the number always reflects everything that session has done to date. `task_id` is
nullable for the same reason: the coordinator's own session and inline tasks burn real
tokens with no task attached, and a report that omitted them would understate the bill
it exists to produce.

**Cache reads are the bill, and pricing on input+output is wrong by ~250x.** A live PWC
worker session logged 32M cache-read tokens against 30k input. Priced naively as
in+out it looked like $3.30; priced correctly it was $24.34, of which $16 was cache
reads alone. Agentic sessions re-send their whole context every turn, so the cache
classes dominate. The table therefore carries four price columns, not two, and the
router's ranking blends them by a stated (assumed) token mix.

**"Capable enough" and "allowed to see real data" are different questions.** We shipped
the collapse of these two and caught it in testing: a `prod-data` task routed straight
to a third-party metered API because it cleared the capability floor. Capability is a
tier; trust is a separate declared flag. `--risk prod-data` now filters on both. The
lesson generalizes — a privacy constraint wearing a cost costume will always get
optimized away.

**No fallback chains, on purpose.** If nothing qualifies, `route` exits nonzero and
says what filtered everything out. The alternative — silently downgrading to a model
already judged unfit — is how you get a "cheap" run that quietly produces garbage on a
task that needed care. A failed dispatch is a decision for the user, not something for
the router to paper over. Same reasoning as the `available` hard filter: a model whose
harness isn't authenticated would fail at spawn, so it must never be *picked*, rather
than being picked and then falling back.

**The overlay is structural, not a promise.** Capability tiers are the user's judgment;
costs are the vendor's fact. Keeping corrections in a separate top-level `overlay`
object (rather than as fields inside the rows) is what makes "your calibration survives
a refresh" true by construction: `fetch` rewrites `models[]` wholesale and literally
cannot touch the overlay. Verified: rate a model down, refresh, the rating holds.

**Preference is a third input, not a bent price.** The top-level `preferences` block
records user taste per domain. A stable task-ID bucket applies its 0-100% target share
after all hard filters and the capability gate. Tasks outside that share use cost
order. This produces a real mix while keeping objective prices and capability tiers
truthful. An optional RFC3339 expiry stops temporary preferences automatically.
