from datetime import datetime, timezone

import quota
from route import route

NOW = datetime(2026, 10, 13, 16, 0, tzinfo=timezone.utc)


def _entry(*windows, error=None):
    if error:
        return {"error": {"message": error}}
    keys = ("primary", "secondary", "tertiary")
    return {"usage": {k: w for k, w in zip(keys, windows)}}


def _w(used, minutes, resets_at):
    return {"usedPercent": used, "windowMinutes": minutes, "resetsAt": resets_at}


# ── status_from_codexbar ──────────────────────────────────────────────────────
def test_weekly_slack_is_expected_minus_used():
    # 7-day window that resets in 3.5 days: half elapsed, so 50% expected.
    st = quota.status_from_codexbar(
        _entry(_w(20, 10080, "2026-10-17T04:00:00Z")), now=NOW)
    assert st["status"] == "ok"
    assert st["slack"] == 30.0


def test_slack_uses_the_longest_window():
    st = quota.status_from_codexbar(_entry(
        _w(10, 300, "2026-10-13T20:00:00Z"),        # 5h window, 20% elapsed
        _w(20, 10080, "2026-10-17T04:00:00Z")), now=NOW)
    assert st["slack"] == 30.0


def test_window_near_full_is_exhausted():
    st = quota.status_from_codexbar(
        _entry(_w(92, 10080, "2026-10-17T04:00:00Z")), now=NOW)
    assert st["status"] == "exhausted"
    assert "92% used" in st["why"]


def test_window_on_pace_to_run_out_is_exhausted():
    # 5h window 80% elapsed and 89% used -> on pace for ~111%.
    st = quota.status_from_codexbar(
        _entry(_w(89, 300, "2026-10-13T17:00:00Z")), now=NOW)
    assert st["status"] == "exhausted"
    assert "on pace" in st["why"]


def test_no_projection_early_in_a_window():
    # 10% elapsed and 20% used would project to 200%, but that is too early to trust.
    st = quota.status_from_codexbar(
        _entry(_w(20, 300, "2026-10-13T20:30:00Z")), now=NOW)
    assert st["status"] == "ok"


def test_missing_window_length_defaults_to_weekly():
    st = quota.status_from_codexbar(
        _entry({"usedPercent": 0, "resetsAt": "2026-10-17T04:00:00Z"}), now=NOW)
    assert st["windows"][0]["window_minutes"] == 10080
    assert st["slack"] == 50.0


def test_error_is_unknown():
    st = quota.status_from_codexbar(_entry(error="not logged in"), now=NOW)
    assert st["status"] == "unknown"
    assert "not logged in" in st["why"]


def test_active_mark_overrides_status():
    status = {"x": {"status": "ok", "slack": 10}}
    marks = {"x": {"until": "2026-10-14T00:00:00Z", "why": "quota error"}}
    out = quota.apply_marks(status, marks, now=NOW)
    assert out["x"]["status"] == "exhausted"


def test_expired_mark_is_ignored():
    status = {"x": {"status": "ok", "slack": 10}}
    marks = {"x": {"until": "2026-10-13T00:00:00Z", "why": "old"}}
    assert quota.apply_marks(status, marks, now=NOW)["x"]["status"] == "ok"


def test_null_override_removes_a_plan():
    subs = quota.subscriptions({"subscriptions": {"google-ai": None}})
    assert "google-ai" not in subs
    assert "claude" in subs


def test_table_overrides_subscription_map():
    subs = quota.subscriptions({"subscriptions": {"claude": {"plan": "Claude Pro"}}})
    assert subs["claude"]["plan"] == "Claude Pro"
    assert subs["claude"]["harnesses"] == ["claude"]


# ── route with quota ──────────────────────────────────────────────────────────
def _row(key, harness, *, tier=4, cost=1.0, vendor=None):
    row = {"key": key, "harness": harness, "model": key.split("/")[-1],
           "catalog_id": None, "available": True, "data_ok": True,
           "context": 200_000, "cost_in": cost, "cost_out": cost,
           "cache_read": cost, "cache_write": cost, "cost_weight": 1.0,
           "tiers": {"implementation": tier, "code-review": tier}}
    if vendor:
        row["vendor"] = vendor
    return row


TABLE = {"models": [
    _row("claude/opus", "claude", cost=5.0),
    _row("codex/sol", "codex", cost=3.0),
    _row("grok/grok-4.7", "grok", cost=1.0),
    _row("agy/opus-4.6", "agy", cost=2.0, vendor="anthropic"),
], "overlay": {}, "preferences": {}}


def _profile(task="t-1", domain="implementation", **extra):
    return {"task_id": task, "domain": domain, "reasoning": 3, **extra}


def _q(**statuses):
    return {k: v for k, v in statuses.items()}


def test_plan_with_most_slack_wins():
    q = _q(claude={"status": "ok", "slack": 5}, chatgpt={"status": "ok", "slack": 30},
           x={"status": "ok", "slack": 10}, **{"google-ai": {"status": "ok", "slack": 0}})
    r = route(_profile(), TABLE, quota=q)
    assert r["harness"] == "codex"
    assert r["quota"]["subscription"] == "chatgpt"


