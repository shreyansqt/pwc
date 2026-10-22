"""spawn.py routing enforcement — refuses unrouted, uses stored routing,
force-model override with audit log, reroute clears fields."""

from __future__ import annotations

import json
import os
import sqlite3
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

sys.path.insert(0, str(SCRIPTS))
import spawn as spawn_module  # noqa: E402


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

    def set_skill_hints(self, hints):
        config = {"sources": {}, "skill_hints": hints}
        pwc_dir = self.root / ".pwc"
        pwc_dir.mkdir(exist_ok=True)
        (pwc_dir / "sources.json").write_text(json.dumps(config))

    def reroute(self, task_id, reason=None):
        args = ["reroute", "--task", task_id]
        if reason:
            args += ["--reason", reason]
        return _run_taskdb(str(self.root), *args)

    def events(self, task_id):
        rc, out, _ = _run_taskdb(str(self.root), "events", "--task", task_id)
        return out

    def log_event(self, task_id, kind, detail):
        rc, _, err = _run_taskdb(
            str(self.root), "log-event", "--task", task_id,
            "--source", "coordinator", "--kind", kind, "--detail", detail,
        )
        assert rc == 0, err

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
    assert "pwc route --task no-route --domain" in err
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


def _codex_state_db(path, *, session_id, first_message="", preview="", tokens=0):
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE threads ("
            "id TEXT PRIMARY KEY, first_user_message TEXT NOT NULL, "
            "preview TEXT NOT NULL, tokens_used INTEGER NOT NULL)"
        )
        conn.execute(
            "INSERT INTO threads VALUES (?, ?, ?, ?)",
            (session_id, first_message, preview, tokens),
        )
        conn.commit()
    finally:
        conn.close()


def test_codex_resume_detection_uses_sqlite_without_rollout(monkeypatch, tmp_path):
    session_id = "00000000-0000-0000-0000-000000000151"
    state_db = tmp_path / "state_5.sqlite"
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    _codex_state_db(
        state_db, session_id=session_id,
        first_message="PWC setup", preview="PWC setup", tokens=42,
    )
    monkeypatch.setattr(spawn_module, "_CODEX_STATE_DB", state_db)
    monkeypatch.setattr(spawn_module, "_CODEX_SESSIONS", sessions)

    assert spawn_module._codex_session_exists(session_id) is True


def test_codex_resume_detection_rejects_empty_sqlite_thread(
        monkeypatch, tmp_path):
    session_id = "00000000-0000-0000-0000-000000000152"
    state_db = tmp_path / "state_5.sqlite"
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    _codex_state_db(state_db, session_id=session_id)
    monkeypatch.setattr(spawn_module, "_CODEX_STATE_DB", state_db)
    monkeypatch.setattr(spawn_module, "_CODEX_SESSIONS", sessions)

    assert spawn_module._codex_session_exists(session_id) is False


def test_codex_resume_detection_keeps_legacy_rollout_fallback(
        monkeypatch, tmp_path):
    session_id = "00000000-0000-0000-0000-000000000153"
    state_db = tmp_path / "state_5.sqlite"
    sessions = tmp_path / "sessions"
    rollout_dir = sessions / "2026" / "08" / "31"
    rollout_dir.mkdir(parents=True)
    _codex_state_db(state_db, session_id="different-thread")
    (rollout_dir / f"rollout-old-{session_id}.jsonl").touch()
    monkeypatch.setattr(spawn_module, "_CODEX_STATE_DB", state_db)
    monkeypatch.setattr(spawn_module, "_CODEX_SESSIONS", sessions)

    assert spawn_module._codex_session_exists(session_id) is True


