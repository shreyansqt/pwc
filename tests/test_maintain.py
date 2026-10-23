from datetime import date

import maintain


def test_parse_grok_models():
    lines = ["You are logged in with grok.com.", "", "Default model: grok-4.7", "",
             "Available models:", "  * grok-4.7 (default)", "  - grok-4.6"]
    assert maintain.parse_grok_models(lines) == {"grok-4.7", "grok-4.6"}


def test_parse_agy_models():
    lines = ["Fetching available models...",
             "gemini-3.8-flash-high\tGemini 3.8 Flash (High)",
             "claude-sonnet-4-6\tClaude Sonnet 4.6 (Thinking)"]
    assert maintain.parse_agy_models(lines) == {"gemini-3.8-flash-high",
                                                "claude-sonnet-4-6"}


def _table():
    return {"models": [
        {"key": "codex/old", "harness": "codex", "model": "old", "available": True},
        {"key": "codex/keep", "harness": "codex", "model": "keep", "available": True},
        {"key": "claude/opus", "harness": "claude", "model": "opus", "available": True},
    ]}


def test_new_model_needs_tiers_and_gone_model_retires():
    data = _table()
    changes = maintain.reconcile(data, {"codex": {"keep", "new"}})
    rows = {r["key"]: r for r in data["models"]}
    assert rows["codex/new"]["needs_tiers"] is True
    assert rows["codex/new"]["tiers"] == {}
    assert rows["codex/new"]["data_ok"] is False
    assert rows["codex/old"]["retired"] is True
    assert rows["codex/old"]["available"] is False
    assert rows["codex/keep"]["available"] is True
    assert rows["claude/opus"]["available"] is True   # harness not listed: untouched
    assert {c["key"] for c in changes} == {"codex/new", "codex/old"}


def test_relisted_model_is_unretired():
    data = _table()
    maintain.reconcile(data, {"codex": {"keep"}})
    maintain.reconcile(data, {"codex": {"keep", "old"}})
    old = next(r for r in data["models"] if r["key"] == "codex/old")
    assert "retired" not in old and old["available"] is True


def test_price_refresh_keeps_retired_rows_off(monkeypatch):
    import models
    data = _table()
    maintain.reconcile(data, {"codex": {"keep"}})
    monkeypatch.setattr(models, "_openrouter_catalog", lambda: {})
    monkeypatch.setattr(models, "_codex_model_slugs", lambda: None)
    monkeypatch.setattr(models, "harness_available", lambda h: True)
    new, _ = models.compute_fetch(data)
    old = next(r for r in new["models"] if r["key"] == "codex/old")
    assert old["available"] is False


def test_due_once_per_day(monkeypatch, tmp_path):
    monkeypatch.setenv("PWC_CONFIG_DIR", str(tmp_path))
    assert maintain.is_due(date(2026, 10, 13))
    maintain._save_marker({"date": "2026-10-13"})
    assert not maintain.is_due(date(2026, 10, 13))
    assert maintain.is_due(date(2026, 10, 14))


def test_parse_cursor_models_skips_auto_and_heading():
    lines = ["Available models", "", "auto - Auto (default)",
             "composer-2.5 - Composer 2.5",
             "grok-4.7-xhigh - Grok 4.7  Extra High"]
    assert maintain.parse_cursor_models(lines) == {"composer-2.5", "grok-4.7-xhigh"}


def test_maintain_keeps_unretired_cursor_model_off_the_plan(monkeypatch, tmp_path):
    """Coming back onto `cursor-agent models` is not enough. The plan check
    runs after un-retire, and the approved tiers stay put."""
    import models
    data = {"models": [{
        "key": "cursor-agent/claude-sonnet-5-5-high", "harness": "cursor-agent",
        "model": "claude-sonnet-5-5-high", "available": False, "retired": True,
    }], "overlay": {"cursor-agent/claude-sonnet-5-5-high":
                    {"tiers": {"implementation": 4}}}}
    monkeypatch.setenv("PWC_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(models, "load_raw", lambda must_exist=False: data)
    monkeypatch.setattr(models, "compute_fetch", lambda d: (d, []))
    monkeypatch.setattr(maintain, "update_harnesses", lambda *a, **k: [])
    monkeypatch.setattr(maintain, "list_models",
                        lambda h: {"claude-sonnet-5-5-high"}
                        if h == "cursor-agent" else None)
    monkeypatch.setattr(models, "harness_available", lambda h: True)
    monkeypatch.setattr(models, "cursor_plan_inclusion",
                        lambda: {"models": {"grok-4.7-high"}, "prepaid_cents": 0})
    saved = {}
    monkeypatch.setattr(models, "save", lambda d: saved.update(table=d))
    maintain.run()
    row = saved["table"]["models"][0]
    assert "retired" not in row
    assert row["available"] is False
    assert saved["table"]["overlay"]["cursor-agent/claude-sonnet-5-5-high"][
        "tiers"] == {"implementation": 4}


def test_cursor_discovery_adds_no_rows_but_retires_gone_ones():
    data = {"models": [{"key": "cursor-agent/old", "harness": "cursor-agent",
                        "model": "old", "available": True}]}
    changes = maintain.reconcile(data, {"cursor-agent": {"new-a", "new-b"}})
    assert [r["key"] for r in data["models"]] == ["cursor-agent/old"]
    assert data["models"][0]["retired"] is True
    assert [c["change"] for c in changes] == ["gone from the cursor-agent model list"]
