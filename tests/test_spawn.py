"""spawn.py routing enforcement — refuses unrouted, uses stored routing,
force-model override with audit log, reroute clears fields."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
SPAWN = SCRIPTS / "spawn.py"
TASKDB = SCRIPTS / "taskdb.py"
PWC_DB = SCRIPTS / "pwc_db.py"
_COMMON = SCRIPTS / "_common.py"

PYTHON = sys.executable
ENV = {**os.environ, "PYTHONPATH": str(SCRIPTS)}


def _run(*args, **kwargs):
    """Run a script with PYTHONPATH=scripts. Returns (returncode, stdout, stderr)."""
    result = subprocess.run(
        [PYTHON, *args], capture_output=True, text=True,
        timeout=30, env=ENV, **kwargs,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def _run_taskdb(workspace, *args):
    return _run(str(TASKDB), "--workspace", workspace, *args)


class _Workspace:
    """A temp directory with an initialized PWC task database."""

    def __init__(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "workdir").mkdir()
        _run_taskdb(str(self.root), "init")

    def add_task(self, task_id, *, harness=None, model=None,
                 title="test", type="build-or-feature-work", **kw):
        args = ["add-task", "--task", task_id, "--type", type, "--title", title]
        if harness is not None:
            args += ["--harness", harness]
        if model is not None:
            args += ["--model", model]
        if kw.get("status"):
            args += ["--status", kw["status"]]
        _run_taskdb(str(self.root), *args)

    def set_routing(self, task_id, harness, model):
        _run_taskdb(str(self.root), "update-task", "--task", task_id,
                    "--harness", harness, "--model", model)

    def reroute(self, task_id, reason=None):
        args = ["reroute", "--task", task_id]
        if reason:
            args += ["--reason", reason]
        return _run_taskdb(str(self.root), *args)

    def events(self, task_id):
        rc, out, _ = _run_taskdb(str(self.root), "events", "--task", task_id)
        return out

    def close(self):
        import shutil
        shutil.rmtree(self.root)


@pytest.fixture
def ws():
    w = _Workspace()
    yield w
    w.close()


# ── spawn refuses unrouted ────────────────────────────────────────────────────
def test_spawn_refuses_unrouted(ws):
    ws.add_task("no-route")
    rc, _, err = _run(str(SPAWN), "--task", "no-route",
                      "--cwd", str(ws.root / "workdir"), "--dry-run")
    assert rc != 0
    assert "has no routing" in err
    assert "pwc route --domain" in err
    assert "pwc update-task" in err
    assert "pwc spawn" not in err.lower() or "re-run" in err.lower()


# ── spawn uses stored routing ─────────────────────────────────────────────────
def test_spawn_uses_stored_routing(ws):
    ws.add_task("routed", harness="opencode", model="openrouter/test/model")
    rc, out, err = _run(str(SPAWN), "--task", "routed",
                        "--cwd", str(ws.root / "workdir"), "--dry-run")
    assert rc == 0
    assert '"harness": "opencode"' in out
    assert '"openrouter/test/model"' in out


def test_codex_spawn_enables_network_only_for_worker_sandbox(ws):
    ws.add_task("codex-route", harness="codex", model="gpt-5")
    rc, out, err = _run(str(SPAWN), "--task", "codex-route",
                        "--cwd", str(ws.root / "workdir"), "--dry-run")
    assert rc == 0, err
    assert "-c sandbox_workspace_write.network_access=true resume" in out


# ── --force-model requires --force-reason ──────────────────────────────────────
def test_force_model_requires_reason(ws):
    ws.add_task("routed", harness="opencode", model="openrouter/test/model")
    rc, _, err = _run(str(SPAWN), "--task", "routed",
                      "--cwd", str(ws.root / "workdir"),
                      "--force-model", "--harness", "claude", "--model", "opus",
                      "--dry-run")
    assert rc != 0
    assert "force-reason" in err.lower()


# ── --force-model requires both harness AND model ──────────────────────────────
def test_force_model_requires_both(ws):
    ws.add_task("routed", harness="opencode", model="openrouter/test/model")
    rc, _, err = _run(str(SPAWN), "--task", "routed",
                      "--cwd", str(ws.root / "workdir"),
                      "--force-model", "--force-reason", "test",
                      "--model", "openrouter/test/other",
                      "--dry-run")
    assert rc != 0
    assert "harness" in err.lower()


def test_force_model_requires_both_harness_only(ws):
    ws.add_task("routed", harness="opencode", model="openrouter/test/model")
    rc, _, err = _run(str(SPAWN), "--task", "routed",
                      "--cwd", str(ws.root / "workdir"),
                      "--force-model", "--force-reason", "test",
                      "--harness", "claude",
                      "--dry-run")
    assert rc != 0
    assert "model" in err.lower()


# ── --force-model with reason uses override and logs audit event ───────────────
def test_force_model_logs_audit(ws):
    ws.add_task("routed", harness="opencode", model="openrouter/test/model")
    rc, out, _ = _run(str(SPAWN), "--task", "routed",
                      "--cwd", str(ws.root / "workdir"),
                      "--force-model", "--force-reason", "testing override",
                      "--harness", "claude", "--model", "opus",
                      "--dry-run")
    assert rc == 0
    assert '"harness": "claude"' in out
    assert '"model": "opus"' in out
    # The audit event should be in the task's event log
    events = ws.events("routed")
    assert "FORCED model claude/opus" in events
    assert "testing override" in events


# ── bare --harness without --force-model is an error ───────────────────────────
def test_bare_harness_without_force_errors(ws):
    ws.add_task("routed", harness="opencode", model="openrouter/test/model")
    rc, _, err = _run(str(SPAWN), "--task", "routed",
                      "--cwd", str(ws.root / "workdir"),
                      "--harness", "claude", "--dry-run")
    assert rc != 0
    assert "force-model" in err.lower()


def test_bare_model_without_force_errors(ws):
    ws.add_task("routed", harness="opencode", model="openrouter/test/model")
    rc, _, err = _run(str(SPAWN), "--task", "routed",
                      "--cwd", str(ws.root / "workdir"),
                      "--model", "opus", "--dry-run")
    assert rc != 0
    assert "force-model" in err.lower()


# ── reroute clears harness/model ──────────────────────────────────────────────
def test_reroute_clears_routing(ws):
    ws.add_task("routed", harness="opencode", model="openrouter/test/model")
    rc, out, _ = ws.reroute("routed", reason="re-scoped to a new chapter")
    assert rc == 0
    # The task row should show harness/model as null
    assert '"harness": null' in out or '"harness": None' in out.lower()
    assert '"model": null' in out or '"model": None' in out.lower()
    assert "_hint" in out

    # Spawn should now refuse
    rc, _, err = _run(str(SPAWN), "--task", "routed",
                      "--cwd", str(ws.root / "workdir"), "--dry-run")
    assert rc != 0
    assert "has no routing" in err


# ── type-to-domain mapping in the route template ───────────────────────────────
def test_unrouted_template_maps_type_to_domain(ws):
    ws.add_task("pr", type="pr-review")
    rc, _, err = _run(str(SPAWN), "--task", "pr",
                      "--cwd", str(ws.root / "workdir"), "--dry-run")
    assert rc != 0
    assert "code-review" in err

    ws.add_task("jira-task", type="jira")
    rc, _, err = _run(str(SPAWN), "--task", "jira-task",
                      "--cwd", str(ws.root / "workdir"), "--dry-run")
    assert rc != 0
    assert "implementation" in err

    ws.add_task("research-task", type="research")
    rc, _, err = _run(str(SPAWN), "--task", "research-task",
                      "--cwd", str(ws.root / "workdir"), "--dry-run")
    assert rc != 0
    assert "research-writing" in err


# ── model weight survives fetch (merged_models applies cost_weight) ────────────
def test_cost_weight_from_overlay_merged():
    import json as _json
    from models import merged_models, _seed_table

    data = _seed_table()
    data["overlay"] = {
        "opencode/deepseek-v4-pro": {"cost_weight": 0.8},
        "opencode/glm-5.2": {"cost_weight": 1.5},
    }
    merged = merged_models(data)
    by_key = {m["key"]: m for m in merged}
    assert by_key["opencode/glm-5.2"]["cost_weight"] == 1.5
    assert by_key["opencode/deepseek-v4-pro"]["cost_weight"] == 0.8
    # A model without overlay entry defaults to 1.0
    assert by_key["claude/sonnet"]["cost_weight"] == 1.0


# ── --resume is strict: no transcript => fail, never a silent fresh session ────
def test_resume_without_transcript_fails_loudly(ws):
    """A --resume whose transcript can't be found must FAIL.

    Regression 2026-08-07: it silently fell through to a fresh session, so a
    resume of a real session (whose transcript lived under a different cwd slug,
    e.g. after a workspace rename) reported mode=fresh and threw the context
    away. Resuming is asked for exactly when that context is the point.
    """
    ws.add_task("resume-me", harness="claude", model="opus")
    rc, out, err = _run(str(SPAWN), "--task", "resume-me",
                        "--cwd", str(ws.root / "workdir"), "--dry-run",
                        "--session-id", "00000000-dead-beef-0000-000000000000",
                        "--resume")
    assert rc != 0, f"expected failure, got success with: {out}"
    assert "no transcript" in err
    assert "drop --resume" in err
    assert '"mode": "fresh"' not in out


def test_no_resume_flag_still_starts_fresh(ws):
    """Without --resume a missing transcript is normal — that's a fresh spawn."""
    ws.add_task("fresh-one", harness="claude", model="opus")
    rc, out, _ = _run(str(SPAWN), "--task", "fresh-one",
                      "--cwd", str(ws.root / "workdir"), "--dry-run",
                      "--session-id", "00000000-dead-beef-0000-000000000001")
    assert rc == 0
    assert '"mode": "fresh"' in out


