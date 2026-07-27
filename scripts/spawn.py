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
              attach. Seed is TYPED into its TUI by spawn() — `--prompt` does NOT
              work interactively (verified 2026-07-13: leaves the composer empty
              and the worker idle at $0.00). Model via --model. Note: on a fresh spawn the
              session id is MINTED HERE (opencode ids aren't chooseable), so the
              caller must record the RETURNED session_id, not one it generated.
  codex     — session-tracked too (verified 2026-07-10 on v0.144.1): a fresh spawn
              PRE-CREATES the session via `codex app-server` JSON-RPC
              (`thread/start` mints the uuid; the rollout file is only written on
              the server's GRACEFUL shutdown — close stdin and wait, never
              terminate()), then launches `codex resume <uuid> '<seed>'` — resume
              accepts a positional prompt and -m/--model, and the uuid in argv
              gives pgrep liveness. Auto-submit behavior of that prompt: verify on
              first interactive use. PWC workers also pass
              `-c sandbox_workspace_write.network_access=true` (supported by
              codex-cli 0.145.0) so hub-backed workspace calls can reach their
              task store without changing the user's global Codex sandbox policy.
              Like opencode, the id is MINTED HERE on a fresh spawn — record the
              RETURNED session_id.
Verify a new harness's flags on first real use and update its builder here.

Requires iTerm2 running with the Python API enabled
(Preferences -> General -> Magic -> Enable Python API) and `pip install iterm2`.
Fails with a clear message (never hangs) if it can't connect.

Remote workers (`--ssh <target>`, claude harness only for now): the worker runs on
a named machine inside a REMOTE TMUX SESSION, so it survives this laptop sleeping
or the tab closing — the iTerm tab is just a viewport (`ssh -t <target> tmux
new-session -A -s pwc-<task> …`; reattach anytime with the same command). The seed
is STAGED to a file on the remote host first (`~/.pwc/seeds/<uuid>.txt` via ssh
stdin) and the launch reads it with `$(cat …)` — long multiline seeds never pass
through nested shell quoting. `--cwd` must be the REMOTE path (the dispatch skill
maps the task's workdir through the runhost's `workspace_root`). Liveness still
works: the pre-allocated uuid is in the remote claude's argv, and worker_status.py
greps it over ssh.

Usage:
  spawn.py --task <id> --cwd <dir>
           [--ssh <target>] [--runhost <name>]
           [--session-id <uuid>] [--resume]
           [--prompt-file <path> | --prompt -] [--name <display-name>]

  spawn.py --task <id> --cwd <dir>
           --force-model --force-reason "<why>" --harness <h> --model <m>
           [--session-id <uuid>] [--resume] ...

Harness/model are read from the task record (set by `pwc route`). The
--harness/--model flags require --force-model + --force-reason and log an
audit event — they are the locked-down override, not everyday flags.
"""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import sys
import uuid
from pathlib import Path

from _common import emit, fail, find_workspace_root, now_iso


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
    (pgrep liveness) either way.

    SEED DELIVERY DIFFERS FROM CLAUDE, and the difference is not cosmetic.
    `claude '<prompt>'` AUTO-SUBMITS its positional prompt; interactive
    `opencode --prompt '<text>'` only PRE-FILLS the input box and waits for the
    user to hit enter. Verified live 2026-07-13 on v1.17.18: a worker spawned with
    --prompt sat idle with 0 messages and $0.00 cost — the seed was on screen but
    never sent. (spawn.py's own docstring had flagged this as "verify on first
    interactive use"; this was that use, and the assumption was false.)

    So the caller must SUBMIT it. `needs_submit` in the build result tells spawn()
    to send a bare newline into the tab once the TUI has drawn. We do NOT use
    `opencode run` (which does auto-submit) because that is the non-interactive
    one-shot mode — it prints and exits, leaving no live session for the user to
    take over, which is the entire point of a worker tab.
    """
    if resume and session_id and _opencode_session_exists(session_id):
        mode = "resume"
    else:
        mode = "fresh"
        session_id = ("ses_DRYRUN-not-created" if dry_run
                      else _preallocate_opencode_session(cwd, title or "PWC worker"))
    args = ["opencode", "--session", session_id]
    if model:
        args += ["--model", model]
    # NOTE: the seed is deliberately NOT passed as `--prompt`. Verified live
    # 2026-07-13 (v1.17.18): interactive `opencode --prompt '<text>'` does not put
    # the text into the TUI's input box at all — the worker comes up with an EMPTY
    # composer and sits idle (0 messages, $0.00), while the seed text is left behind
    # in the shell. Pressing enter afterwards submits nothing. So spawn() TYPES the
    # seed into the session after the TUI has drawn (see _type_seed) — the same
    # send-text-to-the-session mechanism the claude path already relies on, and the
    # only one observed to actually deliver a seed to opencode.
    return mode, args, session_id


_CODEX_SESSIONS = Path.home() / ".codex" / "sessions"


def _codex_rollout_exists(session_id: str) -> bool:
    """Codex stores each session as a rollout file named ...-<uuid>.jsonl under
    ~/.codex/sessions/YYYY/MM/DD/. Existence of that file is what `codex resume
    <uuid>` needs."""
    try:
        return next(_CODEX_SESSIONS.glob(f"*/*/*/rollout-*-{session_id}.jsonl"),
                    None) is not None
    except OSError:
        return True  # on doubt, attach — a bad resume fails visibly in the tab


def _preallocate_codex_session(cwd: str, title: str) -> str:
    """Mint a codex session BEFORE the worker exists and return its uuid.

    codex has no `session create` CLI, but `codex app-server` (JSON-RPC on stdio,
    works unauthenticated) does: `thread/start` returns the session uuid and its
    rollout path. TWO quirks, both verified 2026-07-10 on v0.144.1:
    - an EMPTY UNNAMED thread is discarded at shutdown — `thread/name/set` is what
      marks it worth persisting (and names it usefully in codex's resume picker);
    - the rollout file is only flushed on the server's GRACEFUL shutdown — close
      stdin and wait; terminate() discards the session.
    """
    import json as _json
    import subprocess
    import time

    proc = subprocess.Popen(
        ["codex", "app-server"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, cwd=cwd,
    )

    def send(obj):
        proc.stdin.write(_json.dumps(obj) + "\n")
        proc.stdin.flush()

    sid = path = None
    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"clientInfo": {"name": "pwc", "title": "PWC",
                                        "version": "1.0"}}})
        send({"jsonrpc": "2.0", "id": 2, "method": "thread/start",
              "params": {"cwd": cwd}})

        def read_reply(rpc_id, what):
            deadline = time.time() + 20
            while time.time() < deadline:
                line = proc.stdout.readline()
                if not line:
                    fail(f"codex app-server exited before answering {what}")
                try:
                    msg = _json.loads(line)
                except ValueError:
                    continue
                if msg.get("id") == rpc_id:
                    if "error" in msg:
                        fail(f"codex {what} failed: {msg['error'].get('message')}")
                    return msg["result"]
            fail(f"codex app-server did not answer {what} within 20s")

        thread = read_reply(2, "thread/start")["thread"]
        sid, path = thread["sessionId"], thread.get("path")
        # Name the thread — without this an empty thread is DISCARDED at shutdown.
        send({"jsonrpc": "2.0", "id": 3, "method": "thread/name/set",
              "params": {"threadId": sid, "name": title}})
        read_reply(3, "thread/name/set")
    finally:
        # Graceful shutdown — this is what makes codex WRITE the rollout file.
        try:
            proc.stdin.close()
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.terminate()
    if path and not os.path.exists(path):
        fail(f"codex session {sid} was minted but its rollout file never appeared "
             f"at {path} — cannot hand a resumable session to the worker")
    return sid