def test_codex_preallocation_completes_setup_turn(monkeypatch, tmp_path):
    sent = []
    session_id = "00000000-0000-0000-0000-000000000154"

    class FakeStdin:
        def write(self, line):
            sent.append(json.loads(line))

        def flush(self):
            pass

        def close(self):
            pass

    class FakeStdout:
        def __init__(self):
            self.lines = iter([
                json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}) + "\n",
                json.dumps({
                    "jsonrpc": "2.0", "id": 2,
                    "result": {"thread": {"sessionId": session_id}},
                }) + "\n",
                json.dumps({"jsonrpc": "2.0", "id": 3, "result": {}}) + "\n",
                json.dumps({
                    "jsonrpc": "2.0", "method": "turn/completed",
                    "params": {
                        "threadId": session_id,
                        "turn": {"status": "completed"},
                    },
                }) + "\n",
                json.dumps({"jsonrpc": "2.0", "id": 4, "result": {}}) + "\n",
            ])

        def readline(self):
            return next(self.lines, "")

    class FakeProcess:
        def __init__(self):
            self.stdin = FakeStdin()
            self.stdout = FakeStdout()

        def wait(self, timeout):
            return 0

        def terminate(self):
            pass

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: FakeProcess())

    result = spawn_module._preallocate_codex_session(
        str(tmp_path), "Test worker", "gpt-test",
    )

    assert result == session_id
    assert [message["method"] for message in sent] == [
        "initialize", "thread/start", "turn/start", "thread/name/set",
    ]
    assert sent[2]["params"]["model"] == "gpt-test"
    assert sent[2]["params"]["input"] == [
        {"type": "text", "text": spawn_module._CODEX_PRIME_PROMPT},
    ]


def test_codex_resume_missing_thread_fails_instead_of_starting_fresh(
        monkeypatch, capsys):
    monkeypatch.setattr(spawn_module, "_codex_session_exists", lambda _: False)

    with pytest.raises(SystemExit):
        spawn_module._build_codex(
            session_id="missing", resume=True, cwd="/tmp", seed_prompt=None,
            model="gpt-test", dry_run=False,
        )

    assert "no resumable codex thread" in capsys.readouterr().err


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


# ── fresh seeds enforce configured skill hints ────────────────────────────────
def test_fresh_spawn_refuses_seed_missing_configured_skill(ws):
    ws.set_skill_hints({"jira": ["start-ticket"]})
    ws.add_task("jira-task", type="jira", harness="opencode", model="test/model")

    rc, _, err = _run(str(SPAWN), "--task", "jira-task",
                      "--cwd", str(ws.root / "workdir"), "--dry-run",
                      "--prompt", "Implement the ticket from its plan.")

    assert rc != 0
    assert "omits configured skill hint(s): start-ticket" in err
    assert "--skip-skill-hint --skip-skill-reason" in err


@pytest.mark.parametrize("mention", ["/start-ticket", "start-ticket"])
def test_fresh_spawn_accepts_skill_with_or_without_slash(ws, mention):
    ws.set_skill_hints({"jira": ["start-ticket"]})
    ws.add_task("jira-task", type="jira", harness="opencode", model="test/model")

    rc, _, err = _run(str(SPAWN), "--task", "jira-task",
                      "--cwd", str(ws.root / "workdir"), "--dry-run",
                      "--prompt", f"Run {mention} first.")

    assert rc == 0, err


def test_skill_name_must_be_a_standalone_mention(ws):
    ws.set_skill_hints({"jira": ["start-ticket"]})
    ws.add_task("jira-task", type="jira", harness="opencode", model="test/model")

    rc, _, err = _run(str(SPAWN), "--task", "jira-task",
                      "--cwd", str(ws.root / "workdir"), "--dry-run",
                      "--prompt", "Run start-ticket-extra first.")

    assert rc != 0
    assert "start-ticket" in err


def test_fresh_spawn_requires_every_configured_skill(ws):
    ws.set_skill_hints({"pr-review": ["code-review", "request-review"]})
    ws.add_task("review", type="pr-review", harness="opencode", model="test/model")

    rc, _, err = _run(str(SPAWN), "--task", "review",
                      "--cwd", str(ws.root / "workdir"), "--dry-run",
                      "--prompt", "Run /code-review first.")

    assert rc != 0
    assert "request-review" in err
    assert "code-review" not in err


