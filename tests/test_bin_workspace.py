"""The `pwc` wrapper must accept `--workspace` from ANY position.

The underlying scripts declare --workspace as a LEADING global (argparse rejects it
after the subcommand). But callers — and the find-work skill, which documents
`pwc sources priority --workspace <root>` — write it AFTER the subcommand. The
wrapper normalizes both forms; without this, the first multi-workspace find-work run
from ~/work died with "unrecognized arguments: --workspace …".
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path

_BIN = Path(__file__).resolve().parent.parent / "bin" / "pwc"


def _load():
    loader = importlib.machinery.SourceFileLoader("pwcbin", str(_BIN))
    spec = importlib.util.spec_from_loader("pwcbin", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


pwcbin = _load()


def test_workspace_trailing_is_lifted_out():
    ws, rest = pwcbin._extract_workspace(["sources", "enabled", "--workspace", "/a/b"])
    assert ws == "/a/b"
    assert rest == ["sources", "enabled"]


def test_workspace_leading_still_works():
    ws, rest = pwcbin._extract_workspace(["--workspace", "/a/b", "find-refs"])
    assert ws == "/a/b"
    assert rest == ["find-refs"]


def test_workspace_equals_form():
    ws, rest = pwcbin._extract_workspace(["sources", "priority", "--workspace=/a/b"])
    assert ws == "/a/b"
    assert rest == ["sources", "priority"]


def test_no_workspace_is_untouched():
    ws, rest = pwcbin._extract_workspace(["summary"])
    assert ws is None
    assert rest == ["summary"]


def test_bare_trailing_flag_is_left_for_argparse():
    """A --workspace with no value stays in argv so argparse reports it normally."""
    ws, rest = pwcbin._extract_workspace(["summary", "--workspace"])
    assert ws is None
    assert rest == ["summary", "--workspace"]


def test_first_occurrence_wins():
    ws, rest = pwcbin._extract_workspace(
        ["add-task", "--workspace", "/a", "--task", "t", "--workspace", "/b"])
    assert ws == "/a"
    # the second, stray one is left in place (not silently swallowed)
    assert rest == ["add-task", "--task", "t", "--workspace", "/b"]