def _build_codex(*, session_id, resume, cwd, seed_prompt, model,
                 title=None, dry_run=False):
    """Session-tracked like claude/opencode, same inverted id flow as opencode
    (codex mints the uuid; pre-created here on a fresh spawn). BOTH fresh and
    resume launch `codex resume <uuid>` — fresh just resumes the empty
    pre-created session — so the uuid sits in the process argv (pgrep liveness)
    either way. `codex resume` takes -m/--model and a positional prompt."""
    if resume and session_id and _codex_rollout_exists(session_id):
        mode = "resume"
    else:
        mode = "fresh"
        session_id = ("00000000-DRYRUN-not-created" if dry_run
                      else _preallocate_codex_session(cwd, title or "PWC worker"))
    # Absolute path: `codex` is an npm global under one mise node version, so in
    # a repo whose .nvmrc pins a different node it vanishes from the worker
    # shell's PATH ("command not found", kontax-backend/smarta-systems
    # 2026-07-13). Resolve it on the coordinator's PATH at build time instead.
    codex_bin = shutil.which("codex") or "codex"
    # PWC workers in hub-backed workspaces need to reach the hub. Scope this to
    # spawned workers rather than requiring users to loosen ~/.codex/config.toml.
    # codex-cli 0.145.0 supports config overrides via -c.
    args = [codex_bin, "-c", "sandbox_workspace_write.network_access=true",
            "resume", session_id]
    if model:
        args += ["--model", model]
    if seed_prompt:
        args.append(seed_prompt)
    return mode, args, session_id