def test_task_type_without_skill_hint_is_not_blocked(ws):
    ws.set_skill_hints({"jira": ["start-ticket"]})
    ws.add_task("plain-task", type="task", harness="opencode", model="test/model")

    rc, _, err = _run(str(SPAWN), "--task", "plain-task",
                      "--cwd", str(ws.root / "workdir"), "--dry-run",
                      "--prompt", "Handle this task.")

    assert rc == 0, err


def test_resume_does_not_enforce_skill_hint(ws):
    ws.set_skill_hints({"jira": ["start-ticket"]})
    ws.add_task("jira-task", type="jira", harness="opencode", model="test/model")

    rc, _, err = _run(str(SPAWN), "--task", "jira-task",
                      "--cwd", str(ws.root / "workdir"), "--dry-run", "--resume",
                      "--session-id", "ses_existing", "--prompt", "Continue.")

    assert rc == 0, err
    assert "skill hint" not in err.lower()


def test_skip_skill_hint_requires_reason(ws):
    ws.set_skill_hints({"jira": ["start-ticket"]})
    ws.add_task("jira-task", type="jira", harness="opencode", model="test/model")

    rc, _, err = _run(str(SPAWN), "--task", "jira-task",
                      "--cwd", str(ws.root / "workdir"), "--dry-run",
                      "--prompt", "Use a different process.", "--skip-skill-hint")

    assert rc != 0
    assert "--skip-skill-reason is required" in err


def test_skip_skill_hint_logs_missing_skills_and_reason(ws):
    ws.set_skill_hints({"jira": ["start-ticket"]})
    ws.add_task("jira-task", type="jira", harness="opencode", model="test/model")

    rc, _, err = _run(str(SPAWN), "--task", "jira-task",
                      "--cwd", str(ws.root / "workdir"), "--dry-run",
                      "--prompt", "Use a different process.",
                      "--skip-skill-hint", "--skip-skill-reason",
                      "incident recovery requires the existing branch")

    assert rc == 0, err
    events = ws.events("jira-task")
    assert "SKIPPED configured skill hint(s) start-ticket" in events
    assert "incident recovery requires the existing branch" in events


def test_stored_skill_event_wins_over_the_type_hint(ws):
    ws.set_skill_hints({"calendar": ["calendar-skill"]})
    ws.add_task("prep", type="calendar", harness="opencode", model="test/model")
    ws.log_event("prep", "skill", "refinement-prep")

    rc, _, err = _run(str(SPAWN), "--task", "prep",
                      "--cwd", str(ws.root / "workdir"), "--dry-run",
                      "--prompt", "Run /refinement-prep for this meeting.")

    assert rc == 0, err


def test_stored_skill_event_does_not_accept_only_the_type_skill(ws):
    ws.set_skill_hints({"calendar": ["calendar-skill"]})
    ws.add_task("prep", type="calendar", harness="opencode", model="test/model")
    ws.log_event("prep", "skill", "refinement-prep")

    rc, _, err = _run(str(SPAWN), "--task", "prep",
                      "--cwd", str(ws.root / "workdir"), "--dry-run",
                      "--prompt", "Run /calendar-skill first.")

    assert rc != 0
    assert "omits stored skill(s): refinement-prep" in err
    assert "calendar-skill" not in err


def test_newest_skill_event_wins(ws):
    ws.set_skill_hints({})
    ws.add_task("prep", type="calendar", harness="opencode", model="test/model")
    ws.log_event("prep", "skill", "start-ticket")
    ws.log_event("prep", "skill", "refinement-prep")

    rc, _, err = _run(str(SPAWN), "--task", "prep",
                      "--cwd", str(ws.root / "workdir"), "--dry-run",
                      "--prompt", "Run refinement-prep now.")

    assert rc == 0, err


