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
