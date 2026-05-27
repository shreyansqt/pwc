#!/usr/bin/env python3
"""Spawn a PWC worker in a new iTerm2 tab.

Builds a `claude` invocation (fresh `--session-id <uuid>` + seed prompt, or
`--resume <uuid>`) and runs it in a new tab of the current iTerm2 window, titled
after the task. Each worker gets its own full-width tab (Cmd-1/2/... to switch);
the coordinator's tab is untouched. Prints session id, mode, and placement as
JSON. Does NOT touch the task DB — the dispatch skill calls `taskdb.py set-session`
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


def build_claude_command(*, session_id, resume, cwd):
    """Return (mode, launch_command). The launch command starts `claude`
    *interactively* (no prompt argument) so the session stays open for the user to
    drive — a positional prompt would make claude one-shot and exit. The seed
    prompt is delivered separately (typed into the session), which also avoids the
    shell-quoting fragility of passing a long multiline prompt as an argument.
    """
    args = ["claude"]
    mode = "fresh"
    if resume and transcript_path(cwd, session_id).exists():
        args += ["--resume", session_id]
        mode = "resume"
    else:
        args += ["--session-id", session_id]  # fresh / resume-fallback
    inner = f"cd {shlex.quote(cwd)} && {shlex.join(args)}"
    return mode, inner


def spawn(*, cwd, command, seed_prompt=None, title=None):
    """Open the worker in a new iTerm2 tab in the current window. Returns placement.

    Each worker gets its own full-width tab (switchable with Cmd-1/2/...), leaving
    the coordinator's tab untouched. `command` launches claude interactively; if a
    `seed_prompt` is given, it's typed into the session's input box after claude
    boots — but deliberately *not* submitted. The seed sits in the box for the user
    to read and send with Enter. This is intentional: auto-submitting was racy (the
    keystrokes raced claude's startup and were silently lost) and gave the user no
    chance to glance at the briefing first. Leaving it in the box is both reliable
    and reviewable.

    Readiness is detected by polling the rendered screen for claude's input box
    rather than a fixed sleep, so we type only after the box can accept input.
    `placement["seed"]` reports what happened: "in-box" (typed into the box, awaiting
    the user's Enter), "skipped" (no seed), or "not-typed" (the TUI never drew within
    the timeout, so the seed was NOT typed — surfaced so the caller tells the user to
    paste it manually).
    """
    try:
        import iterm2  # lazy: non-spawn use of this module shouldn't need it
    except ImportError:
        fail("iterm2 module not installed — run `pip install iterm2`")

    placement = {}

    async def _await_ready(session, timeout=30.0, interval=0.5):
        """Wait until claude's TUI has drawn its input box and can accept text.

        A fresh interactive session writes no transcript until the first message is
        submitted, so the transcript file is NOT a usable readiness signal here.
        Instead poll the rendered screen for claude's input prompt (the ">" box and
        its hint line), which appears once the TUI is up. Falls back to returning
        True at timeout so we still type the seed into the box (it just sits there
        either way, since we never auto-submit).
        """
        import asyncio
        # Markers claude's interactive TUI draws once it can accept input. Cover the
        # current prompt glyph ("❯"), the boxed ">" some builds use, and the footer
        # hints ("for shortcuts", "auto mode on", "esc to interrupt", "Bypassing").
        markers = ("❯", "│ >", "for shortcuts", "auto mode on",
                   "esc to interrupt", "Bypassing")
        waited = 0.0
        while waited < timeout:
            try:
                contents = await session.async_get_screen_contents()
                text = "\n".join(
                    contents.line(i).string
                    for i in range(contents.number_of_lines)
                )
            except Exception:  # noqa: BLE001 — screen read is best-effort
                text = ""
            if any(m in text for m in markers):
                await asyncio.sleep(0.3)
                return True
            await asyncio.sleep(interval)
            waited += interval
        return False

    async def _main(connection):
        app = await iterm2.async_get_app(connection)
        window = app.current_terminal_window
        if window is None:
            fail("no active iTerm2 window to open a tab in")

        # Open a normal interactive shell tab (NOT command=...). Interactive claude
        # needs a real TTY; launching it as the tab's program via command= gives it
        # no interactive terminal and it exits instantly (closing the tab). Instead
        # we type the launch command into a real shell, so claude runs with a TTY.
        tab = await window.async_create_tab()
        placement["tab"] = True
        if tab is None:
            return
        session = tab.current_session
        placement["iterm_session_id"] = session.session_id if session else None
        if title:
            try:
                await tab.async_set_title(title)
            except Exception:  # noqa: BLE001 — title is cosmetic, never fatal
                pass
        if session is None:
            return

        # Launch claude in the new shell.
        await session.async_send_text(command + "\r")

        if not seed_prompt:
            placement["seed"] = "skipped"
            return

        # Type the briefing into the input box ONLY once the TUI is confirmed ready —
        # and WITHOUT a trailing "\r" so it sits there for the user to review and
        # submit with Enter. If readiness can't be confirmed within the timeout, do
        # NOT type: dumping a multi-line block into a shell/boot screen risks it being
        # consumed or partially submitted. Report "not-typed" so the caller tells the
        # user to paste it manually.
        ready = await _await_ready(session)
        if ready:
            await session.async_send_text(seed_prompt)
            placement["seed"] = "in-box"
        else:
            placement["seed"] = "not-typed"

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
                   help="print the command without opening a tab (for testing)")
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
    )
    # A resumed session carries its own context; don't re-inject a seed.
    if mode == "resume":
        seed_prompt = None

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

    placement = spawn(cwd=cwd, command=command, seed_prompt=seed_prompt,
                      title=args.name or args.task)
    result.update(placement)
    emit(result)


if __name__ == "__main__":
    main(sys.argv[1:])