def test_skill_event_json_list_requires_every_skill(ws):
    ws.set_skill_hints({})
    ws.add_task("prep", type="calendar", harness="opencode", model="test/model")
    ws.log_event("prep", "skill", '["refinement-prep", "jira"]')

    rc, _, err = _run(str(SPAWN), "--task", "prep",
                      "--cwd", str(ws.root / "workdir"), "--dry-run",
                      "--prompt", "Run /refinement-prep now.")

    assert rc != 0
    assert "omits stored skill(s): jira" in err


def test_skill_event_accepts_a_leading_slash(ws):
    ws.set_skill_hints({"calendar": ["calendar-skill"]})
    ws.add_task("prep", type="calendar", harness="opencode", model="test/model")
    ws.log_event("prep", "skill", "/refinement-prep")

    rc, _, err = _run(str(SPAWN), "--task", "prep",
                      "--cwd", str(ws.root / "workdir"), "--dry-run",
                      "--prompt", "Run refinement-prep now.")

    assert rc == 0, err


def test_blank_skill_event_falls_back_to_the_type_hint(ws):
    ws.set_skill_hints({"jira": ["start-ticket"]})
    ws.add_task("jira-task", type="jira", harness="opencode", model="test/model")
    ws.log_event("jira-task", "skill", "   ")

    rc, _, err = _run(str(SPAWN), "--task", "jira-task",
                      "--cwd", str(ws.root / "workdir"), "--dry-run",
                      "--prompt", "Handle this task.")

    assert rc != 0
    assert "omits configured skill hint(s): start-ticket" in err


def test_malformed_skill_event_json_is_an_error(ws):
    ws.set_skill_hints({})
    ws.add_task("prep", type="calendar", harness="opencode", model="test/model")
    ws.log_event("prep", "skill", "[refinement-prep")

    rc, _, err = _run(str(SPAWN), "--task", "prep",
                      "--cwd", str(ws.root / "workdir"), "--dry-run",
                      "--prompt", "Run refinement-prep now.")

    assert rc != 0
    assert "malformed JSON" in err


def test_resume_does_not_enforce_a_stored_skill(ws):
    ws.set_skill_hints({})
    ws.add_task("prep", type="calendar", harness="opencode", model="test/model")
    ws.log_event("prep", "skill", "refinement-prep")

    rc, _, err = _run(str(SPAWN), "--task", "prep",
                      "--cwd", str(ws.root / "workdir"), "--dry-run", "--resume",
                      "--session-id", "ses_existing", "--prompt", "Continue.")

    assert rc == 0, err


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


# ── spawn-time routing from a saved task profile ──────────────────────────────
def _routing_env(tmp_path, models):
    from _common import now_iso
    table = tmp_path / "model-table.json"
    table.write_text(json.dumps({"version": 1, "fetched_at": now_iso(),
                                 "models": models, "overlay": {},
                                 "preferences": {}}))
    return {**ENV, "PWC_MODEL_TABLE": str(table),
            "PWC_CONFIG_DIR": str(tmp_path / "cfg"),
            "PWC_CODEXBAR": str(tmp_path / "no-codexbar")}


def _row(key, harness, model, tier=4):
    return {"key": key, "harness": harness, "model": model, "catalog_id": None,
            "available": True, "data_ok": True, "context": 200_000,
            "cost_in": 1.0, "cost_out": 1.0, "cache_read": 1.0, "cache_write": 1.0,
            "tiers": {"implementation": tier, "code-review": tier}}


def _run_env(env, *args):
    r = subprocess.run([PYTHON, *args], capture_output=True, text=True,
                       timeout=60, env=env)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def test_spawn_routes_saved_profile_at_spawn_time(ws, tmp_path):
    env = _routing_env(tmp_path, [_row("grok/grok-4.7", "grok", "grok-4.7")])
    ws.add_task("profiled")
    rc, out, err = _run_env(env, str(SCRIPTS / "route.py"), "--task", "profiled",
                            "--domain", "implementation", "--reasoning", "3",
                            "--workspace", str(ws.root), "--save")
    assert rc == 0, err
    assert '"saved_profile"' in out
    rc, out, err = _run_env(env, str(SPAWN), "--task", "profiled",
                            "--cwd", str(ws.root / "workdir"), "--dry-run")
    assert rc == 0, err
    result = json.loads(out)
    assert result["harness"] == "grok"
    assert result["model"] == "grok-4.7"
    assert "routed" in result
    assert "--permission-mode auto" in result["command"]
    assert " -s " in result["command"]


