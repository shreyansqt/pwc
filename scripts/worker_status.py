#!/usr/bin/env python3
"""Worker-status check — is a worker session still running?

A worker is spawned with its session id in the process argv (`claude --session-id
<uuid>`, `opencode --session <ses_…>`, `codex resume <uuid>`), so `pgrep -f <id>`
is an exact "is this session still running?" test — no PID storage, no polling,
terminal-agnostic. ("Worker status" = alive/dead here, distinct from a task's
status field.)

REMOTE workers (task has a runhost): the process lives on another machine, so the
check hops over ssh — pass `ssh` per entry in the --json input. An UNREACHABLE
host is NOT a dead worker (the worker is probably fine; your network isn't):
those come back `alive: null, unreachable: true` and must not be swept.

Used by /show-work: for each `in-progress` task with a session_id, check worker
status here; for the dead ones, detach the session (`pwc clear-session`) and
leave the task `in-progress` (resumable). This detects death, not outcome — a dead
worker may have left finished-but-unpushed work, so never infer done/failed.

Usage:
  worker_status.py --session-ids <uuid,uuid,...>
  worker_status.py --json -   # [{"task": "...", "session_id": "...", "ssh": "host"?}] on stdin

Output (stdout, JSON): a list of {session_id, alive[, task][, unreachable]}.
"""

from __future__ import annotations

import argparse
import subprocess
import sys

from _common import emit, fail, read_json_stdin

_SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]


def is_alive(session_id: str, ssh_target: str | None = None) -> bool | None:
    """True/False if a process with this session id in its argv is running
    (locally, or on `ssh_target` if given). None = remote host unreachable —
    liveness UNKNOWN, not dead.

    pgrep exits 0 if a match is found, 1 if none, >1 on error. The session id is a
    uuid/ses_-id, so a substring match (`-f`) is specific enough to not collide.
    """
    if not session_id:
        return False
    if ssh_target:
        try:
            result = subprocess.run(
                ["ssh", *_SSH_OPTS, ssh_target, f"pgrep -f {session_id}"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return None
        if result.returncode == 0:
            return True
        if result.returncode == 1:
            return False
        return None  # 255 etc. = ssh-level failure -> unknown, never "dead"
    try:
        result = subprocess.run(
            ["pgrep", "-f", session_id],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        fail("pgrep not found — cannot check liveness on this system")
    return result.returncode == 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="worker_status.py", description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--session-ids", help="comma-separated session uuids")
    g.add_argument("--json", metavar="-", help="read JSON list from stdin ('-')")
    args = p.parse_args(argv)

    if args.session_ids is not None:
        entries = [{"session_id": s.strip()}
                   for s in args.session_ids.split(",") if s.strip()]
    else:
        if args.json != "-":
            fail("--json only supports '-' (read from stdin)")
        payload = read_json_stdin()
        if not isinstance(payload, list):
            fail("--json stdin must be a JSON list of {task, session_id}")
        entries = payload

    out = []
    for e in entries:
        sid = e.get("session_id")
        alive = is_alive(sid, e.get("ssh"))
        row = {"session_id": sid, "alive": alive}
        if alive is None:
            row["unreachable"] = True
        if "task" in e:
            row["task"] = e["task"]
        out.append(row)
    emit(out)


if __name__ == "__main__":
    main(sys.argv[1:])
