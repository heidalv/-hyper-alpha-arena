# -*- coding: utf-8 -*-
"""主动流观察池、交易位、换出后的离场集合。"""
from backend.services.market_maker.flow_universe import (
    exit_only_symbols,
    in_watch_pool,
    membership,
    ranked_watch_pool,
    select_trading_slots,
)


def _row(symbol, volume, spread, trades=10, trades_15m=None):
    row = {"symbol": symbol, "quote_volume_usd": volume, "spread_bp": spread,
           "trades_24h": trades}
    if trades_15m is not None:
        row["trades_15m"] = trades_15m
    return row


def test_watch_pool_ranks_volume_and_rejects_wide_spread():
    rows = [
        _row("BBB", 200, 20),
        _row("AAA", 100, 2),
        _row("USDC", 500, 1),
        _row("CCC", 80, 3, trades=0),
        _row("DDD", 50, 4),
    ]
    assert ranked_watch_pool(rows, stop_bp=15) == ["AAA", "DDD"]
    assert in_watch_pool(_row("BBB", 200, 20), 15) is False
    assert in_watch_pool(_row("EEE", 10, 1, trades_15m=0), 15) is False


def test_slots_stay_empty_without_oos():
    pool = ["AAA", "BBB"]
    gates = {"AAA": {"oos": {"mean_y": 1.2, "n_eff": 10}}}
    chosen, new_in = select_trading_slots(pool, gates, [], {}, 1_000)
    assert chosen == []
    assert new_in == []


def test_slots_take_positive_oos_only():
    pool = ["AAA", "BBB", "CCC"]
    gates = {
        "AAA": {"oos": {"mean_y": 1.2, "n_eff": 40, "fill_rate": 0.2}},
        "BBB": {"oos": {"mean_y": -0.4, "n_eff": 80, "win_rate": 0.9, "fill_rate": 0.5}},
        "CCC": {"oos": {"mean_y": 2.0, "n_eff": 35, "fill_rate": 0.3}},
    }
    chosen, _ = select_trading_slots(pool, gates, [], {}, 1_000)
    assert chosen[0] == "CCC"
    assert "BBB" not in chosen
    assert set(chosen) == {"AAA", "CCC"}


def test_negative_recent_drops_even_inside_dwell():
    gates = {"AAA": {"oos": {"mean_y": 2.0, "n_eff": 40}, "recent_mean_y": -0.2}}
    assert membership("AAA", gates["AAA"], 10_000, {"AAA": 9_000}) == "drop"
    chosen, _ = select_trading_slots(["AAA"], gates, ["AAA"], {"AAA": 9_000}, 10_000)
    assert chosen == []


def test_no_swap_for_a_slightly_better_coin_during_dwell():
    gates = {
        "AAA": {"oos": {"mean_y": 1.0, "n_eff": 40, "fill_rate": 0.2}},
        "BBB": {"oos": {"mean_y": 3.0, "n_eff": 40, "fill_rate": 0.2}},
    }
    chosen, new_in = select_trading_slots(
        ["AAA", "BBB"], gates, ["AAA"], {"AAA": 9_500}, 10_000, slot_cap=1)
    assert chosen == ["AAA"]
    assert new_in == []
    later, new_in = select_trading_slots(
        ["AAA", "BBB"], gates, ["AAA"], {"AAA": 1_000}, 10_000, slot_cap=1)
    assert later == ["BBB"]
    assert new_in == ["BBB"]


def test_failed_exam_drops_even_when_average_is_positive():
    gates = {"AAA": {"allow": False, "oos": {"mean_y": 2.0, "n_eff": 40}}}
    assert membership("AAA", gates["AAA"], 5_000, {"AAA": 1_000}) == "drop"
    chosen, _ = select_trading_slots(["AAA"], gates, ["AAA"], {"AAA": 1_000}, 5_000)
    assert chosen == []


def test_unknown_gate_does_not_eject_current_coin():
    gates = {"AAA": {"allow": False, "reason": "no_tradable_bucket",
                     "oos": {"mean_y": 0.0, "n_eff": 0.0}}}
    chosen, _ = select_trading_slots(["ZZZ"], gates, ["AAA"], {"AAA": 1_000}, 5_000)
    assert "AAA" in chosen
    assert exit_only_symbols(["AAA"], {"AAA": 1.0, "BBB": -2.0, "CCC": 0.0}) == ["BBB"]
