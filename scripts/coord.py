#!/usr/bin/env python3
"""Launch a PWC coordinator session in THIS terminal tab.

The coordinator is the session you sit in: it routes and dispatches, and it has to
be running before any worker can be spawned from it. So unlike `pwc spawn` — which
opens a worker in a NEW background iTerm2 tab — this REPLACES the current shell via
`os.execvp`. The tab you typed in becomes the coordinator.

Why this lives here and not in a shell alias
--------------------------------------------
The tab title. An OSC title sequence (`\033]0;…\007`) only reaches iTerm2 from a
process that owns the tty. A coordinator session cannot title its own tab: Claude
Code's tool subprocesses run DETACHED (controlling tty `??`, and `/dev/tty` is
unconfigured), so a printf from inside a session goes into the pipe back to the
harness and is captured as tool output — silently swallowed, never rendered. The
skill step that tried it was a guaranteed no-op.

`pwc coord` runs in the interactive shell, BEFORE exec, and that shell does own the
tty — so the title lands. Writing it here also means the title is emitted the same
way for every harness, and stays correct because it is built by spawn.tab_title(),
the same function that titles worker tabs.

Model selection
---------------
The coordinator model is DERIVED from the models table, not hardcoded: the
strongest available model for the harness, scored on the reasoning-heavy domains a
coordinator actually works in. Deriving it means a session cannot launch on a weak
model by accident, and the table stays the single source of truth about model
capability (`pwc models set-tier` retunes this without touching code). `--model`
overrides for a one-off.

Usage:
  pwc coord [claude|codex|opencode] [--model M] [--workspace DIR]
            [--name N] [--prompt P] [--print-command] [--no-title]
"""

from __future__ import annotations

import argparse
import os
import shlex
import sys
from pathlib import Path

import models
import spawn

# The domains a coordinator's own work sits in. It reads a board, reconciles it
# against reality, decides what is worth dispatching, and writes the briefing —
# judgment and prose, not code review or implementation (that is the workers').
COORD_DOMAINS = ("research-writing", "ops-comms")

DEFAULT_HARNESS = "claude"
DEFAULT_NAME = "PWC coordinator"
DEFAULT_PROMPT = "/pwc-show-work"


def fail(msg: str) -> None:
    print(f"pwc coord: {msg}", file=sys.stderr)
    raise SystemExit(2)


def pick_model(harness: str) -> tuple[str | None, str]:
    """Strongest available model for `harness` across COORD_DOMAINS.

    Returns (model, why). Scored on the MINIMUM tier across the coordinator's
    domains, so a model that is brilliant at prose but mediocre at ops work does not
    win on its best score alone — the coordinator needs both. Ties break toward the
    cheaper model: equal capability is not worth paying more for.

    Returns (None, reason) when the table cannot answer, and the caller launches on
    the harness default rather than refusing — a coordinator that will not start is
    worse than one on an unverified model.
    """
    try:
        data = models.table(must_exist=True)
    except SystemExit:
        raise
    except Exception as exc:
        return None, f"models table unreadable ({exc}) — using harness default"

    rows = [r for r in data.get("models", []) if r.get("harness") == harness]
    if not rows:
        return None, f"no {harness} rows in the models table — using harness default"

    usable = [r for r in rows if r.get("available")]
    if not usable:
        return None, f"no available {harness} model in the table — using harness default"

    def score(row):
        tiers = row.get("tiers") or {}
        weakest = min((tiers.get(d) or 0) for d in COORD_DOMAINS)
        # Cheaper wins ties: negate cost so max() prefers the lower number.
        return (weakest, -(row.get("cost_out") or 0.0))

    best = max(usable, key=score)
    weakest = min(((best.get("tiers") or {}).get(d) or 0) for d in COORD_DOMAINS)
    doms = "+".join(COORD_DOMAINS)
    return best.get("model"), f"strongest available {harness} (tier {weakest} across {doms})"


def resolve_workspace(explicit: str | None) -> str:
    """Where the coordinator runs. Defaults to the cwd, which is the point: standing
    in a PARENT of several workspaces is a supported vantage (pwc sweeps them all and
    tags each row), so this must NOT force-resolve down to a single .pwc/ root."""
    root = Path(explicit).expanduser() if explicit else Path.cwd()
    if not root.is_dir():
        fail(f"workspace {str(root)!r} is not a directory")
    return str(root.resolve())


