---
name: pwc-setup-workspace
description: Onboard a workspace to PWC — figure out which external sources of work apply here (Jira, GitHub, Slack, email) and how to query each, then write the per-workspace sources config that /pwc-find-work uses. Run this once per workspace, or again to change what /pwc-find-work scans.
---

# /pwc-setup-workspace

Set up (or reconfigure) what work PWC pulls into *this* workspace. Different
workspaces draw work from different places — one is Jira + GitHub + Slack, another
is just GitHub, another only local notes — so the sources `/pwc-find-work` scans are
configured per workspace, not hardcoded. This command figures out what applies
here, with you, and writes the config.

The result is `<workspace>/.pwc/sources.json`. `/pwc-find-work` reads it; nothing else
depends on the exact contents, so it's safe to re-run anytime to adjust.

## Configuration

- **Scripts directory**: `~/work/pwc/scripts` (`$SCRIPTS`).
- **Workspace**: the current directory; config written to `<workspace>/.pwc/sources.json`.

## Tools

- `python3 $SCRIPTS/sources.py show` — current config (empty if none yet).
- `python3 $SCRIPTS/sources.py set --json -` — write the config (JSON on stdin).
  **Note:** `set` replaces the *whole* config, so include `"mode"` in the JSON you
  write (step 6) or it reverts to the default. Use `set-mode` only to flip an
  existing workspace's mode without rewriting everything else.
- `python3 $SCRIPTS/sources.py mode` / `set-mode --mode <iterm2|desktop>` — the
  worker-launch mode (how `/pwc-start-work` dispatches and how `/pwc-show-work`
  checks liveness). See step 1b.
- **Detection** (read-only, to suggest sensible defaults): `gh auth status` and
  `gh repo list <org>` for GitHub; the Atlassian MCP tools for Jira projects; the
  Slack tools for channels; check whether a Gmail/email integration is connected.

## Steps

1. **Show the current config** with `sources.py show` so the user sees what's
   already set (if re-running).

1b. **Ask which worker-launch mode this setup uses — iTerm2 or Claude Desktop.**
   This decides how `/pwc-start-work` dispatches and how `/pwc-show-work` checks
   workers, so it's set once at onboarding:
   - **`iterm2`** (default) — the user has iTerm2 with the Python API enabled.
     `/pwc-start-work` spawns a worker in a new tab and types the seed in;
     `/pwc-show-work` checks worker liveness via `pgrep`. This is the original model.
   - **`desktop`** — the user is on the **Claude Desktop app** with **no terminal /
     no iTerm2** (e.g. Stella). `/pwc-start-work` can't spawn a tab, so it **hands the
     user the seed** (the working directory + the seed copied to the clipboard) and
     the user opens a new session themselves in the Desktop app's code section (which
     sees the global skills) and pastes it. `/pwc-show-work` **skips the `pgrep`
     liveness sweep** (a Desktop worker isn't a local process) and asks the user for
     status where it matters.
   Carry the chosen value into the JSON you write in step 6 as a top-level
   `"mode": "<iterm2|desktop>"`. (To change it later without re-running full setup,
   `sources.py set-mode --mode <…>`.)

2. **Detect what's available** — don't make the user recall everything. Probe
   read-only:
   - GitHub: `gh auth status` (is it authed? as whom?), and the org(s) they work in.
   - Jira: list accessible projects via the Atlassian MCP, and who "currentUser" is.
   - Slack: which channels they're in / care about.
   - Email: is an account connected?

3. **Ask, source by source**, what to enable and the key parameters. Keep it short —
   propose detected defaults, let the user confirm or adjust:
   - **Jira** — which project key? what JQL defines "my work" (default:
     `assignee = currentUser() AND statusCategory != Done`)?
   - **GitHub** — which org? watch for review-requests, assigned issues, or both?
   - **Slack** — which channels to scan for mentions/threads worth tracking?
   - **Email** — enable? which account/labels signal actionable mail?
   Sources the user doesn't want are recorded with `"enabled": false` (so it's
   clear they were considered and declined, and easy to turn on later).

4. **Confirm the id conventions** — how a new task's *id* is derived from each
   source. These are meaningful ids, not numbers. Propose sensible defaults and let
   the user adjust:
   - **Jira** → `"jira-key"` (use the ticket key itself, e.g. `SMT-874`). This is
     almost always what you want for Jira.
   - **GitHub / Slack / email / etc.** → `"<prefix>-slug"` — a prefix plus a slug of
     the title (e.g. `slack-deploy-window`, `email-invoice-priya`). Confirm the
     prefix per source (defaults: the source name).
   - **General fallback** (`id_fallback`, top-level) → for tasks that span multiple
     sources or have none. Default `"task-slug"` (a plain title slug, `task-` prefix
     optional). Confirm one value.
   Store each source's choice as `"id_convention"` on that source, and the fallback
   as a top-level `"id_fallback"`.

5. **Configure skill hints** — which existing skills map to which kinds of task, so
   `/pwc-start-work` can *suggest* the right skill in a worker's seed (instead of the
   user imposing it later). **Scan the available skills first** — list the skill
   directories (e.g. the team-skills repo, or `~/.claude/skills/`) and read each
   `SKILL.md`'s `description` — then propose a `task-type → skill(s)` map and let the
   user confirm/adjust. Map by what the task *is*, e.g.:
   - a PR-review task → `code-review` (+ `request-review` to announce)
   - starting a Jira ticket (build work) → `start-ticket`; needing a new ticket → `create-ticket`
   - a task whose output is a Slack message → `slack-message`
   - investigation / data lookup → `db-query`, `service-cli`
   - and any one-off domain skills present (release, delete-customer, trigger-job, …)
   Store as a top-level `"skill_hints"` object: `{"<task-type-or-signal>": ["<skill>", …]}`.
   Keep it to skills that actually exist in the scan. This is optional — skip if the
   user has no skills set up.

6. **Write the config** by piping the assembled JSON to `sources.py set --json -`.
   The shape is
   `{"mode": "<iterm2|desktop>", "sources": {"<name>": {"enabled": <bool>, "id_convention": "...", ...params}}, "id_fallback": "task-slug", "skill_hints": {...}}`.
   Include the `mode` from step 1b (`set` replaces the whole file, so omitting it
   reverts to the default `iterm2`).
   Heed any validation warnings it prints (e.g. an enabled source missing a required
   field) and fix them with the user before finishing.

7. **Confirm** what was written and tell the user they can now run `/pwc-find-work` to
   scan these sources, and re-run `/pwc-setup-workspace` anytime to change them (incl.
   the skill hints, as new skills get added).

## Notes

- This only configures **what to scan** — it doesn't fetch work itself. `/pwc-find-work`
  does the scanning and surfaces candidates (which the user confirms before anything
  is queued). Setup and finding are separate steps.
- Detection is best-effort. If a source can't be auto-detected (e.g. an MCP isn't
  connected this session), just ask the user for the values directly.
- The config is plain JSON in `.pwc/` — it can be hand-edited too, but `sources.py`
  is the canonical path (it validates the shape).
