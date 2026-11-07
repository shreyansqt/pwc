#!/usr/bin/env python3
"""`pwc done` — finish a worker's task and close its session.

A worker runs this (through `/pwc-done`) when its work is finished. In order:

  1. resolve the task (--task, else this session's id -> `pwc find-session`)
  2. preflight: the work is committed and pushed (skip with --skip-git-check)
  3. attach final refs (--ref TYPE=VALUE, repeatable)
  4. close the paired tracker issue (a github-issue ref), with the outcome as the
     closing comment — both sides of a paired task must move
  5. log the final `done` event and set status done
  6. close the iTerm2 tab, which ends the session (every harness dies with its tab)

Closing is LAST. Any failure in 1-5 exits non-zero before anything irreversible
happens to the tab, and the worker stays alive to fix it. Step 5 follows step 4 so a
failed `gh` call never leaves a task marked done with its issue still open; the
reverse (issue closed, task not done) is retried safely, since closing is idempotent.

The tab is found by $ITERM_SESSION_ID, else by tty. It is closed by a detached
process after --delay seconds, so the worker can still print its last message.
Spawn does not record session ids on tasks (shreyansqt/pwc#19), so --task is the
normal path; session lookup is only the fallback.

Usage:
  pwc done --task ID --outcome "what shipped" [--ref TYPE=VALUE ...]
  pwc done --task ID --blocked --outcome "waiting on X / paused, resume later"
           [--skip-git-check] [--no-close-tab] [--delay N] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

PWC = str(Path(__file__).resolve().parent.parent / "bin" / "pwc")
ISSUE_RE = re.compile(r"^(?P<repo>[\w.-]+/[\w.-]+)#(?P<num>\d+)$")


def fail(msg: str) -> None:
    print(f"pwc done: {msg}", file=sys.stderr)
    raise SystemExit(2)


def pwc(args: list[str], workspace: str | None, check: bool = True) -> str:
    cmd = [PWC] + (["--workspace", workspace] if workspace else []) + args
    out = subprocess.run(cmd, capture_output=True, text=True)
    if check and out.returncode != 0:
        fail(f"`pwc {' '.join(args[:2])}` failed: {(out.stderr or out.stdout).strip()}")
    return out.stdout


def session_id_from_argv() -> str | None:
    """Walk up the process tree for a `--session-id <uuid>` / `--resume <uuid>` arg."""
    pid = str(os.getppid())
    for _ in range(12):
        out = subprocess.run(["ps", "-o", "ppid=,command=", "-p", pid],
                             capture_output=True, text=True).stdout.strip()
        if not out:
            return None
        ppid, _, command = out.partition(" ")
        m = re.search(r"--(?:session-id|resume)[ =]([0-9a-f-]{36})", command)
        if m:
            return m.group(1)
        pid = ppid.strip()
    return None


def resolve_task(task: str | None, workspace: str | None) -> str:
    if task:
        return task
    sid = session_id_from_argv()
    if not sid:
        fail("no --task given and no session id found in the process tree")
    row = json.loads(pwc(["find-session", "--session-id", sid], workspace) or "null")
    if not row:
        fail(f"no task carries session {sid}; pass --task (see shreyansqt/pwc#19)")
    return row["id"]


def git_preflight(cwd: str) -> list[str]:
    """Problems that mean the work is not safely recorded. Empty = fine."""
    def git(*a):
        return subprocess.run(["git", "-C", cwd, *a], capture_output=True, text=True)
    if git("rev-parse", "--is-inside-work-tree").returncode != 0:
        return []  # not a repo: nothing to commit
    problems = []
    if git("status", "--porcelain").stdout.strip():
        problems.append("uncommitted changes in the working tree")
    ahead = git("rev-list", "--count", "@{u}..HEAD")
    if ahead.returncode != 0:
        branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        problems.append(f"branch {branch!r} has no upstream (never pushed)")
    elif int(ahead.stdout.strip() or 0) > 0:
        problems.append(f"{ahead.stdout.strip()} commit(s) not pushed")
    return problems


def tracker_issues(detail: dict) -> list[tuple[str, str]]:
    out = []
    for ref in detail.get("refs", []):
        if ref.get("ref_type") == "github-issue":
            m = ISSUE_RE.match(ref["value"])
            if m:
                out.append((m["repo"], m["num"]))
    return out


def close_issue(repo: str, num: str, outcome: str) -> str:
    view = subprocess.run(["gh", "issue", "view", num, "--repo", repo, "--json", "state"],
                          capture_output=True, text=True)
    if view.returncode != 0:
        fail(f"cannot read {repo}#{num}: {view.stderr.strip()}")
    if json.loads(view.stdout)["state"] == "CLOSED":
        return "already-closed"
    res = subprocess.run(["gh", "issue", "close", num, "--repo", repo,
                          "--comment", outcome], capture_output=True, text=True)
    if res.returncode != 0:
        fail(f"cannot close {repo}#{num}: {res.stderr.strip()}")
    return "closed"


def close_tab(tty: str | None, delay: float) -> str:
    """Hand off to a detached process that closes this session's tab after `delay`."""
    if not os.environ.get("ITERM_SESSION_ID") and not tty:
        return "skipped: not in an iTerm2 tab (remote --ssh worker, or another terminal)"
    subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--_close-tab",
         os.environ.get("ITERM_SESSION_ID", ""), tty or "", str(delay)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    return f"scheduled in {delay:g}s"