# session_tracked = the harness's session id is known at spawn time and appears in
# the worker's argv, so identity (`pwc set-session`), pgrep liveness, and resume
# all work. claude: caller-chosen uuid. opencode/codex: pre-created via their
# server APIs (they mint the id — callers record the returned one).
#
# needs_submit = the harness PRE-FILLS its seed into the input box but does not send
# it, so spawn() must type a newline once the TUI has drawn. claude auto-submits a
# positional prompt; opencode --prompt does NOT (verified live 2026-07-13: a worker
# spawned without this sat idle at 0 messages / $0.00 — the seed was visible on
# screen and never sent). codex's `resume <id> '<prompt>'` is the same shape as
# claude's and is assumed to auto-submit — VERIFY on its first interactive use, and
# if it too just pre-fills, flip this flag rather than reinventing the delivery.
_BUILDERS = {
    "claude": (_build_claude, True, False),   # (builder, session_tracked, needs_submit)
    "opencode": (_build_opencode, True, True),
    "codex": (_build_codex, True, False),
}


_SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]


def _tmux_name(task: str) -> str:
    """tmux session names reject '.' and ':'; keep it simple and predictable."""
    import re
    return "pwc-" + re.sub(r"[^A-Za-z0-9_-]", "-", task)


def tab_title(*, name, task, harness, model):
    """The iTerm2 tab title: what the task is, AND what is running it.

    With routing in play, different workers now run on different harnesses and
    models — and the tab bar is the only place you see them side by side. A title
    that says only the task leaves you unable to tell the DeepSeek worker from the
    Opus one, which is exactly the thing you're now making cost/quality tradeoffs
    about. So the runner is part of the title, not just the JSON result.

        SMT-921 · fix login retry [claude/opus]
        pwc-routing · deepseek-v4-pro [opencode]

    The model is shown bare (its dispatch id's last segment — the provider prefix is
    noise in a tab bar); the harness qualifies it. A model-less worker (harness
    default) shows just the harness.
    """
    base = name or task
    short_model = (model or "").split("/")[-1]
    runner = f"{harness}/{short_model}" if short_model else (harness or "")
    return f"{base} [{runner}]" if runner else base


