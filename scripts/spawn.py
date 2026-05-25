#!/usr/bin/env python3
"""Spawn a PWC worker in an iTerm2 split pane.

Builds a `claude` invocation (fresh `--session-id <uuid>` + seed prompt, or
`--resume <uuid>`) and runs it in a new split pane. Layout: the first worker
splits the coordinator's current window horizontally (worker pane below);
subsequent workers split that worker region vertically, tiling beside each other.
The worker-region pane is remembered in <workspace>/.pwc/iterm_layout.json and
self-heals if closed. Prints session id, mode, and placement as JSON. Does NOT
touch the ledger — the dispatch skill calls `ledger.py set-session` so all DB
writes funnel through one path.

Requires iTerm2 running with the Python API enabled
(Preferences -> General -> Magic -> Enable Python API) and `pip install iterm2`.
Fails with a clear message (never hangs) if it can't connect.

Usage:
  spawn.py --task <id> --cwd <dir> [--session-id <uuid>] [--resume]
           [--prompt-file <path> | --prompt -] [--name <display-name>]
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import uuid
from pathlib import Path

from _common import emit, fail


def session_slug(cwd: str) -> str:
    """Claude Code's transcript dir name for a cwd (literal path, / -> -)."""
    return str(Path(cwd).resolve()).replace("/", "-")


def transcript_path(cwd: str, session_id: str) -> Path:
    return (Path.home() / ".claude" / "projects"
            / session_slug(cwd) / f"{session_id}.jsonl")


def build_claude_command(*, session_id, resume, cwd, seed_prompt, name):
    """Return (mode, shell_command_string) to run in the new window."""
    args = ["claude"]
    mode = "fresh"
    if resume and transcript_path(cwd, session_id).exists():
        args += ["--resume", session_id]
        mode = "resume"
    else:
        # Fresh session (also the fallback when a resume target doesn't exist).
        args += ["--session-id", session_id]
        if seed_prompt:
            args.append(seed_prompt)
    # cd into the working dir, then exec claude through a login shell so PATH is set.
    inner = f"cd {shlex.quote(cwd)} && {shlex.join(args)}"
    return mode, inner


def _layout_state_path(cwd):
    """Where we remember the 'worker region' iTerm2 pane for this workspace.

    Layout is per-workspace, so this lives in the workspace root's .pwc/ — found by
    walking up from cwd. Crucially NOT cwd/.pwc/: a worker's cwd is a sub-repo, and
    writing a .pwc/ there would shadow the real workspace root for ledger discovery.
    """
    from _common import db_path, find_workspace_root
    return db_path(find_workspace_root(cwd)).parent / "iterm_layout.json"


def _read_worker_region(cwd):
    p = _layout_state_path(cwd)
    if p.exists():
        try:
            return json.loads(p.read_text()).get("worker_region_session_id")
        except (ValueError, OSError):
            return None
    return None


def _write_worker_region(cwd, session_id):
    p = _layout_state_path(cwd)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"worker_region_session_id": session_id}))


def spawn(*, cwd, command):
    """Split the coordinator's window to place a worker pane. Returns placement info.

    Layout model: the first worker splits the coordinator's current session
    horizontally (worker pane below). Subsequent workers split that worker region
    vertically, tiling beside each other. The 'worker region' pane is remembered
    in <workspace>/.pwc/iterm_layout.json; if it's been closed, we fall back to a
    fresh horizontal split off the coordinator.
    """
    try:
        import iterm2  # lazy: non-spawn use of this module shouldn't need it
    except ImportError:
        fail("iterm2 module not installed — run `pip install iterm2`")

    placement = {}

    async def _main(connection):
        app = await iterm2.async_get_app(connection)
        window = app.current_terminal_window
        if window is None:
            fail("no active iTerm2 window to split from")

        region_id = _read_worker_region(cwd)
        base = app.get_session_by_id(region_id) if region_id else None

        if base is not None:
            # Subsequent worker: tile beside existing workers.
            new = await base.async_split_pane(vertical=True)
            placement["split"] = "vertical"
        else:
            # First worker (or the region pane is gone): split coordinator below.
            base = window.current_tab.current_session
            new = await base.async_split_pane(vertical=False)
            placement["split"] = "horizontal"
            _write_worker_region(cwd, new.session_id)

        placement["iterm_session_id"] = new.session_id
        # The new pane starts a login shell; run the worker command in it.
        await new.async_send_text(command + "\n")

    try:
        iterm2.run_until_complete(_main)
    except Exception as e:  # noqa: BLE001
        fail(
            "could not reach iTerm2's Python API "
            "(is iTerm2 running with the API enabled in "
            f"Preferences -> General -> Magic?): {type(e).__name__}: {e}"
        )
    return placement


def main(argv=None):
    p = argparse.ArgumentParser(prog="spawn.py", description=__doc__)
    p.add_argument("--task", required=True)
    p.add_argument("--cwd", required=True)
    p.add_argument("--session-id")
    p.add_argument("--resume", action="store_true")
    p.add_argument("--prompt-file")
    p.add_argument("--prompt", help="seed prompt text, or '-' to read from stdin")
    p.add_argument("--name")
    p.add_argument("--dry-run", action="store_true",
                   help="print the command without opening a window (for testing)")
    args = p.parse_args(argv)

    cwd = str(Path(args.cwd).expanduser())
    if not os.path.isdir(cwd):
        fail(f"--cwd does not exist: {cwd}")

    session_id = args.session_id or str(uuid.uuid4())

    seed_prompt = None
    if args.prompt_file:
        seed_prompt = Path(args.prompt_file).read_text()
    elif args.prompt == "-":
        seed_prompt = sys.stdin.read()
    elif args.prompt:
        seed_prompt = args.prompt

    mode, command = build_claude_command(
        session_id=session_id, resume=args.resume, cwd=cwd,
        seed_prompt=seed_prompt, name=args.name,
    )

    result = {
        "session_id": session_id,
        "cwd": cwd,
        "mode": mode,
        "transcript_expected": str(transcript_path(cwd, session_id)),
        "command": command,
    }

    if args.dry_run:
        result["dry_run"] = True
        emit(result)
        return

    placement = spawn(cwd=cwd, command=command)
    result.update(placement)
    emit(result)


if __name__ == "__main__":
    main(sys.argv[1:])
