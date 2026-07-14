# PWC Hub — cloud control plane (design proposal)

*Status: proposal, 2026-07-10. Captures the architecture discussion before any hub
code exists. Style follows `docs/design-notes.md`: decisions with the reasoning,
including the alternatives that lost. Once accepted, entries graduate into
design-notes as they're built.*

---

## What changed: the requirements that retire "no infra"

PWC v1 deliberately had no infrastructure: one SQLite file per workspace, all
state on one laptop, nothing to operate. Four requirements arrived together and
retire that posture:

1. **Most work should run on remote machines** (always-on Mac mini first) — the
   laptop becomes primarily a coordination surface. Remote workers exist as of
   2026-07-10 (SSH + tmux dispatch), but they can't write to a task database
   locked inside one laptop's filesystem.
2. **The task database must be backed up at all times**, and work must survive
   any single machine being unplugged, lost, or powered down — including the
   laptop, which today holds the only copy.
3. **Coordinating from a phone** is a real future, not a fantasy. Any mobile
   surface needs an API reachable from anywhere.
4. **No always-on VPN on the laptop.** If every briefing needs Tailscale up,
   the design fights daily habits and loses.

A control plane with these properties is a small cloud service. The execution
plane (worker processes, tmux sessions, transcripts) stays on machines — that
part is irreducibly physical.

## Decision 1: the workspace stays the unit; the *config* decides where it lives

PWC's decentralization was never really "one file per workspace" — it's
"workspaces never mix, and each declares its own wiring in `.pwc/`". That
property survives unchanged. Each workspace's config declares its store:

```jsonc
{"store": "local"}                                     // default, forever supported
{"store": "hub",
 "url": "https://pwc-hub.<account>.workers.dev",
 "workspace": "smarta",
 "token_ref": "op://…"}                                // cloud-backed
```

