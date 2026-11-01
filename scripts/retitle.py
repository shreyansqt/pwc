#!/usr/bin/env python3
"""Restore a running session's tab title and work out its session name.

Any session (coordinator or worker, any harness) can run this when it has lost
its name, typically after `/restart`, which relaunches a plain `claude` without
the `--name` that `pwc coord` / `pwc spawn` passed.

What this can and cannot set
----------------------------
- The TAB TITLE: yes. A session's subprocess cannot write an OSC title to the
  tty (see coord.py), but the iTerm2 Python API can set it, and the tab is found
  by tty exactly as `pwc coord` finds it to group it.
- The SESSION NAME (Claude Code / Remote Control): no. `/rename` is a built-in
  slash command that only the user can type. This command prints the exact line
  to paste. codex and opencode have no session name, so none is printed.

Usage:
  pwc retitle [--task ID] [--workspace DIR] [--harness H] [--model M] [--print]

With --task the session is that task's worker; without it, the coordinator of
the workspace. --print computes and shows the names without touching iTerm2.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import spawn

COORD_LABEL = "PWC"


def fail(msg: str) -> None:
    print(f"pwc retitle: {msg}", file=sys.stderr)
    raise SystemExit(2)


def coordinator_name(workspace: str) -> str:
    """`<machine> · PWC · <workspace>`, the name `pwc coord` gives a coordinator."""
    return f"{spawn.machine_label()} · {COORD_LABEL} · {Path(workspace).name}"


def worker_identity(workspace: str, task_id: str) -> dict:
    """Name, harness and model of a worker, from its task row."""
    out = subprocess.run(
        [str(Path(__file__).resolve().parent.parent / "bin" / "pwc"),
         "--workspace", workspace, "detail", "--task", task_id],
        capture_output=True, text=True)
    if out.returncode != 0:
        fail(f"cannot read task {task_id!r}: {out.stderr.strip() or out.stdout.strip()}")
    task = json.loads(out.stdout)["task"]
    return {"base": task_id, "harness": task.get("harness") or "claude",
            "model": task.get("model")}


def compute(*, workspace: str, task: str | None, harness: str | None,
            model: str | None) -> dict:
    if task:
        ident = worker_identity(workspace, task)
        base = ident["base"]
        session = spawn.session_name(base)
        harness = harness or ident["harness"]
        model = model or ident["model"]
    else:
        session = coordinator_name(workspace)
        base = session
        harness = harness or "claude"
        if model is None:
            import coord
            model, _ = coord.pick_model(harness)
    title = spawn.tab_title(name=base if not task else None, task=base,
                            harness=harness, model=model)
    # Workers are titled `<task> [runner]` (no machine prefix); the coordinator is
    # titled with its full name, as `pwc coord` does.
    rename = f"/rename {session}" if harness == "claude" else None
    return {"tab_title": title, "session_name": session if rename else None,
            "rename_command": rename, "harness": harness, "model": model}


def set_tab_title(title: str, tty: str | None) -> str:
    """Set the title of the tab owning `tty`. Never raises; returns a status."""
    try:
        import iterm2
        import iterm_groups
    except ImportError:
        return "skipped: iterm2 module not installed"
    if tty is None:
        return "skipped: no tty"
    outcome = {"status": "no-tab"}

    async def _main(connection):
        app = await iterm2.async_get_app(connection)
        tab = await iterm_groups.find_tab_by_tty(app, tty)
        if tab is None:
            return
        await tab.async_set_title(title)
        outcome["status"] = "set"

    try:
        iterm2.run_until_complete(_main)
    except Exception as exc:  # noqa: BLE001 — cosmetic
        return f"skipped: {type(exc).__name__}: {exc}"
    return outcome["status"]


def parent_tty() -> str | None:
    """Our subprocess is detached, so find the tty of the owning session by walking
    up the process tree to the first ancestor that has one."""
    pid = str(__import__("os").getppid())
    for _ in range(12):
        out = subprocess.run(["ps", "-o", "tty=,ppid=", "-p", pid],
                             capture_output=True, text=True).stdout.split()
        if len(out) < 2:
            return None
        tty, ppid = out[0], out[1]
        if tty not in ("??", "?", ""):
            return tty if tty.startswith("/dev/") else f"/dev/{tty}"
        pid = ppid
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pwc retitle", description=__doc__.split("\n\n")[0])
    ap.add_argument("--task", help="the task id, when this session is a worker")
    ap.add_argument("--workspace", help="workspace root (default: cwd)")
    ap.add_argument("--harness")
    ap.add_argument("--model")
    ap.add_argument("--print", dest="dry", action="store_true",
                    help="show the names without touching iTerm2")
    args = ap.parse_args(argv)

    workspace = str(Path(args.workspace).expanduser().resolve()) if args.workspace \
        else str(Path.cwd().resolve())
    res = compute(workspace=workspace, task=args.task, harness=args.harness,
                  model=args.model)
    res["tab_status"] = "not changed (--print)" if args.dry \
        else set_tab_title(res["tab_title"], parent_tty())
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
