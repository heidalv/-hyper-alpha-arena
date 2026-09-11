# -*- coding: utf-8 -*-
"""smart_money 采集器核心逻辑单测（不碰网络/DB）。"""
from backend.services.events.smart_money import detect_moves, _aggregate_positions


def _pos(symbol, direction, notional, leverage=5.0, size=1.0):
    return {"symbol": symbol, "direction": direction, "notional_usd": notional,
            "leverage": leverage, "size": size, "entry_price": 100.0,
            "unrealized_pnl": 0.0, "extra": {}}


class TestAggregatePositions:
    def test_same_symbol_sub_positions_merged(self):
        # 实测 OKX 带单员同币种两笔空单子仓 → 应聚合为一行
        positions = [
            _pos("BTC", "short", 161992.0, leverage=100.0),
            _pos("BTC", "short", 239384.0, leverage=100.0),
        ]
        out = _aggregate_positions(positions)
        assert len(out) == 1
        assert out[0]["symbol"] == "BTC"
        assert out[0]["direction"] == "short"
        assert abs(out[0]["notional_usd"] - 401376.0) < 1e-6

    def test_hedged_long_short_nets_out(self):
        positions = [
            _pos("ETH", "long", 100000.0),
            _pos("ETH", "short", 30000.0),
        ]
        out = _aggregate_positions(positions)
        assert len(out) == 1
        assert out[0]["direction"] == "long"
        assert abs(out[0]["notional_usd"] - 70000.0) < 1e-6

    def test_empty(self):
        assert _aggregate_positions([]) == []


class TestDetectMoves:
    def test_open(self):
        moves, held = detect_moves({}, [_pos("BTC", "long", 100_000)],
                                   min_notional=50_000, change_ratio=0.3)
        assert len(moves) == 1 and moves[0]["action"] == "open"
        assert moves[0]["direction"] == "long"
        assert held == ["BTC"]

    def test_close(self):
        prev = {"BTC": {"direction": "long", "notional": 100_000, "leverage": 5}}
        moves, held = detect_moves(prev, [], min_notional=50_000, change_ratio=0.3)
        assert len(moves) == 1 and moves[0]["action"] == "close"
        assert held == []

    def test_flip(self):
        prev = {"BTC": {"direction": "long", "notional": 100_000, "leverage": 5}}
        moves, _ = detect_moves(prev, [_pos("BTC", "short", 120_000)],
                                min_notional=50_000, change_ratio=0.3)
        assert len(moves) == 1 and moves[0]["action"] == "flip"
        assert moves[0]["direction"] == "short"

    def test_increase_and_decrease(self):
        prev = {"BTC": {"direction": "long", "notional": 100_000, "leverage": 5}}
        moves, _ = detect_moves(prev, [_pos("BTC", "long", 150_000)],
                                min_notional=50_000, change_ratio=0.3)
        assert moves[0]["action"] == "increase"
        moves, _ = detect_moves(prev, [_pos("BTC", "long", 60_000)],
                                min_notional=50_000, change_ratio=0.3)
        assert moves[0]["action"] == "decrease"

    def test_small_change_no_move(self):
        prev = {"BTC": {"direction": "long", "notional": 100_000, "leverage": 5}}
        moves, _ = detect_moves(prev, [_pos("BTC", "long", 110_000)],
                                min_notional=50_000, change_ratio=0.3)
        assert moves == []

    def test_below_min_notional_ignored(self):
        # 小于追踪门槛的仓位不报 open
        moves, _ = detect_moves({}, [_pos("DOGE", "long", 10_000)],
                                min_notional=50_000, change_ratio=0.3)
        assert moves == []