def test_pinned_task_ignores_profile(ws, tmp_path):
    env = _routing_env(tmp_path, [_row("grok/grok-4.7", "grok", "grok-4.7")])
    ws.add_task("pinned", harness="claude", model="opus")
    _run_env(env, str(SCRIPTS / "route.py"), "--task", "pinned", "--domain",
             "implementation", "--reasoning", "3", "--workspace", str(ws.root),
             "--save")
    rc, out, err = _run_env(env, str(SPAWN), "--task", "pinned",
                            "--cwd", str(ws.root / "workdir"), "--dry-run")
    assert rc == 0, err
    assert json.loads(out)["harness"] == "claude"


def test_resume_uses_the_harness_that_ran_the_session(ws, tmp_path):
    env = _routing_env(tmp_path, [_row("claude/opus", "claude", "opus")])
    ws.add_task("resumed")
    sid = "11111111-2222-3333-4444-555555555555"
    _run_taskdb(str(ws.root), "set-session", "--task", "resumed",
                "--session-id", sid, "--harness", "agy", "--model", "gemini-x")
    rc, out, err = _run_env(env, str(SPAWN), "--task", "resumed", "--cwd",
                            str(ws.root / "workdir"), "--session-id", sid,
                            "--resume", "--dry-run", "--prompt", "continue")
    assert rc == 0, err
    result = json.loads(out)
    assert result["harness"] == "agy"
    assert f"--conversation {sid}" in result["command"]
    assert "-i continue" in result["command"]


def test_resume_without_known_harness_refuses(ws, tmp_path):
    env = _routing_env(tmp_path, [_row("claude/opus", "claude", "opus")])
    ws.add_task("lost")
    rc, _, err = _run_env(env, str(SPAWN), "--task", "lost", "--cwd",
                          str(ws.root / "workdir"), "--session-id",
                          "99999999-0000-0000-0000-000000000000", "--resume",
                          "--dry-run")
    assert rc != 0
    assert "cannot tell which harness" in err


def test_resume_infers_harness_from_the_force_note(ws):
    """A session row with a null harness still resumes as whatever was forced.

    Remote nabu was routed to cursor-agent, then forced to claude/sonnet, and
    set-session stored neither. The FORCED line is the harness that ran.
    """
    ws.add_task("stuck")
    ws.log_event(
        "stuck", "routed",
        "cursor-agent/claude-sonnet-5-5-high — picked first",
    )
    ws.log_event(
        "stuck", "note",
        "FORCED model agy/gemini-x over routed cursor-agent/claude-sonnet-5-5-high: "
        "remote workers are claude-shaped in this test",
    )
    sid = "11111111-2222-3333-4444-555555555555"
    rc, out, err = _run(
        str(SPAWN), "--task", "stuck", "--cwd", str(ws.root / "workdir"),
        "--session-id", sid, "--resume", "--dry-run",
    )
    assert rc == 0, err
    result = json.loads(out)
    assert result["harness"] == "agy"
    assert result["model"] == "gemini-x"


def test_resume_session_harness_beats_a_later_force_note(ws):
    ws.add_task("pinned-session")
    sid = "11111111-2222-3333-4444-555555555555"
    _run_taskdb(str(ws.root), "set-session", "--task", "pinned-session",
                "--session-id", sid, "--harness", "agy", "--model", "gemini-x")
    ws.log_event(
        "pinned-session", "note",
        "FORCED model grok/grok-4.7 over routed None/None: should not win",
    )
    rc, out, err = _run(
        str(SPAWN), "--task", "pinned-session", "--cwd", str(ws.root / "workdir"),
        "--session-id", sid, "--resume", "--dry-run",
    )
    assert rc == 0, err
    assert json.loads(out)["harness"] == "agy"


