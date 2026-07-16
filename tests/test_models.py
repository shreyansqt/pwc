import pytest
import datetime as _dt
from unittest import mock

import models
from _common import now_iso

SEED_DATA = {
    "version": 1,
    "fetched_at": None,
    "models": [
        {"key": "codex/gpt-5.5", "harness": "codex", "model": "gpt-5.5",
         "catalog_id": "openai/gpt-5.5", "available": True, "context": None,
         "cost_in": None, "cost_out": None, "cache_read": None, "cache_write": None,
         "data_ok": True, "tiers": {}},
        {"key": "codex/gpt-5.3-codex", "harness": "codex", "model": "gpt-5.3-codex",
         "catalog_id": "openai/gpt-5.3-codex", "available": True, "context": None,
         "cost_in": None, "cost_out": None, "cache_read": None, "cache_write": None,
         "data_ok": True, "tiers": {}},
        {"key": "claude/opus-5", "harness": "claude", "model": "claude-opus-5",
         "catalog_id": "anthropic/claude-opus-5", "available": True,
         "context": None, "cost_in": None, "cost_out": None,
         "cache_read": None, "cache_write": None, "data_ok": True, "tiers": {}},
    ],
    "overlay": {},
}

EMPTY_CATALOG = {}

CODEX_SLUGS = {"gpt-5.5", "gpt-5.4"}


def _make_patch(target, value):
    return mock.patch(target, return_value=value)


# codex catalog cross-reference marks non-matching models unavailable
def test_codex_catalog_filters_unavailable():
    with mock.patch.object(models, "_openrouter_catalog", return_value=EMPTY_CATALOG), \
         mock.patch.object(models, "_codex_model_slugs", return_value=CODEX_SLUGS), \
         mock.patch("shutil.which", return_value="/usr/bin/codex"):
        new_table, changes = models.compute_fetch(SEED_DATA)

    gpt55 = [m for m in new_table["models"] if m["key"] == "codex/gpt-5.5"][0]
    codex3 = [m for m in new_table["models"] if m["key"] == "codex/gpt-5.3-codex"][0]
    claude = [m for m in new_table["models"] if m["key"] == "claude/opus-5"][0]

    # gpt-5.5 is in the codex catalog -> available
    assert gpt55["available"] is True
    # gpt-5.3-codex is NOT in the codex catalog -> unavailable
    assert codex3["available"] is False
    # claude model is unaffected (uses harness_available)
    assert claude["available"] is True

    avail_changes = [c for c in changes if c["field"] == "available"]
    codex3_change = [c for c in avail_changes if c["key"] == "codex/gpt-5.3-codex"]
    assert len(codex3_change) == 1
    assert codex3_change[0]["old"] is True
    assert codex3_change[0]["new"] is False


# when codex catalog query fails, fall back to binary check
def test_codex_catalog_failure_falls_back_to_binary():
    with mock.patch.object(models, "_openrouter_catalog", return_value=EMPTY_CATALOG), \
         mock.patch.object(models, "_codex_model_slugs", return_value=None), \
         mock.patch("shutil.which", return_value="/usr/bin/codex"):
        new_table, _changes = models.compute_fetch(SEED_DATA)

    gpt55 = [m for m in new_table["models"] if m["key"] == "codex/gpt-5.5"][0]
    codex3 = [m for m in new_table["models"] if m["key"] == "codex/gpt-5.3-codex"][0]

    # both available — fallback to binary check
    assert gpt55["available"] is True
    assert codex3["available"] is True


# when codex binary is not installed, even catalog-matched models are unavailable
def test_codex_not_installed_marks_all_unavailable():
    with mock.patch.object(models, "_openrouter_catalog", return_value=EMPTY_CATALOG), \
         mock.patch.object(models, "_codex_model_slugs", return_value=CODEX_SLUGS), \
         mock.patch("shutil.which", return_value=None):
        new_table, _changes = models.compute_fetch(SEED_DATA)

    gpt55 = [m for m in new_table["models"] if m["key"] == "codex/gpt-5.5"][0]
    codex3 = [m for m in new_table["models"] if m["key"] == "codex/gpt-5.3-codex"][0]

    assert gpt55["available"] is False
    assert codex3["available"] is False


# ── table_freshness ─────────────────────────────────────────────────────────
def _data_with_fetched_at(ts: str | None) -> dict:
    return {"version": 1, "fetched_at": ts, "models": [], "overlay": {}}


def test_freshness_recent_table():
    data = _data_with_fetched_at(now_iso())
    result = models.table_freshness(data)
    assert result["stale"] is False
    assert result["age_hours"] is not None
    assert result["age_hours"] < 1.0


def test_freshness_stale_table():
    stale_ts = (_dt.datetime.now(_dt.timezone.utc)
                - _dt.timedelta(hours=models._STALE_HOURS + 1)
                ).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = _data_with_fetched_at(stale_ts)
    result = models.table_freshness(data)
    assert result["stale"] is True
    assert result["age_hours"] > models._STALE_HOURS


def test_freshness_never_fetched():
    data = _data_with_fetched_at(None)
    result = models.table_freshness(data)
    assert result["stale"] is True
    assert result["age_hours"] is None
    assert "never fetched" in result["why"]


def test_freshness_custom_threshold():
    data = _data_with_fetched_at(now_iso())
    result = models.table_freshness(data, threshold_hours=0.0)
    assert result["stale"] is True
    assert result["threshold_hours"] == 0.0


def test_freshness_just_under_threshold():
    ts = (_dt.datetime.now(_dt.timezone.utc)
          - _dt.timedelta(hours=models._STALE_HOURS - 0.5)
          ).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = _data_with_fetched_at(ts)
    result = models.table_freshness(data)
    assert result["stale"] is False