def test_exhausted_plan_is_rejected_even_if_cheapest():
    q = _q(claude={"status": "ok", "slack": 5}, chatgpt={"status": "ok", "slack": 1},
           x={"status": "exhausted", "slack": 50, "why": "weekly 95% used"},
           **{"google-ai": {"status": "ok", "slack": 0}})
    r = route(_profile(), TABLE, quota=q)
    assert r["harness"] != "grok"
    assert any("out of quota" in x["why"] for x in r["rejected"])


def test_all_exhausted_fails_without_fallback():
    q = {s: {"status": "exhausted", "why": "full"}
         for s in ("claude", "chatgpt", "x", "google-ai")}
    try:
        route(_profile(), TABLE, quota=q)
    except SystemExit:
        return
    raise AssertionError("route should refuse when every plan is out of quota")


def test_fill_order_fills_cheap_plan_to_pace_first():
    table = {**TABLE, "quota": {"fill_order": ["x", "google-ai", "chatgpt", "claude"]}}
    q = _q(claude={"status": "ok", "slack": 40}, chatgpt={"status": "ok", "slack": 30},
           x={"status": "ok", "slack": 2}, **{"google-ai": {"status": "ok", "slack": 0}})
    assert route(_profile(), table, quota=q)["harness"] == "grok"
    q["x"]["slack"] = -5  # x ran ahead of pace -> next in the order
    assert route(_profile(), table, quota=q)["harness"] == "agy"


def test_unknown_plan_gets_only_its_share():
    q = _q(claude={"status": "ok", "slack": 5}, chatgpt={"status": "ok", "slack": 30},
           x={"status": "ok", "slack": 10}, **{"google-ai": {"status": "unknown"}})
    picks = [route(_profile(task=f"t-{i}"), TABLE, quota=q)["harness"]
             for i in range(400)]
    share = picks.count("agy") / len(picks)
    assert 0.05 < share < 0.16
    # deterministic: the same task always routes the same way
    assert route(_profile(task="t-7"), TABLE, quota=q)["harness"] == picks[7]


def test_only_unknown_plans_left_still_routes():
    q = {"claude": {"status": "exhausted"}, "chatgpt": {"status": "exhausted"},
         "x": {"status": "exhausted"}, "google-ai": {"status": "unknown"}}
    assert route(_profile(), TABLE, quota=q)["harness"] == "agy"


def test_capability_floor_beats_quota():
    weak = {**TABLE, "models": [_row("grok/grok-4.7", "grok", tier=2, cost=0.1),
                                _row("claude/opus", "claude", tier=5, cost=9.0)]}
    q = _q(claude={"status": "ok", "slack": 0}, x={"status": "ok", "slack": 90})
    r = route({**_profile(), "reasoning": 4}, weak, quota=q)
    assert r["harness"] == "claude"


def test_reviewer_vendor_differs_from_author():
    q = _q(claude={"status": "ok", "slack": 90}, chatgpt={"status": "ok", "slack": 1},
           x={"status": "ok", "slack": 1}, **{"google-ai": {"status": "ok", "slack": 1}})
    r = route(_profile(domain="code-review", author_vendor="anthropic"), TABLE, quota=q)
    assert r["harness"] in ("codex", "grok")   # not claude, not agy's Claude model
    why = {x["key"]: x["why"] for x in r["rejected"]}
    assert "same vendor" in why["claude/opus"]
    assert "same vendor" in why["agy/opus-4.6"]


def test_author_vendor_ignored_outside_review():
    q = _q(claude={"status": "ok", "slack": 90})
    r = route(_profile(author_vendor="anthropic"),
              {**TABLE, "models": [_row("claude/opus", "claude")]}, quota=q)
    assert r["harness"] == "claude"


def test_allowed_harnesses_fence():
    q = _q(claude={"status": "ok", "slack": 1}, x={"status": "ok", "slack": 90})
    r = route(_profile(), TABLE, quota=q, allowed_harnesses=["claude", "codex"])
    assert r["harness"] in ("claude", "codex")
    assert any("not allowed" in x["why"] for x in r["rejected"])


def test_preference_ignored_when_quota_routing():
    table = {**TABLE, "preferences": {"implementation": {
        "key": "claude/opus", "strength": 100}}}
    q = _q(claude={"status": "ok", "slack": 0}, chatgpt={"status": "ok", "slack": 50},
           x={"status": "ok", "slack": 0}, **{"google-ai": {"status": "ok", "slack": 0}})
    assert route(_profile(), table, quota=q)["harness"] == "codex"


def test_cursor_shares_the_x_subscription_with_grok():
    subs = quota.subscriptions()
    assert quota.subscription_for_harness("cursor-agent", subs) == "x"
    assert quota.subscription_for_harness("grok", subs) == "x"
