#!/usr/bin/env python3
"""Hand a PWC worker off to the user to start manually (Claude Desktop mode).

The terminal model (`spawn.py`) opens an iTerm2 tab and types the seed into a
fresh `claude` session for the user. A Claude Desktop user has no terminal and no
iTerm2 Python API, so there is nothing to spawn into. Instead we **hand the seed
back to the user**: they open a new session themselves (in the Desktop app's code
section, which sees the global skills) in the given directory and paste the seed.

This script does the deterministic part of that handoff: it pre-allocates / accepts
the session id, **copies the seed to the clipboard** (`pbcopy`) so the user just
pastes, and prints the directory + seed + clipboard status as JSON for the skill to
relay. It does NOT touch the task DB — like `spawn.py`, the dispatch skill owns the
`set-session` write so all DB writes funnel through one path.

Unlike `spawn.py` there is no live process afterwards: a Desktop worker is a session
the user drives, not a local `claude` process, so it is not `pgrep`-able. The
`session_id` is still recorded (by the skill) so the task reads as dispatched and the
user can report status against it.

Usage:
  handoff.py --task <id> --cwd <dir> [--session-id <uuid>]
             [--prompt-file <path> | --prompt -] [--name <display-name>]
             [--no-clipboard]
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from _common import emit, fail


def copy_to_clipboard(text: str) -> bool:
    """Copy text to the macOS clipboard via pbcopy. Returns success (never raises).

    Best-effort: if pbcopy is missing or fails, we report it and let the skill fall
    back to showing the seed for manual copy — the handoff still works, the user just
    selects the text instead of pasting from the clipboard.
    """
    if not shutil.which("pbcopy"):
        return False
    try:
        subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def main(argv=None):
    p = argparse.ArgumentParser(prog="handoff.py", description=__doc__)
    p.add_argument("--task", required=True)
    p.add_argument("--cwd", required=True)
    p.add_argument("--session-id")
    p.add_argument("--prompt-file")
    p.add_argument("--prompt", help="seed prompt text, or '-' to read from stdin")
    p.add_argument("--name")
    p.add_argument("--no-clipboard", action="store_true",
                   help="skip pbcopy; just print the seed for manual copy")
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

    clipboard = "skipped"
    if seed_prompt and not args.no_clipboard:
        clipboard = "copied" if copy_to_clipboard(seed_prompt) else "failed"

    emit({
        "task": args.task,
        "session_id": session_id,
        "cwd": cwd,
        "mode": "desktop",
        "name": args.name or args.task,
        "seed": seed_prompt,
        "clipboard": clipboard,
    })


if __name__ == "__main__":
    main(sys.argv[1:])