Two workspaces may point at the same hub deployment (smarta + side-projects as
logical workspaces in one hub) or at different deployments entirely (an
employer's hub on their account, a personal one on yours). **PWC never decides
central-vs-decentralized; the workspace file does** — the same posture sources
config has today.

## Decision 2: one Worker + one D1 database, workspace as a column

Inside a hub deployment: a single Cloudflare Worker in front of a single D1
database, every table carrying a `workspace` column. Chosen over
database-per-workspace and Durable-Object-per-workspace because:

- **D1 is SQLite.** Today's `schema.sql` and queries port nearly verbatim, and
  local mode vs hub mode stay dialect-identical — one mental model, two homes.
- **The backup requirement is handled by the platform.** D1 ships Time Travel
  (point-in-time restore, 30 days) and first-class `wrangler d1 export`;
  a scheduled export to R2 adds belt-and-suspenders. "Cannot be deleted by
  someone tripping over a cable" becomes a property, not a procedure.
- **Cross-workspace reads are one query** — "everything on my plate, all
  workspaces" is exactly what a phone view wants.

DO-per-workspace was seriously considered (it mirrors the file-per-workspace
model 1:1, and DO SQLite is familiar territory); it lost on backup/export
maturity, which was the hardest requirement. Isolation inside a hub stays
logical (workspace-scoped queries); per-workspace tokens can be added if a
workspace is ever shared with a team.

## Decision 3: public template, private instance

The public repo stays usable by anyone, with no personal residue:

- **Public (this repo):** a new `hub/` directory — Worker source, D1 schema
  migrations, and a `wrangler.jsonc` *template*. That is the
  infrastructure-as-code: anyone runs `wrangler d1 create pwc && wrangler
  deploy` into their own Cloudflare account and owns their hub.
- **Private (companion repo):** the instance config — real account id, database
  id, custom domain, R2 backup export wiring. Same template/instance split as a
  `deployment-secrets` repo.
- **Secrets in neither repo:** the API token lives in `wrangler secret` on the
  deployment side and in a password manager (`token_ref`) on each machine.
  Workspace `.pwc/` state remains untracked, as it already is.

**Anti-lock-in guarantee:** `pwc export` / `pwc import` work in both directions
between local and hub. That keeps the public project honest (leave the cloud
with your data in one command) and doubles as the day-one migration tool.

## Decision 4: a storage-driver seam in the CLI; skills change zero

Everything already funnels through the `pwc` CLI — that chokepoint is the whole
migration surface. It grows two drivers behind one interface:

- **`local`** — today's sqlite3 code, unchanged, stdlib-only, the default.
- **`hub`** — the same operations as HTTPS calls (urllib, still zero deps),
  mirroring the taskdb subcommands 1:1.

Skills keep calling `pwc summary` / `pwc log-event` / `pwc detail`, oblivious to
the backend. Accepted cost: the operation logic exists twice (Python locally,
TypeScript in the Worker). The operations are small and stable; duplication
beats shared-engine cleverness.

**Offline behavior — v1 is online-only (decided 2026-07-11).** An unreachable
hub is a clean error, not silent divergence. The read-cache ("stale as of
HH:MM" board) and write-spool (queue offline, replay on reconnect) remain the
designed extension, added behind the same driver seam if offline coordination
turns out to matter in practice.

## Decision 5: roles symmetrize; only dispatch stays machine-to-machine

With the hub in place:

- **Coordinator** = any machine with the token + skills (laptop, the mini over
  SSH, eventually a phone through the same API).
- **Worker** = any machine; it reports status over HTTPS to the hub. No VPN, no
  outbox, no reverse-SSH. The earlier outbox design dissolves — it survives only
  as the offline write spool.
- **Liveness inverts from pull to push (phase 2):** each runhost gets a dumb
  scheduled job that pgreps its own active sessions and POSTs heartbeats. The
  board then knows worker liveness from anywhere, VPN-free.
- **Dispatch and attach** remain SSH+Tailscale — starting or viewing a process
  on a specific machine is irreducibly a network operation to that machine. But
  it's needed only at those moments, and `pwc spawn` can check-and-raise
  Tailscale on demand. No always-on VPN.

**What stays physical:** a worker's transcript and tmux session live on the
machine that ran it, so resume happens where the work started. `runhost` remains
a task field in the fully-cloud world for exactly this reason.

## Rejected: synced files / an Obsidian vault as the store

Considered seriously (it would also solve artifact exchange) and rejected:

- Sync engines are **last-writer-wins per file**; concurrent writes (coordinator
  reprioritizes while a worker appends status) produce conflict copies or silent
  loss. Designing around that — one file per writer, append-only per-host event
  files — just re-derives the outbox pattern *inside* a sync engine, enforced by
  convention instead of by a transaction. Agents drift from conventions.
- It re-loses the transactionality that moved PWC from files to SQLite in the
  first place (see design-notes, "Why the store is SQLite").
- The genuinely attractive part — the board on a phone via existing Obsidian
  sync — is captured conflict-free by a **one-way projection**: `show-work`
  renders a read-only `PWC board.md` into the vault. Window, not database.

Also rejected: canonical DB on the mini with SSH tunneling (makes the *common*
operation depend on the VPN and keeps a single physical point of failure);
SQLite replication engines like litestream-as-primary/libSQL/cr-sqlite
(machinery for write conflicts the single-writer-rows + append-only-events
schema doesn't produce).

## Artifact exchange (related, separate channel)

Files a remote worker produces stay out of the database and out of the personal
vault. The `_playground/<task>` convention holds on whichever machine ran the
worker, recorded as a working ref; the coordinator fetches on demand
(`pwc pull <task>` → rsync over the existing SSH path). A synced `_shared/`
directory (Syncthing) is the escalation if on-demand pulling proves to be
friction.

## Scope (trimmed 2026-07-11, build started)

**In:** `hub/` Worker + D1 schema (public template); private instance repo
(`pwc-infra`); `hub` driver in `pwc` (online-only, store.json per workspace);
`pwc export`/`import` in both backends; conformance test; migrate the smarta
workspace; install the CLI + token on the mini so remote workers report over
HTTPS.

**Out (cut or deferred, with reasons):**
- *Backup cron for the local databases* — cut: superseded by the migration
  itself (D1 Time Travel + optional R2 export). Accepted risk: until cutover,
  the task database remains a single local copy.
- *Obsidian board projection* — cut: it was only ever a phone-glance stopgap;
  the hub API is the real mobile story.
- *Read-cache + write-spool* — deferred (see offline behavior above).
- *Runhost heartbeats, `pwc pull`, mobile surface* — later phases, unchanged.

## Decisions settled during build (2026-07-11)

- **API shape:** `POST /w/<workspace>/<op>` mirroring the taskdb subcommands
  1:1, request body = the argparse fields, response = byte-compatible with the
  local `emit()` output. The Python client (`hub_client.py`) is a dumb
  passthrough; all semantics live in the Worker.
- **Token model:** one bearer token per deployment to start (client copy at
  `~/.config/pwc/hub-token`, chmod 600; deployment copy via `wrangler secret`);
  per-workspace tokens only if a workspace is ever shared.
- **Store config:** `<workspace>/.pwc/store.json` — separate from sources.json
  (task-store wiring vs find-work config). Absent file = local, forever.
- **Import safety:** `/import` (and local `pwc import`) refuse non-empty
  targets — migration can't silently merge into existing data.
- **Deploy auth:** the laptop's default wrangler login is the smarta WORK
  account; the hub deploys with a personal-account API token via
  `CLOUDFLARE_API_TOKEN`, never by switching the default login.

## Decision 6: the coordinator can stand ABOVE the workspaces (2026-07-14)

Decision 2 promised "cross-workspace reads are one query — 'everything on my plate,
all workspaces'". The schema delivered it (`workspace` is a real column, both boards
already share one D1 database), but nothing ever *called* it that way: every route is
`POST /w/:workspace/:op`, every op scopes to one workspace, and CLI discovery only ever
walked UP from the cwd. So standing in `~/work` — the natural place to coordinate
across `smarta/` and `side-projects/` — resolved to nothing and died with a stale
"no task database, run init" (stale twice over: post-hub-migration there is no local
`taskdb.db` in either workspace, so following it would create a database PWC ignores).

**Discovery now also looks DOWN, one level.** A directory holding several workspaces is
a supported vantage point, not an error.

**Reads fan out and merge; writes pin to one board.** The asymmetry is the whole
design:

- A **read** from a parent sweeps every workspace and tags each row with its
  `workspace`. That's the combined board.
- A **write** belongs to exactly one board. Naming an existing task resolves its
  workspace automatically. Naming one that exists in **more than one** workspace
  **refuses** and demands `--workspace`. Creating a task from a parent requires
  `--workspace` outright — a new id exists nowhere yet, so there is nothing to infer.

The refusal is not defensive programming, it's a bug that happened: task ids are unique
*within* a workspace and nothing ever enforced uniqueness *across* them.
`pwc-routing-engine` ended up on **both** boards — the same work queued twice, because
the coordinator was cd'd into `smarta` when the work was a side-project. Guessing a
board would silently mutate the wrong one.

**Rendering groups by workspace and never interleaves.** Priority does not mean the
same thing on every board: smarta runs a strict queue order (1,2,3,4,5, two-way-synced
to a Jira board rank), side-projects runs tiers (twelve tasks at `p2` across five
unrelated projects). Sorting the union by priority number would invent a cross-board
ranking the user never made.

**Client-side fan-out, not a hub route.** `POST /all/summary` (drop the `WHERE
workspace = ?`) is one query and is what a future phone client wants — but it only
serves hub-backed workspaces, needs a deploy, and duplicates what the CLI can do for
free. The client sweep works for local *and* hub workspaces, needed no Worker change,
and N is 2. The hub route stays available for the mobile surface, where the round-trip
actually matters.

**Fallout fixed on the way:** `cost.py` resolved its `usage.db` through
`find_workspace_root()`, which falls back to the CWD when it finds no marker — so a
cost read from a non-workspace directory *created* `.pwc/` there to hold it. The failed
`pwc summary` in `~/work` had already minted `~/work/.pwc/`, which then made `~/work`
look like a workspace and masked the two real ones below it. A workspace is now defined
as a directory with a **task store** (`taskdb.db` or `store.json`), not merely a
`.pwc/`; and cost refuses to mint one where no workspace exists.

## Open questions (still open)

- D1 latency from Europe in practice; batch the briefing reads if it drags.
- Whether event uuids (for idempotent spool replay) go in now-dormant columns
  or wait for the spool itself.