def _close_tab_now(session_env: str, tty: str, delay: float) -> None:
    """Detached half. Strict: closes only a tab positively matched by session id or
    tty, never the focused one."""
    import time
    time.sleep(delay)
    try:
        import iterm2
    except ImportError:
        return
    guid = session_env.split(":", 1)[-1] if session_env else ""

    async def _main(connection):
        app = await iterm2.async_get_app(connection)
        for window in app.terminal_windows:
            for tab in window.tabs or []:
                for session in tab.sessions or []:
                    hit = bool(guid) and session.session_id == guid
                    if not hit and tty:
                        try:
                            hit = await session.async_get_variable("tty") == tty
                        except Exception:  # noqa: BLE001
                            hit = False
                    if hit:
                        await tab.async_close(force=True)
                        return

    try:
        iterm2.run_until_complete(_main)
    except Exception:  # noqa: BLE001 — nothing left to report to
        pass


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--_close-tab"]:
        _close_tab_now(argv[1], argv[2], float(argv[3]))
        return 0

    ap = argparse.ArgumentParser(prog="pwc done", description=__doc__.split("\n\n")[0])
    ap.add_argument("--task")
    ap.add_argument("--workspace")
    ap.add_argument("--outcome", required=True, help="one line: what shipped / was decided")
    ap.add_argument("--ref", action="append", default=[], metavar="TYPE=VALUE",
                    help="final working ref to attach, e.g. commit=abc123 (repeatable)")
    ap.add_argument("--blocked", action="store_true",
                    help="end the session without finishing: status blocked, tracker issue "
                         "left open, --outcome says what it is waiting on / why paused")
    ap.add_argument("--skip-git-check", action="store_true")
    ap.add_argument("--no-close-tab", action="store_true", help="record only; keep the session")
    ap.add_argument("--delay", type=float, default=4.0, help="seconds before the tab closes")
    ap.add_argument("--dry-run", action="store_true", help="check and report; change nothing")
    args = ap.parse_args(argv)

    ws = args.workspace
    task = resolve_task(args.task, ws)
    detail = json.loads(pwc(["detail", "--task", task], ws))
    if detail["task"]["status"] == "done":
        print(f"pwc done: {task} is already done; skipping to tab close", file=sys.stderr)

    if not args.skip_git_check:
        problems = git_preflight(os.getcwd())
        if problems:
            fail("work is not safely recorded, so not closing: " + "; ".join(problems)
                 + " (commit/push, or pass --skip-git-check)")

    issues = [] if args.blocked else tracker_issues(detail)
    report = {"task": task, "issues": {f"{r}#{n}": "would-close" for r, n in issues}}
    if args.dry_run:
        report["tab"] = "dry-run"
        print(json.dumps(report, indent=2))
        return 0

    for spec in args.ref:
        rtype, _, value = spec.partition("=")
        if not value:
            fail(f"--ref wants TYPE=VALUE, got {spec!r}")
        pwc(["add-ref", "--task", task, "--kind", "working", "--ref-type", rtype,
             "--value", value], ws)
    for repo, num in issues:
        report["issues"][f"{repo}#{num}"] = close_issue(repo, num, args.outcome)

    status = "blocked" if args.blocked else "done"
    pwc(["log-event", "--task", task, "--source", "worker", "--kind", status,
         "--detail", args.outcome, "--set-status", status], ws)
    report["status"] = status

    if args.no_close_tab:
        report["tab"] = "kept (--no-close-tab)"
    else:
        from retitle import parent_tty
        report["tab"] = close_tab(parent_tty(), args.delay)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