def _ssh_run(target, command, *, input_text=None, what=""):
    """Run a non-interactive command on the remote host; fail loudly."""
    import subprocess
    result = subprocess.run(
        ["ssh", *_SSH_OPTS, target, command],
        input=input_text, capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        fail(f"ssh {target} failed ({what or command}): "
             f"{(result.stderr or result.stdout).strip()[:200]}")
    return result.stdout


def build_remote_claude_command(*, ssh_target, session_id, resume, cwd,
                                seed_prompt, model, task, dry_run):
    """Build the ssh+tmux launch for a REMOTE claude worker.

    Fresh AND resume run inside `tmux new-session -A -s pwc-<task>` on the remote
    host — `-A` attaches if the session already exists, so the same command is
    also how the user reopens a live worker's viewport. The seed is staged to
    `~/.pwc/seeds/<uuid>.txt` on the remote host (via ssh stdin, never nested
    quoting) and consumed with `$(cat …)` as claude's positional prompt.
    `~/.local/bin` is prepended to PATH inside the session because claude's
    native install lives there and non-login shells don't have it.
    """
    remote_transcript = (f"$HOME/.claude/projects/"
                         f"{cwd.replace('/', '-')}/{session_id}.jsonl")
    mode = "fresh"
    if resume and not dry_run:
        exists = _ssh_run(
            ssh_target, f'test -f "{remote_transcript}" && echo yes || echo no',
            what="remote transcript check").strip() == "yes"
        if exists:
            mode = "resume"

    inner_parts = ['export PATH="$HOME/.local/bin:$PATH"',
                   f"cd {shlex.quote(cwd)}"]
    claude_cmd = "claude"
    if model:
        claude_cmd += f" --model {shlex.quote(model)}"
    if mode == "resume":
        claude_cmd += f" --resume {session_id}"
    else:
        claude_cmd += f" --session-id {session_id}"
    if seed_prompt:
        seed_path = f"$HOME/.pwc/seeds/{session_id}.txt"
        if not dry_run:
            _ssh_run(ssh_target, 'mkdir -p "$HOME/.pwc/seeds" && '
                                 f'cat > "{seed_path}"',
                     input_text=seed_prompt, what="seed staging")
        claude_cmd += f' "$(cat "{seed_path}")"'
    inner_parts.append(claude_cmd)
    inner = " && ".join(inner_parts)

    tmux_cmd = f"tmux new-session -A -s {_tmux_name(task)} {shlex.quote(inner)}"
    command = f"ssh -t {shlex.quote(ssh_target)} {shlex.quote(tmux_cmd)}"
    return mode, command


def build_command(*, harness, session_id, resume, cwd, seed_prompt=None,
                  model=None, title=None, dry_run=False):
    """Return (mode, launch_command, session_id, session_tracked, needs_submit) for
    the task's harness. `session_id` in the result is the EFFECTIVE id (opencode mints
    its own on a fresh spawn — record that one), or None for untracked harnesses.
    `needs_submit` is True when the harness only PRE-FILLS the seed and spawn() must
    press enter for it (see _BUILDERS)."""
    if harness not in _BUILDERS:
        fail(f"unknown harness {harness!r} — known: {', '.join(sorted(_BUILDERS))}")
    builder, session_tracked, needs_submit = _BUILDERS[harness]
    kwargs = dict(session_id=session_id, resume=resume, cwd=cwd,
                  seed_prompt=seed_prompt, model=model)
    if harness in ("opencode", "codex"):
        kwargs.update(title=title, dry_run=dry_run)
    mode, args, effective_id = builder(**kwargs)
    inner = f"cd {shlex.quote(cwd)} && {shlex.join(args)}"
    # Only type when there IS a seed and the harness can't carry it on the CLI.
    return mode, inner, effective_id, session_tracked, bool(seed_prompt) and needs_submit


_TYPE_DOMAIN = {
    "pr-review": "code-review",
    "jira": "implementation",
    "build-or-feature-work": "implementation",
    "slack": "ops-comms",
    "research": "research-writing",
    "investigation": "research-writing",
}


def _unrouted_error(task_type: str, task_id: str) -> None:
    """Emit the route template and exit nonzero — the unfilled template the
    coordinator pastes, fills in the judgment flags, and runs."""
    domain = _TYPE_DOMAIN.get(task_type, "implementation")
    fail(
        f"task {task_id!r} has no routing — harness/model not set.\n"
        f"\n"
        f"  pwc route --domain {domain} --reasoning <1-5>"
        f" --verifiability <1-5> --risk none\n"
        f"\n"
        f"then store the result:\n"
        f"\n"
        f"  pwc update-task --task {task_id} --harness <h> --model <m>\n"
        f"\n"
        f"and re-run this spawn command.\n"
        f"\n"
        f"to override routing manually (rare): "
        f"pass --force-model --force-reason \"<why>\" --harness <h> --model <m>"
    )


def _load_task_row(workspace_root, task_id):
    """Fetch one task row through the workspace's configured store.

    Local store: direct sqlite read (as before). Hub store: `detail` via
    hub_client — spawn must work identically against both backends
    (regression 2026-07-16: hub-backed workspaces made every spawn crash).
    Returns a plain dict, or None if the task doesn't exist.
    """
    from _common import store_config
    store = store_config(workspace_root)
    if store.get("store") == "hub":
        import hub_client
        detail = hub_client.call("detail", {"task": task_id}, store)
        return detail.get("task") if isinstance(detail, dict) else None
    import pwc_db
    conn = pwc_db.connect(workspace_root)
    row = conn.execute(
        "SELECT * FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    return dict(row) if row is not None else None


def _log_task_event(workspace_root, task_id, kind, detail_text,
                    source="coordinator"):
    """Append an event to a task through the workspace's configured store."""
    from _common import store_config
    store = store_config(workspace_root)
    if store.get("store") == "hub":
        import hub_client
        hub_client.call("log-event", {
            "task": task_id, "source": source,
            "kind": kind, "detail": detail_text,
        }, store)
        return
    import pwc_db
    conn = pwc_db.connect(workspace_root)
    with conn:
        ts = now_iso()
        conn.execute(
            "INSERT INTO events (task_id, at, source, kind, detail) "
            "VALUES (?,?,?,?,?)",
            (task_id, ts, source, kind, detail_text),
        )
        conn.execute(
            "UPDATE tasks SET last_event_at = ? WHERE id = ?", (ts, task_id),
        )


def resolve_routing(args, task_row, workspace_root):
    """Return (harness, model) for the worker, or fail with an actionable error.

    Normal path: the task's stored harness/model (set by pwc route). Force path:
    explicit --force-model + --force-reason + --harness + --model, logged as an
    audit event. Error path: --harness/--model without --force-model, or no
    routing on the task.
    """
    task_harness = task_row["harness"]
    task_model = task_row["model"]
    task_type = task_row["type"]
    task_id = task_row["id"]

    if args.force_model:
        if not args.force_reason:
            fail("--force-reason is required with --force-model "
                 "(explain why the override is necessary)")
        if not args.harness or not args.model:
            fail("--harness AND --model are both required with --force-model")

        _log_task_event(
            workspace_root, task_id, "note",
            f"FORCED model {args.harness}/{args.model} "
            f"over routed {task_harness}/{task_model}: {args.force_reason}",
        )
        return args.harness, args.model

    if args.harness or args.model:
        fail("--harness and --model require --force-model + --force-reason "
             "to override routing. Run `pwc route` to set routing normally, "
             "or add --force-model --force-reason \"<why>\" to override.")

    if not task_harness or not task_model:
        _unrouted_error(task_type, task_id)

    return task_harness, task_model


# Tails that mark "the shell is sitting at an interactive prompt" on the last
# non-empty screen line — the fallback readiness signal when iTerm2 shell
# integration isn't installed in the worker's shell.
_PROMPT_TAILS = ("❯", "➜", "$", "%", ">")


async def _wait_for_shell_prompt(iterm2, connection, session, timeout=15.0):
    """Wait until the tab's shell is at an interactive prompt before typing.

    Typing into the TTY while the shell is still starting up goes through the
    kernel's canonical-mode line discipline, which caps a single input line at
    MAX_CANON (1024 bytes on macOS) and silently drops the rest — so a long
    launch command sent too early arrives truncated, leaving a broken command
    sitting unexecuted at the prompt (observed 2026-07-13: a ~1.4KB codex seed
    cut mid-quote). Once the shell's line editor owns the TTY (raw mode), the
    cap no longer applies.

    Readiness signals, polled together every 200ms:
    - iTerm2 shell integration's prompt record (exact, when installed);
    - fallback: the last non-empty screen line looks like a prompt.
    On timeout, proceed anyway (the pre-guard behavior) and report it.
    Returns "shell-integration" | "screen" | "timeout" for placement.
    """
    import asyncio

    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        try:
            if await iterm2.async_get_last_prompt(connection, session.session_id):
                return "shell-integration"
        except Exception:  # noqa: BLE001 — no integration / transient API error
            pass
        try:
            contents = await session.async_get_screen_contents()
            last = ""
            for i in range(contents.number_of_lines):
                line = contents.line(i).string.rstrip()
                if line:
                    last = line
            if last.endswith(_PROMPT_TAILS):
                return "screen"
        except Exception:  # noqa: BLE001 — screen read is best-effort
            pass
        await asyncio.sleep(0.2)
    return "timeout"


def spawn(*, cwd, command, seed_in_command=False, title=None, needs_submit=False,
          typed_seed=None):
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

        # Wait for the shell to reach its prompt before typing — sending while
        # the shell is still starting goes through the TTY's canonical-mode
        # line buffer, which truncates the command at 1024 bytes (MAX_CANON).
        # Done after the focus restore so the user isn't parked on the new tab
        # while we wait. On "timeout" we type anyway (pre-guard behavior).
        placement["shell_ready"] = await _wait_for_shell_prompt(
            iterm2, connection, session)

        # Launch claude in the new shell. The seed (if any) is already part of
        # `command` as claude's positional prompt, so claude auto-submits it on
        # startup — nothing more to type.
        await session.async_send_text(command + "\r")
        placement["seed"] = "submitted" if seed_in_command else "skipped"

        # Harnesses whose CLI cannot carry the seed (opencode) get it TYPED into
        # their TUI once it has drawn. `--prompt` does not work for this: verified
        # live 2026-07-13 that it leaves the composer empty and the worker idle at
        # $0.00. Typing into the session is the same mechanism the claude path
        # already trusts, and it is the only one observed to actually land.
        #
        # We poll for the TUI's own chrome rather than sleeping a fixed time: the
        # shell prompt appearing is NOT enough (the harness may still be booting, and
        # text sent then lands in the shell, not the app — which is exactly the
        # failure we're fixing).
        if needs_submit and typed_seed:
            import asyncio
            loop = asyncio.get_event_loop()
            deadline = loop.time() + 45
            drawn = False
            while loop.time() < deadline:
                await asyncio.sleep(0.5)
                try:
                    contents = await session.async_get_screen_contents()
                    screen = "\n".join(
                        contents.line(i).string
                        for i in range(contents.number_of_lines))
                except Exception:  # noqa: BLE001 — screen read is best-effort
                    continue
                # opencode's TUI paints its model/agent banner ("Build · <model>")
                # and a box-drawn composer. Either is proof the app owns the TTY.
                if "Build ·" in screen or "┃" in screen or "▀▀▀" in screen:
                    drawn = True
                    break
            if drawn:
                await asyncio.sleep(0.6)  # let the composer take focus
                await session.async_send_text(typed_seed)
                await asyncio.sleep(0.3)
                await session.async_send_text("\r")
            placement["seed"] = "typed" if drawn else "not-typed"
            placement["tui_drawn"] = drawn

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
    p.add_argument("--force-model", action="store_true",
                   help="override the task's routed harness/model "
                        "(requires --force-reason, --harness, --model)")
    p.add_argument("--force-reason", help="why the override is necessary")
    p.add_argument("--harness", help="harness override (requires --force-model)")
    p.add_argument("--model", help="model override (requires --force-model)")
    p.add_argument("--ssh", help="run the worker on this remote host (ssh target) "
                                 "inside tmux; --cwd must be the REMOTE path. "
                                 "claude harness only for now.")
    p.add_argument("--runhost", help="display name of the remote host (recorded in "
                                     "the result; the task's runhost field)")
    p.add_argument("--session-id")
    p.add_argument("--resume", action="store_true")
    p.add_argument("--prompt-file")
    p.add_argument("--prompt", help="seed prompt text, or '-' to read from stdin")
    p.add_argument("--name")
    p.add_argument("--dry-run", action="store_true",
                   help="print the command without opening a tab (for testing)")
    args = p.parse_args(argv)

    cwd = str(Path(args.cwd).expanduser())
    if not args.dry_run and not os.path.isdir(cwd):
        fail(f"--cwd does not exist: {cwd}")

    workspace_root = str(find_workspace_root(cwd))
    task_row = _load_task_row(workspace_root, args.task)
    if task_row is None:
        fail(f"no task {args.task!r} in workspace {workspace_root}")

    harness, model = resolve_routing(args, task_row, workspace_root)

    session_id = args.session_id or str(uuid.uuid4())

    seed_prompt = None
    if args.prompt_file:
        seed_prompt = Path(args.prompt_file).read_text()
    elif args.prompt == "-":
        seed_prompt = sys.stdin.read()
    elif args.prompt:
        seed_prompt = args.prompt

    if args.ssh:
        # Remote worker: cwd is a REMOTE path — validate remotely, build ssh+tmux.
        if harness != "claude":
            fail(f"remote workers support only the claude harness for now "
                 f"(got {harness!r}) — opencode/codex pre-allocation would "
                 f"need their server APIs run on the remote host")
        cwd = args.cwd
        if not args.dry_run:
            _ssh_run(args.ssh, f'test -d {shlex.quote(cwd)}',
                     what=f"remote cwd check: {cwd}")
        mode, command = build_remote_claude_command(
            ssh_target=args.ssh, session_id=session_id, resume=args.resume,
            cwd=cwd, seed_prompt=seed_prompt, model=model,
            task=args.task, dry_run=args.dry_run,
        )
        session_tracked = True
        needs_submit = False  # remote is claude-only; claude auto-submits
    else:
        if not args.dry_run and shutil.which(harness) is None:
            fail(f"harness {harness!r} is not installed (not on PATH)")
        mode, command, session_id, session_tracked, needs_submit = build_command(
            harness=harness, session_id=session_id, resume=args.resume,
            cwd=cwd, seed_prompt=seed_prompt, model=model,
            title=args.name or args.task, dry_run=args.dry_run,
        )
    # The seed rides in the launch command as the harness's prompt argument
    # (auto-submitted) on BOTH a fresh spawn and a resume-with-follow-up — the only
    # difference is meaning (fresh = the task seed; resume = a follow-up ask). A
    # resume with no seed carries no prompt. `seed_in_command` tracks whether
    # `command` actually contains a prompt, so spawn() reports the right seed status.
    # For a typed-seed harness (opencode) the seed is NOT in the launch command —
    # spawn() types it into the TUI — so it must not be reported as such.
    seed_in_command = bool(seed_prompt) and not needs_submit

    title = tab_title(name=args.name, task=args.task,
                      harness=harness, model=model)

    result = {
        "harness": harness,
        "model": model,
        "tab_title": title,
        # The EFFECTIVE session id — for opencode a fresh spawn mints it here, so
        # the dispatch skill must record THIS value, not an id it generated.
        # None for untracked harnesses (no `pwc set-session` for those).
        "session_id": session_id if session_tracked else None,
        "session_tracked": session_tracked,
        "cwd": cwd,
        "mode": mode,
        "command": command,
    }
    if args.ssh:
        result["runhost"] = args.runhost or args.ssh
        result["ssh"] = args.ssh
        result["tmux_session"] = _tmux_name(args.task)
        # How to reopen the viewport if the tab is closed (worker keeps running).
        result["attach_command"] = (
            f"ssh -t {shlex.quote(args.ssh)} tmux attach -t {_tmux_name(args.task)}")
    elif harness == "claude":
        result["transcript_expected"] = str(transcript_path(cwd, session_id))

    if args.dry_run:
        result["dry_run"] = True
        emit(result)
        return

    placement = spawn(cwd=cwd, command=command, seed_in_command=seed_in_command,
                      title=title, needs_submit=needs_submit,
                      typed_seed=seed_prompt if needs_submit else None)
    result.update(placement)
    emit(result)


if __name__ == "__main__":
    main(sys.argv[1:])
