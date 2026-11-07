---
name: pwc-done
description: A worker's closing step. Records the final outcome on its PWC task, closes the paired tracker issue, marks the task done, then ends the session and closes its iTerm2 tab. Run by a worker (any harness) when its work is finished, committed and pushed.
---

# /pwc-done

Run this **in a worker session, once the work is finished**. It replaces the manual
`/pwc-report-status` done + closing the issue + closing the tab. Closing the tab is the
last thing it does, and only if everything before it worked.

Not for the coordinator, and not for blocked or paused work: use `/pwc-report-status`
(`blocked` / `note`) for those. A closed tab is not coming back.

## Steps

1. **Finish the work first.** Commit and push per the repo's own `AGENTS.md`. `pwc done`
   refuses to close while the tree is dirty or commits are unpushed.
2. **Do the outward steps the CLI cannot.** Anything `/pwc-report-status` asks on `done`
   that needs a tool of yours: Slack reactions on the task's Slack refs (step 4a there),
   and a Jira transition if the task pairs with a Jira key (the CLI only closes GitHub
   issues). Ask the one model-rating question (step 7 there) now; the tab is about to go.
3. **Run it.** The task id is in your seed prompt. Do not rely on session lookup:
   spawn does not always record session ids (shreyansqt/pwc#19).
   ```
   pwc done --task <id> --outcome "<one line: what shipped / was decided>" \
     [--ref pr=<url>] [--ref commit=<sha>]
   ```
   `--dry-run` checks and reports without changing anything. `--no-close-tab` records
   everything but keeps the session.
4. **Read the JSON.** It lists each tracker issue (`closed` / `already-closed`), the
   status, and the tab (`scheduled in 4s`, or `skipped: ...` for a remote `--ssh`
   worker or a non-iTerm2 terminal, where you tell Shreyans to close it himself).
5. **Say goodbye in one line, then stop.** The tab closes a few seconds later and takes
   the session with it. Don't start more work.

## If it fails

It exits non-zero before touching the tab, so you are still alive to fix it:
- *not safely recorded*: commit and push, then rerun.
- *cannot close org/repo#N*: the issue state is unknown; check `gh auth status`, fix, rerun
  (closing an already-closed issue is a no-op).
- Never work around it with `--skip-git-check` unless the uncommitted files are
  deliberately not yours to commit.

## How it works

`pwc done` (scripts/done.py): resolve task, git preflight, add `--ref`s, close each
`github-issue` ref with the outcome as the comment, `log-event --kind done
--set-status done`, then a detached process closes the tab matched by
`$ITERM_SESSION_ID` (else tty), strictly, never the focused tab. Every harness
(claude, codex, opencode) ends when its tab does.
