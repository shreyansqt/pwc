#!/usr/bin/env python3
"""Spawn a PWC worker in a new iTerm2 tab, in the task's harness.

Builds the harness's launch invocation (default `claude`: fresh `--session-id
<uuid>` + seed prompt, or `--resume <uuid>`) and runs it in a new tab of the
current iTerm2 window, titled after the task. Each worker gets its own full-width
tab (Cmd-1/2/... to switch); the coordinator's tab is untouched. The new tab opens
**in the background** — iTerm2 always switches focus to a newly-created tab (its
API has no background-create flag), so we remember the active tab beforehand and
re-activate it immediately after the new tab is created. Brief flicker; user stays
where they were. Prints session id, mode, and placement as JSON. Does NOT touch
the task DB — the dispatch skill calls `pwc set-session` so all DB writes funnel
through one path.

Harnesses (`--harness`, default claude):
  claude    — fully supported: pre-allocated session id (identity, liveness via
              pgrep, resume via --resume), seed as positional prompt.
  opencode  — UNVERIFIED (written against docs, not yet exercised): seed via
              --prompt, model via --model. No session pre-allocation, so no
              pgrep liveness and no `pwc set-session`; resume maps to
              `opencode --continue` (that DIRECTORY's most recent session —
              best-effort, wrong if two workers share a repo).
  codex     — UNVERIFIED: seed as positional prompt, model via --model. Same
              no-pre-allocation caveats; resume maps to `codex resume --last`.
Verify a new harness's flags on first real use and update its builder here.

Requires iTerm2 running with the Python API enabled
(Preferences -> General -> Magic -> Enable Python API) and `pip install iterm2`.
Fails with a clear message (never hangs) if it can't connect.

Usage:
  spawn.py --task <id> --cwd <dir> [--harness claude|opencode|codex] [--model M]
           [--session-id <uuid>] [--resume]
           [--prompt-file <path> | --prompt -] [--name <display-name>]
"""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
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


def _build_claude(*, session_id, resume, cwd, seed_prompt, model):
    """The fully-supported harness. On a FRESH spawn the seed is passed as
    `claude`'s positional prompt argument, so claude ingests it as the first user
    message and starts working immediately — reliably, with no TUI timing race.

    This replaces the old approach of launching claude bare and then typing the
    seed into its input box once the TUI drew. That screen-scrape-then-type path
    was fragile: it polled the rendered screen for claude's footer markers within a
    timeout, and on a slow (cold) start the timeout fired and the seed was simply
    NOT typed (`seed: "not-typed"`), forcing the user to copy-paste it by hand. A
    positional prompt has none of that — claude's own startup consumes it, so there
    is nothing to time. (Interactive `claude [prompt]` stays interactive; only
    `-p/--print` makes it one-shot, which we never pass.) The long multiline seed
    is shell-quoted via `shlex.quote`, so quoting is not a concern.

    Trade-off: a positional prompt AUTO-SUBMITS — the worker starts on the seed
    rather than letting the user review it un-submitted first. That's the chosen
    behavior (the seed is the same load-context-then-start-ticket boilerplate every
    time, and reliable delivery matters more than the review gate).

    On RESUME the resumed session already carries its full prior context, so a seed
    is optional — but when one IS given it is a *follow-up* (a new ask for the
    resumed worker, e.g. "answer this teammate's question"), and it is appended as
    the positional prompt just like a fresh spawn. `claude --resume <id> '<prompt>'`
    resumes with history intact AND auto-submits the prompt (verified 2026-07-07),
    so a resume-with-follow-up needs no hand-paste. Resume with no seed stays bare.
    """
    args = ["claude"]
    if model:
        args += ["--model", model]
    mode = "fresh"
    if resume and transcript_path(cwd, session_id).exists():
        args += ["--resume", session_id]
        mode = "resume"
        if seed_prompt:
            args.append(seed_prompt)  # follow-up on resume -> claude auto-submits it
    else:
        args += ["--session-id", session_id]  # fresh / resume-fallback
        if seed_prompt:
            args.append(seed_prompt)  # positional prompt -> claude auto-submits it
    return mode, args


def _build_opencode(*, session_id, resume, cwd, seed_prompt, model):
    """UNVERIFIED (opencode not yet installed/exercised — check flags on first use).
    No session pre-allocation: `session_id` is ignored, identity/liveness/resume
    tracking don't apply. `--continue` reopens this directory's most recent
    session — best-effort resume, wrong if two workers ever shared this repo."""
    args = ["opencode"]
    if model:
        args += ["--model", model]
    mode = "fresh"
    if resume:
        args.append("--continue")
        mode = "resume"
    if seed_prompt:
        args += ["--prompt", seed_prompt]
    return mode, args


def _build_codex(*, session_id, resume, cwd, seed_prompt, model):
    """UNVERIFIED (codex not yet installed/exercised — check flags on first use).
    No session pre-allocation; resume maps to `codex resume --last` (most recent
    session in this directory), and a seed can't ride along on a resume."""
    if resume:
        args = ["codex", "resume", "--last"]
        mode = "resume"
    else:
        args = ["codex"]
        mode = "fresh"
        if model:
            args += ["--model", model]
        if seed_prompt:
            args.append(seed_prompt)
    return mode, args


