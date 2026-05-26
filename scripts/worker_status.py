#!/usr/bin/env python3
"""Worker-status check — is a worker session still running?

A worker is spawned with `claude --session-id <uuid>`, so the uuid appears in the
worker process's command line. `pgrep -f <uuid>` is therefore an exact "is this
session still running?" test — no PID storage, no polling, terminal-agnostic.
("Worker status" = alive/dead here, distinct from a task's status field.)

Used by /show-work: for each task with a session_id and a (task) status implying it
should be running, check worker status here; mark the dead ones "gone — needs
triage" via `taskdb.py set-status-gone`. This detects death, not outcome — a gone
worker may have left finished-but-unpushed work, so never infer done/failed.

Usage:
  worker_status.py --session-ids <uuid,uuid,...>
  worker_status.py --json -        # read [{"task": "...", "session_id": "..."}] on stdin

Output (stdout, JSON): a list of {session_id, alive[, task]}.
"""

from __future__ import annotations

import argparse
import subprocess
import sys

from _common import emit, fail, read_json_stdin


def is_alive(session_id: str) -> bool:
    """True if a running process has this session id in its argv.

    pgrep exits 0 if a match is found, 1 if none, >1 on error. The session id is a
    uuid, so a substring match (`-f`) is specific enough to not collide.
    """
    if not session_id:
        return False
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
        row = {"session_id": sid, "alive": is_alive(sid)}
        if "task" in e:
            row["task"] = e["task"]
        out.append(row)
    emit(out)


if __name__ == "__main__":
    main(sys.argv[1:])