def test_model_with_slashes_parses_as_one_model():
    assert spawn_module.runner_from_detail(
        "opencode/openrouter/z-ai/glm-5.2 — cheap and enough"
    ) == ("opencode", "openrouter/z-ai/glm-5.2")


# ── cursor-agent harness ───────────────────────────────────────────────────────
def test_cursor_fresh_mints_chat_and_puts_id_in_argv(monkeypatch):
    monkeypatch.setattr(spawn_module, "_preallocate_cursor_chat", lambda cwd: "chat-1")
    mode, args, sid = spawn_module._build_cursor(
        session_id="ignored", resume=False, cwd="/tmp", seed_prompt="do the task",
        model="composer-2.5")
    assert (mode, sid) == ("fresh", "chat-1")
    assert args[1:] == ["--resume", "chat-1", "--auto-review",
                        "--model", "composer-2.5", "do the task"]


def test_cursor_dry_run_does_not_create_a_chat(monkeypatch):
    def boom(cwd):
        raise AssertionError("dry run must not call create-chat")
    monkeypatch.setattr(spawn_module, "_preallocate_cursor_chat", boom)
    mode, args, sid = spawn_module._build_cursor(
        session_id=None, resume=False, cwd="/tmp", seed_prompt=None, model=None,
        dry_run=True)
    assert mode == "fresh" and "DRYRUN" in sid and "--model" not in args


def test_cursor_resume_attaches_existing_chat(monkeypatch):
    monkeypatch.setattr(spawn_module, "_cursor_session_exists", lambda _: True)
    mode, args, sid = spawn_module._build_cursor(
        session_id="chat-2", resume=True, cwd="/tmp", seed_prompt=None, model=None)
    assert (mode, sid) == ("resume", "chat-2")
    assert args[1:] == ["--resume", "chat-2", "--auto-review"]


def test_cursor_resume_missing_chat_fails(monkeypatch, capsys):
    monkeypatch.setattr(spawn_module, "_cursor_session_exists", lambda _: False)
    with pytest.raises(SystemExit):
        spawn_module._build_cursor(session_id="gone", resume=True, cwd="/tmp",
                                   seed_prompt=None, model=None)
    assert "no cursor chat gone" in capsys.readouterr().err


def test_cursor_session_exists_reads_the_chat_store(monkeypatch, tmp_path):
    (tmp_path / "chats" / "md5ofcwd" / "chat-3").mkdir(parents=True)
    monkeypatch.setattr(spawn_module, "_CURSOR_HOME", tmp_path)
    assert spawn_module._cursor_session_exists("chat-3")
    assert not spawn_module._cursor_session_exists("chat-4")


def test_cursor_trust_covers_subfolders(monkeypatch, tmp_path):
    trusted = tmp_path / "repo"
    (trusted / "sub").mkdir(parents=True)
    marker = tmp_path / "home" / "projects" / "repo-slug" / ".workspace-trusted"
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({"workspacePath": str(trusted)}))
    monkeypatch.setattr(spawn_module, "_CURSOR_HOME", tmp_path / "home")
    assert spawn_module.cursor_trusts(str(trusted / "sub"))
    assert not spawn_module.cursor_trusts(str(tmp_path))


def test_cursor_dry_run_spawn_via_pin(ws):
    ws.add_task("cursor-route", harness="cursor-agent", model="composer-2.5")
    rc, out, err = _run(str(SPAWN), "--task", "cursor-route",
                        "--cwd", str(ws.root / "workdir"), "--dry-run")
    assert rc == 0, err
    result = json.loads(out)
    assert result["harness"] == "cursor-agent" and result["session_tracked"]
    assert "--resume 00000000-DRYRUN-not-created --auto-review" in result["command"]