# ── remote (--ssh) resolves the task store LOCALLY, not from the remote --cwd ──
def test_remote_spawn_resolves_store_from_local_root(ws):
    """--cwd is a REMOTE path and must not be used to find the local task store.

    Regression 2026-08-07: find_workspace_root(--cwd) walked a path that doesn't
    exist locally, fell back to it, store_config() then defaulted to
    {"store": "local"} and a hub-backed workspace died on a missing sqlite file.
    hub + --ssh — the Mac mini's exact setup — could not spawn at all.
    """
    ws.add_task("remote-task", harness="claude", model="opus")
    rc, out, err = _run(str(SPAWN), "--task", "remote-task",
                        "--ssh", "somehost",
                        "--cwd", "/Users/someoneelse/on/the/remote/host",
                        "--local-root", str(ws.root / "workdir"),
                        "--dry-run")
    assert rc == 0, f"remote dry-run failed: {err}"
    assert '"harness": "claude"' in out
    # the launch targets the REMOTE path, even though the store was local
    assert "/Users/someoneelse/on/the/remote/host" in out


def test_remote_spawn_defaults_local_root_to_cwd(ws):
    """Without --local-root the workspace comes from the invocation directory."""
    ws.add_task("remote-task-2", harness="claude", model="opus")
    rc, out, err = _run(str(SPAWN), "--task", "remote-task-2",
                        "--ssh", "somehost",
                        "--cwd", "/Users/someoneelse/elsewhere",
                        "--dry-run", cwd=str(ws.root / "workdir"))
    assert rc == 0, f"remote dry-run failed: {err}"
    assert '"harness": "claude"' in out


def test_remote_spawn_does_not_require_cwd_to_exist_locally(ws):
    """A remote --cwd is a path on the OTHER machine; local existence is irrelevant."""
    ws.add_task("remote-task-3", harness="claude", model="opus")
    rc, out, err = _run(str(SPAWN), "--task", "remote-task-3",
                        "--ssh", "somehost",
                        "--cwd", "/definitely/not/here/locally",
                        "--local-root", str(ws.root / "workdir"),
                        "--dry-run")
    assert rc == 0, f"remote dry-run failed: {err}"
    assert "does not exist" not in err
