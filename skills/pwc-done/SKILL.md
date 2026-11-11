---
name: pwc-done
description: A worker's closing step, for finished work (done) or work you are stopping on (blocked/paused). Use whenever Shreyans wants to wrap up the session and close the tab. Records the outcome on its PWC task, closes the paired tracker issue (done only), then ends the session and closes the tab (Orca or iTerm2).
---

# /pwc-done

Run this **in a worker session, once the work is finished**. It replaces the manual
`/pwc-report-status` done + closing the issue + closing the tab. Closing the tab is the
last thing it does, and only if everything before it worked.

Not for the coordinator. Two end states:
- **Finished:** the default. Marks the task `done` and closes the tracker issue.
- **Stopping without finishing** (blocked on someone, paused, reached a conclusion that
  needs a decision): add `--blocked` and put what it waits on in `--outcome`. Marks the
  task `blocked`, leaves the tracker issue open, still closes the tab. If you only want
  to log status and keep the session, use `/pwc-report-status` instead.

A closed tab is not coming back.

## Steps

1. **Finish the work first.** Commit and push per the repo's own `AGENTS.md`. `pwc done`
   refuses to close while the tree is dirty or commits are unpushed.
2. **Do the outward steps the CLI cannot.** Anything `/pwc-report-status` asks on `done`
   that needs a tool of yours: Slack reactions on the task's Slack refs (step 4a there),
   and a Jira transition if the task pairs with a Jira key (the CLI only closes GitHub
   issues).
3. **Run it.** The task id is in your seed prompt. Do not rely on session lookup:
   spawn does not always record session ids (shreyansqt/pwc#19).
   ```
   pwc done --task <id> --outcome "<one line: what shipped / was decided>" \
     [--ref pr=<url>] [--ref commit=<sha>]
   ```
   For blocked/paused: `pwc done --task <id> --blocked --outcome "waiting on <X>"`.
   `--dry-run` checks and reports without changing anything. `--no-close-tab` records
   everything but keeps the session.
4. **Read the JSON.** It lists each tracker issue (`closed` / `already-closed`), the
   status, and the tab (`scheduled in 4s`, or `skipped: ...` for a remote `--ssh`
   worker or a terminal that is neither Orca nor iTerm2, where you tell Shreyans to close it himself).
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
