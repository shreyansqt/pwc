import pytest
from route import route, required_tier

DOMAIN = "implementation"


def _model(key, *, available=True, data_ok=True, context=200_000, tiers=None,
           cost_in=1.0, cost_out=5.0, cache_read=1.0, cache_write=1.0):
    return {
        "key": key,
        "harness": "test",
        "model": f"test/{key}",
        "catalog_id": f"test/{key}",
        "available": available,
        "data_ok": data_ok,
        "context": context,
        "cost_in": cost_in,
        "cost_out": cost_out,
        "cache_read": cache_read,
        "cache_write": cache_write,
        "tiers": tiers or {DOMAIN: 3},
    }


def _table(*models):
    return {"version": 1, "fetched_at": None, "models": list(models), "overlay": {}}


def _profile(domain=DOMAIN, reasoning=3, verifiability=3, risk="none", context_need=0):
    return {"domain": domain, "reasoning": reasoning, "verifiability": verifiability,
            "risk": risk, "context_need": context_need}


# ── 1. cheapest model clearing the required tier wins ──────────────────────────
def test_cheapest_wins():
    cheap = _model("cheap", cost_in=1.0, cost_out=5.0, cache_read=1.0, cache_write=1.0)
    pricey = _model("pricey", cost_in=2.0, cost_out=10.0, cache_read=2.0, cache_write=2.0)
    result = route(_profile(), _table(cheap, pricey))
    assert result["key"] == "cheap"
    assert result["blended_cost_per_mtok"] < _blended(pricey)


def _blended(row):
    from route import _blended_cost
    return _blended_cost(row)


# ── 2. an unavailable model is never picked ────────────────────────────────────
def test_unavailable_excluded():
    unavailable = _model("ghost", available=False, cost_in=0.1, cost_out=0.5,
                         cache_read=0.1, cache_write=0.1)
    available = _model("real", cost_in=10.0, cost_out=50.0, cache_read=10.0, cache_write=10.0)
    result = route(_profile(), _table(unavailable, available))
    assert result["key"] == "real"
    rejected_keys = {r["key"] for r in result["rejected"]}
    assert "ghost" in rejected_keys


# ── 3. context_need filters out too-small windows ──────────────────────────────
def test_context_need_filters():
    small = _model("small", context=50_000)
    large = _model("large", context=200_000)
    result = route(_profile(context_need=100_000), _table(small, large))
    assert result["key"] == "large"
    rejected = {r["key"]: r["why"] for r in result["rejected"]}
    assert "small" in rejected
    assert "context" in rejected["small"]


# ── 4. low verifiability raises the required tier by one ───────────────────────
def test_low_verifiability_raises_tier():
    lo_tier = _model("lo", tiers={DOMAIN: 3})
    hi_tier = _model("hi", tiers={DOMAIN: 4})
    result = route(_profile(reasoning=3, verifiability=1), _table(lo_tier, hi_tier))
    assert result["key"] == "hi"
    assert result["required_tier"] == 4
    rejected = {r["key"]: r["why"] for r in result["rejected"]}
    assert "lo" in rejected
    assert "tier 3 < required 4" in rejected["lo"]


def test_required_tier_does_not_exceed_5():
    need, _ = required_tier(5, 1)
    assert need == 5


# ── 5. risk=prod-data only picks data_ok=true models ───────────────────────────
def test_prod_data_requires_data_ok():
    blocked = _model("blocked", data_ok=False, tiers={DOMAIN: 4})
    allowed = _model("allowed", data_ok=True, tiers={DOMAIN: 4})
    result = route(_profile(risk="prod-data", reasoning=3), _table(blocked, allowed))
    assert result["key"] == "allowed"
    rejected = {r["key"]: r["why"] for r in result["rejected"]}
    assert "blocked" in rejected
    assert "not cleared for production data" in rejected["blocked"]


def test_prod_data_enforces_min_tier():
    lo = _model("lo", data_ok=True, tiers={DOMAIN: 3})
    hi = _model("hi", data_ok=True, tiers={DOMAIN: 4})
    result = route(_profile(risk="prod-data", reasoning=3, verifiability=3),
                   _table(lo, hi))
    assert result["key"] == "hi"
    assert result["required_tier"] == 4


# ── 6. nothing qualifying raises SystemExit ────────────────────────────────────
def test_no_qualifying_model_exits():
    bad = _model("bad", available=False)
    with pytest.raises(SystemExit):
        route(_profile(), _table(bad))