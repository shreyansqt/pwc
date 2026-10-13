from unittest import mock

import coord


def _row(model, rw, ops, cost_out):
    return {"harness": "claude", "model": model, "available": True,
            "cost_out": cost_out,
            "tiers": {"research-writing": rw, "ops-comms": ops}}


def test_tie_on_weakest_tier_breaks_on_total_then_price():
    rows = [_row("sonnet", 4, 4, 10.0), _row("opus", 5, 4, 20.0),
            _row("fable", 5, 4, 50.0)]
    with mock.patch.object(coord.models, "table", return_value={"models": rows}):
        model, _ = coord.pick_model("claude")
    assert model == "opus"


def test_weakest_tier_still_decides_first():
    rows = [_row("opus", 5, 3, 20.0), _row("sonnet", 4, 4, 10.0)]
    with mock.patch.object(coord.models, "table", return_value={"models": rows}):
        model, _ = coord.pick_model("claude")
    assert model == "sonnet"