# claude is the only harness with pre-allocated session ids (identity, pgrep
# liveness, transcript-based resume). The others launch fine but are untracked:
# the dispatch skill must NOT `pwc set-session` for them.
_BUILDERS = {
    "claude": (_build_claude, True),     # (builder, session_tracked)
    "opencode": (_build_opencode, False),
    "codex": (_build_codex, False),
}


def build_command(*, harness, session_id, resume, cwd, seed_prompt=None, model=None):
    """Return (mode, launch_command, session_tracked) for the task's harness."""
    if harness not in _BUILDERS:
        fail(f"unknown harness {harness!r} — known: {', '.join(sorted(_BUILDERS))}")
    builder, session_tracked = _BUILDERS[harness]
    mode, args = builder(session_id=session_id, resume=resume, cwd=cwd,
                         seed_prompt=seed_prompt, model=model)
    inner = f"cd {shlex.quote(cwd)} && {shlex.join(args)}"
    return mode, inner, session_tracked


def spawn(*, cwd, command, seed_in_command=False, title=None):
    """Open the worker in a new iTerm2 tab in the current window. Returns placement.

    Each worker gets its own full-width tab (switchable with Cmd-1/2/...), leaving
    the coordinator's tab untouched. The new tab opens **in the background** — the
    user's previously-active tab is restored as the focused tab right after the new
    tab is created, so spawning workers doesn't yank the user out of whatever they
    were doing. `async_send_text` targets the worker's session object directly (not
    "the active session"), so the launch command is still delivered to the
    backgrounded worker correctly.

    The seed is no longer typed into the input box. It rides in `command` as
    claude's positional prompt (see `build_claude_command`), so claude auto-submits
    it on startup — reliable, with no TUI timing race. All this function does is run
    `command` in the tab's shell. The old screen-scrape-then-type path (poll for
    claude's footer markers, type the seed un-submitted, and on a slow start give up
    with `seed: "not-typed"` forcing a manual paste) is gone.

    `placement["seed"]` reports "submitted" (seed baked into the launch command and
    auto-submitted) or "skipped" (no seed — e.g. a resume). `seed_in_command` tells
    which.
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

        # Remember the user's currently-active tab BEFORE creating the new one so we
        # can restore focus to it after — `async_create_tab` always switches focus to
        # the new tab (the API has no background-create flag, confirmed), and that's
        # jarring when the user is mid-flow in another tab. The flicker is brief, the
        # net effect is the worker tab opens unfocused in the tab bar.
        original_tab = window.current_tab

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

        # Restore focus to whatever tab the user was on. Safe to do BEFORE typing the
        # launch command below: `async_send_text` targets the session object
        # directly, not "the active session," so the worker tab can sit in the
        # background and still receive its launch command correctly.
        # `order_window_front=False` keeps the window itself from being raised either.
        if original_tab is not None and original_tab is not tab:
            try:
                await original_tab.async_activate(order_window_front=False)
                placement["focus_restored"] = True
            except Exception:  # noqa: BLE001 — focus restore is best-effort, never fatal
                placement["focus_restored"] = False

        # Launch claude in the new shell. The seed (if any) is already part of
        # `command` as claude's positional prompt, so claude auto-submits it on
        # startup — nothing more to type.
        await session.async_send_text(command + "\r")
        placement["seed"] = "submitted" if seed_in_command else "skipped"

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
    p.add_argument("--harness", default="claude",
                   help="coding agent to launch (claude|opencode|codex); default claude")
    p.add_argument("--model", help="model override for the harness")
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

    if not args.dry_run and shutil.which(args.harness) is None:
        fail(f"harness {args.harness!r} is not installed (not on PATH)")

    mode, command, session_tracked = build_command(
        harness=args.harness, session_id=session_id, resume=args.resume,
        cwd=cwd, seed_prompt=seed_prompt, model=args.model,
    )
    # The seed rides in the launch command as the harness's prompt argument
    # (auto-submitted) on BOTH a fresh spawn and a resume-with-follow-up — the only
    # difference is meaning (fresh = the task seed; resume = a follow-up ask). A
    # resume with no seed carries no prompt. `seed_in_command` tracks whether
    # `command` actually contains a prompt, so spawn() reports the right seed status.
    seed_in_command = bool(seed_prompt)

    result = {
        "harness": args.harness,
        "model": args.model,
        # session_id is only meaningful when the harness tracks it (claude);
        # session_tracked tells the dispatch skill whether to `pwc set-session`.
        "session_id": session_id if session_tracked else None,
        "session_tracked": session_tracked,
        "cwd": cwd,
        "mode": mode,
        "command": command,
    }
    if session_tracked:
        result["transcript_expected"] = str(transcript_path(cwd, session_id))

    if args.dry_run:
        result["dry_run"] = True
        emit(result)
        return

    placement = spawn(cwd=cwd, command=command, seed_in_command=seed_in_command,
                      title=args.name or args.task)
    result.update(placement)
    emit(result)


if __name__ == "__main__":
    main(sys.argv[1:])
