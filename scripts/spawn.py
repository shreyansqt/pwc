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
  opencode  — session-tracked too (verified 2026-07-10 on v1.17.18): a fresh spawn
              PRE-CREATES the session via a transient `opencode serve` +
              POST /session (the id is known before the worker exists), then
              launches `opencode --session <ses_id>` — the id is in the process
              argv, so pgrep liveness works, and resume is the same `--session`
              attach. Seed via --prompt (auto-submit behavior: verify on first
              interactive use), model via --model. Note: on a fresh spawn the
              session id is MINTED HERE (opencode ids aren't chooseable), so the
              caller must record the RETURNED session_id, not one it generated.
  codex     — UNVERIFIED (not installed): seed as positional prompt, model via
              --model. No pre-allocation; resume maps to `codex resume --last`
              (most recent session in this directory). Untracked: no pgrep
              liveness, no `pwc set-session`.
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
    return mode, args, session_id


_OPENCODE_DB = Path.home() / ".local" / "share" / "opencode" / "opencode.db"


def _opencode_session_exists(session_id: str) -> bool:
    """Read-only peek into opencode's session store. On ANY doubt (schema moved,
    db missing/locked) say True — attaching to a missing session fails visibly in
    the worker tab, which beats silently minting a fresh session on a resume."""
    try:
        import sqlite3
        conn = sqlite3.connect(f"file:{_OPENCODE_DB}?mode=ro", uri=True, timeout=2)
        try:
            return conn.execute(
                "SELECT 1 FROM session WHERE id = ?", (session_id,)
            ).fetchone() is not None
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return True


def _preallocate_opencode_session(cwd: str, title: str) -> str:
    """Create an opencode session BEFORE the worker exists and return its id.

    opencode has no `session create` CLI and its ids aren't chooseable, but its
    server API mints one: start a transient `opencode serve` in the worker's cwd
    (sessions are directory-bound), POST /session, shut the server down. The
    session persists in opencode's store (verified 2026-07-10), so the later
    `opencode --session <id>` launch attaches to it.
    """
    import json as _json
    import socket
    import subprocess
    import time
    import urllib.request

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        ["opencode", "serve", "--port", str(port)],
        cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 20
        while True:
            if proc.poll() is not None:
                fail("opencode serve exited before becoming ready")
            try:
                urllib.request.urlopen(f"{base}/session", timeout=1)
                break
            except OSError:
                if time.time() > deadline:
                    fail("opencode serve did not become ready within 20s")
                time.sleep(0.3)
        req = urllib.request.Request(
            f"{base}/session",
            data=_json.dumps({"title": title}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            session = _json.loads(resp.read())
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    sid = session.get("id")
    if not sid:
        fail(f"opencode POST /session returned no id: {session}")
    return sid


def _build_opencode(*, session_id, resume, cwd, seed_prompt, model,
                    title=None, dry_run=False):
    """Session-tracked like claude, with inverted id flow: claude accepts a
    caller-chosen uuid, opencode mints its own — pre-created here on a fresh
    spawn so it's still known before the worker process exists. Both fresh and
    resume launch `opencode --session <id>`, so the id sits in the process argv
    (pgrep liveness) either way."""
    if resume and session_id and _opencode_session_exists(session_id):
        mode = "resume"
    else:
        mode = "fresh"
        session_id = ("ses_DRYRUN-not-created" if dry_run
                      else _preallocate_opencode_session(cwd, title or "PWC worker"))
    args = ["opencode", "--session", session_id]
    if model:
        args += ["--model", model]
    if seed_prompt:
        args += ["--prompt", seed_prompt]
    return mode, args, session_id


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
    return mode, args, None  # untracked: codex ids aren't known at spawn time


# session_tracked = the harness's session id is known at spawn time and appears in
# the worker's argv, so identity (`pwc set-session`), pgrep liveness, and resume
# all work. claude: caller-chosen uuid. opencode: pre-created via its server API.
# codex: neither — the dispatch skill must NOT `pwc set-session` for it.
_BUILDERS = {
    "claude": (_build_claude, True),     # (builder, session_tracked)
    "opencode": (_build_opencode, True),
    "codex": (_build_codex, False),
}


def build_command(*, harness, session_id, resume, cwd, seed_prompt=None,
                  model=None, title=None, dry_run=False):
    """Return (mode, launch_command, session_id, session_tracked) for the task's
    harness. `session_id` in the result is the EFFECTIVE id (opencode mints its
    own on a fresh spawn — record that one), or None for untracked harnesses."""
    if harness not in _BUILDERS:
        fail(f"unknown harness {harness!r} — known: {', '.join(sorted(_BUILDERS))}")
    builder, session_tracked = _BUILDERS[harness]
    kwargs = dict(session_id=session_id, resume=resume, cwd=cwd,
                  seed_prompt=seed_prompt, model=model)
    if harness == "opencode":
        kwargs.update(title=title, dry_run=dry_run)
    mode, args, effective_id = builder(**kwargs)
    inner = f"cd {shlex.quote(cwd)} && {shlex.join(args)}"
    return mode, inner, effective_id, session_tracked


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

    mode, command, session_id, session_tracked = build_command(
        harness=args.harness, session_id=session_id, resume=args.resume,
        cwd=cwd, seed_prompt=seed_prompt, model=args.model,
        title=args.name or args.task, dry_run=args.dry_run,
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
        # The EFFECTIVE session id — for opencode a fresh spawn mints it here, so
        # the dispatch skill must record THIS value, not an id it generated.
        # None for untracked harnesses (no `pwc set-session` for those).
        "session_id": session_id if session_tracked else None,
        "session_tracked": session_tracked,
        "cwd": cwd,
        "mode": mode,
        "command": command,
    }
    if args.harness == "claude":
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
