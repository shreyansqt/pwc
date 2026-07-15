"""Sources config fan-out from a PARENT of several workspaces (~/work).

find-work reads `pwc sources enabled` to learn what to scan. Standing in ~/work —
the coordination vantage point, holding smarta/ and side-projects/ — that read must
fan out across every workspace below and return each one's config, keyed by workspace
name. Writing (`set`) must instead PIN to one workspace or refuse, because a config
belongs to exactly one board (mirrors taskdb.py's reads-fan-out / writes-pin rule).
"""

from __future__ import annotations

import io
import json
import contextlib

import pytest

import sources


def _mk_workspace(root, name, cfg):
    """A real workspace (has a task store) carrying a sources.json."""
    pwc = root / name / ".pwc"
    pwc.mkdir(parents=True)
    (pwc / "taskdb.db").write_text("")  # what makes it a REAL workspace
    (pwc / "sources.json").write_text(json.dumps(cfg))
    return root / name


def _run(argv, cwd, monkeypatch):
    """Run sources.main(argv) as if invoked from `cwd`, capturing its JSON stdout."""
    monkeypatch.chdir(cwd)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        sources.main(argv)
    return json.loads(buf.getvalue())


def test_enabled_fans_out_from_a_parent(tmp_path, monkeypatch):
    _mk_workspace(tmp_path, "smarta", {
        "sources": {"jira": {"enabled": True, "project": "SMT", "jql": "x"}}})
    _mk_workspace(tmp_path, "side-projects", {
        "sources": {"github": {"enabled": True, "org": "me"},
                    "email": {"enabled": False}}})

    out = _run(["enabled"], tmp_path, monkeypatch)

    assert set(out["workspaces"]) == {"smarta", "side-projects"}
    assert "jira" in out["workspaces"]["smarta"]["sources"]
    # enabled must filter per-workspace: the disabled email source is gone,
    # the enabled github one stays.
    sp = out["workspaces"]["side-projects"]["sources"]
    assert "github" in sp and "email" not in sp


def test_single_workspace_shape_is_unwrapped(tmp_path, monkeypatch):
    """Standing INSIDE a workspace returns the bare payload — no 'workspaces' wrapper,
    so nothing downstream of the single-workspace path has to change."""
    ws = _mk_workspace(tmp_path, "solo", {
        "sources": {"jira": {"enabled": True, "project": "SMT", "jql": "x"}}})

    out = _run(["enabled"], ws, monkeypatch)

    assert "workspaces" not in out
    assert "jira" in out["sources"]


def test_set_refuses_from_a_parent(tmp_path, monkeypatch):
    """A write from the parent has no workspace to infer — it must refuse, not guess."""
    _mk_workspace(tmp_path, "a", {"sources": {}})
    _mk_workspace(tmp_path, "b", {"sources": {}})

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO('{"sources": {}}'))
    with pytest.raises(SystemExit):
        sources.main(["set", "--json", "-"])


def test_one_bad_workspace_does_not_kill_the_sweep(tmp_path, monkeypatch):
    """A workspace with a malformed sources.json drops out; the others still return."""
    _mk_workspace(tmp_path, "good", {
        "sources": {"jira": {"enabled": True, "project": "SMT", "jql": "x"}}})
    bad = _mk_workspace(tmp_path, "bad", {"sources": {}})
    (bad / ".pwc" / "sources.json").write_text("{ not json")

    out = _run(["enabled"], tmp_path, monkeypatch)

    assert "good" in out["workspaces"]
    assert "bad" not in out["workspaces"]
