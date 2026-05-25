#!/usr/bin/env python3
"""Spawn a PWC worker in a new iTerm2 tab.

Builds a `claude` invocation (fresh `--session-id <uuid>` + seed prompt, or
`--resume <uuid>`) and runs it in a new tab of the current iTerm2 window, titled
after the task. Each worker gets its own full-width tab (Cmd-1/2/... to switch);
the coordinator's tab is untouched. Prints session id, mode, and placement as
JSON. Does NOT touch the ledger — the dispatch skill calls `ledger.py set-session`
so all DB writes funnel through one path.

Requires iTerm2 running with the Python API enabled
(Preferences -> General -> Magic -> Enable Python API) and `pip install iterm2`.
Fails with a clear message (never hangs) if it can't connect.

Usage:
  spawn.py --task <id> --cwd <dir> [--session-id <uuid>] [--resume]
           [--prompt-file <path> | --prompt -] [--name <display-name>]
"""

from __future__ import annotations

import argparse
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


def spawn(*, cwd, command, title=None):
    """Open the worker in a new iTerm2 tab in the current window. Returns placement.

    Each worker gets its own full-width tab (switchable with Cmd-1/2/...), leaving
    the coordinator's tab untouched — no pane tiling, no layout state to track. The
    command runs in lieu of the shell in the new tab; the tab is titled after the
    task so it's identifiable at a glance.
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
            fail("no active iTerm2 window to open a tab in")

        full = f"/bin/zsh -l -c {shlex.quote(command)}"
        tab = await window.async_create_tab(command=full)
        placement["tab"] = True
        if tab is not None:
            session = tab.current_session
            placement["iterm_session_id"] = session.session_id if session else None
            if title:
                try:
                    await tab.async_set_title(title)
                except Exception:  # noqa: BLE001 — title is cosmetic, never fatal
                    pass

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

    placement = spawn(cwd=cwd, command=command, title=args.name or args.task)
    result.update(placement)
    emit(result)


if __name__ == "__main__":
    main(sys.argv[1:])