def build(harness: str, *, model: str | None, cwd: str, name: str, prompt: str) -> list[str]:
    """The launch argv, via the same builder that launches workers.

    A coordinator is a FRESH, untracked session: no pre-allocated session id (it is
    not a worker, nothing resumes it by task) — so session_id is None and resume is
    False. The seed rides as the harness's positional prompt and auto-submits, which
    is exactly what makes the briefing appear without typing it.
    """
    builder = spawn._BUILDERS.get(harness)
    if builder is None:
        fail(f"unknown harness {harness!r} — known: {', '.join(sorted(spawn._BUILDERS))}")

    if harness == "claude":
        args = ["claude"]
        if model:
            args += ["--model", model]
        args += ["--name", name]
        if prompt:
            args.append(prompt)  # positional prompt -> auto-submits on startup
        return args

    if harness == "codex":
        args = ["codex"]
        if model:
            args += ["-m", model]
        if prompt:
            args.append(prompt)
        return args

    if harness == "opencode":
        # opencode cannot carry a seed on the CLI (verified 2026-07-13: --prompt
        # leaves the composer empty and the worker idle). spawn() types it into the
        # TUI for workers, but there is no spawn() here — we exec in place. So launch
        # bare and tell the user the one thing to type.
        args = ["opencode"]
        if model:
            args += ["--model", model]
        return args

    fail(f"harness {harness!r} has no coordinator launch path yet")
    return []  # unreachable; keeps type checkers quiet


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="pwc coord",
        description="Launch a PWC coordinator in this terminal tab (replaces the shell).",
    )
    ap.add_argument("harness", nargs="?", default=DEFAULT_HARNESS,
                    choices=sorted(spawn._BUILDERS),
                    help=f"harness to run the coordinator on (default: {DEFAULT_HARNESS})")
    ap.add_argument("--model", help="override the table-derived coordinator model")
    ap.add_argument("--workspace", help="where to run (default: cwd)")
    ap.add_argument("--name", default=DEFAULT_NAME, help=f"session display name (default: {DEFAULT_NAME!r})")
    ap.add_argument("--prompt", default=DEFAULT_PROMPT,
                    help=f"seed prompt (default: {DEFAULT_PROMPT!r}; empty string for none)")
    ap.add_argument("--print-command", action="store_true",
                    help="print the launch command and exit; do not exec")
    ap.add_argument("--no-title", action="store_true", help="do not set the tab title")
    args = ap.parse_args(argv)

    harness = args.harness
    if not models.harness_available(harness):
        fail(f"harness {harness!r} is not available (not installed, or not authenticated)")

    if args.model:
        model, why = args.model, "explicit --model"
    else:
        model, why = pick_model(harness)

    cwd = resolve_workspace(args.workspace)
    argv_out = build(harness, model=model, cwd=cwd, name=args.name, prompt=args.prompt)
    title = spawn.tab_title(name=args.name, task=args.name, harness=harness, model=model)

    if args.print_command:
        print(f"cd {shlex.quote(cwd)} && {shlex.join(argv_out)}")
        print(f"# title: {title}", file=sys.stderr)
        print(f"# model: {model or '(harness default)'} — {why}", file=sys.stderr)
        return 0

    if shutil_which(argv_out[0]) is None:
        fail(f"{argv_out[0]!r} not found on PATH")

    os.chdir(cwd)

    # Set the title BEFORE exec, while this process still owns the tty. Only when
    # stdout is a terminal — piped output must stay clean, and the sequence would be
    # captured as text rather than rendered (exactly the bug this command exists to
    # fix).
    if not args.no_title and sys.stdout.isatty():
        sys.stdout.write(f"\033]0;{title}\007")
        sys.stdout.flush()

    print(f"pwc coord: {harness}"
          + (f"/{model}" if model else " (harness default model)")
          + f" in {cwd}", file=sys.stderr)
    print(f"pwc coord: model — {why}", file=sys.stderr)
    if harness == "opencode" and args.prompt:
        # Say it plainly rather than pretending the seed was delivered.
        print(f"pwc coord: opencode takes no CLI seed — type {args.prompt!r} once it loads.",
              file=sys.stderr)

    os.execvp(argv_out[0], argv_out)  # replaces this process; never returns


def shutil_which(cmd: str):
    import shutil
    return shutil.which(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
