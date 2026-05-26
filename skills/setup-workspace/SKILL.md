---
name: setup-workspace
description: Onboard a workspace to PWC — figure out which external sources of work apply here (Jira, GitHub, Slack, email) and how to query each, then write the per-workspace sources config that /find-work uses. Run this once per workspace, or again to change what /find-work scans.
---

# /setup-workspace

Set up (or reconfigure) what work PWC pulls into *this* workspace. Different
workspaces draw work from different places — one is Jira + GitHub + Slack, another
is just GitHub, another only local notes — so the sources `/find-work` scans are
configured per workspace, not hardcoded. This command figures out what applies
here, with you, and writes the config.

The result is `<workspace>/.pwc/sources.json`. `/find-work` reads it; nothing else
depends on the exact contents, so it's safe to re-run anytime to adjust.

## Configuration

- **Scripts directory**: `~/work/pwc/scripts` (`$SCRIPTS`).
- **Workspace**: the current directory; config written to `<workspace>/.pwc/sources.json`.

## Tools

- `python3 $SCRIPTS/sources.py show` — current config (empty if none yet).
- `python3 $SCRIPTS/sources.py set --json -` — write the config (JSON on stdin).
- **Detection** (read-only, to suggest sensible defaults): `gh auth status` and
  `gh repo list <org>` for GitHub; the Atlassian MCP tools for Jira projects; the
  Slack tools for channels; check whether a Gmail/email integration is connected.

## Steps

1. **Show the current config** with `sources.py show` so the user sees what's
   already set (if re-running).

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

4. **Write the config** by piping the assembled JSON to `sources.py set --json -`.
   The shape is `{"sources": {"<name>": {"enabled": <bool>, ...params}}}`. Heed any
   validation warnings it prints (e.g. an enabled source missing a required field)
   and fix them with the user before finishing.

5. **Confirm** what was written and tell the user they can now run `/find-work` to
   scan these sources, and re-run `/setup-workspace` anytime to change them.

## Notes

- This only configures **what to scan** — it doesn't fetch work itself. `/find-work`
  does the scanning and surfaces candidates (which the user confirms before anything
  is queued). Setup and finding are separate steps.
- Detection is best-effort. If a source can't be auto-detected (e.g. an MCP isn't
  connected this session), just ask the user for the values directly.
- The config is plain JSON in `.pwc/` — it can be hand-edited too, but `sources.py`
  is the canonical path (it validates the shape).
