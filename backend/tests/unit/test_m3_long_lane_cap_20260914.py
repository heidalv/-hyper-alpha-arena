# -*- coding: utf-8 -*-
"""[M3 2026-09-14] 长车道独立并发帽契约测试。

背景：E1 趋势 sleeve 启用后，脑 mid 4 仓占满 MIDLONG_MAX_OPEN_POSITIONS=4 全局帽
→ E1 全拒（2026-09-14 实测 [MidLongChokeGate] 拒单 BTC ... midlong_open_positions
4>=4）。E1 有自身风险边界（60% 权益桶 + gross/cluster/risk_per_trade 帽 +
Chandelier 止损），目标最多 8 核心币 → 长车道开仓走独立并发帽
MIDLONG_MAX_LONG_LANE_POSITIONS（默认 8），净敞口/簇帽仍看全量 midlong（不削弱）。

契约：
- long_lane=True：并发帽只数长车道持仓（mid 仓不占帽），帽值读 MIDLONG_MAX_LONG_LANE_POSITIONS。
- long_lane=False：保持旧口径（全量 midlong vs MIDLONG_MAX_OPEN_POSITIONS）。
- 净敞口检查在 long_lane=True 时仍按全量 midlong 计算（不削弱）。
"""
import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

MOD = "backend.services.mlto.midlong_portfolio_risk"


def _fresh(monkeypatch, max_long_lane="8"):
    monkeypatch.setenv("MIDLONG_MAX_OPEN_POSITIONS", "4")
    monkeypatch.setenv("MIDLONG_MAX_LONG_LANE_POSITIONS", max_long_lane)
    monkeypatch.setenv("MIDLONG_PORTFOLIO_GATE_ENABLED", "true")
    monkeypatch.setenv("MIDLONG_MAX_NET_EXPOSURE_PCT", "1.5")
    monkeypatch.setenv("MIDLONG_CORR_CLUSTER_MAX", "2")
    monkeypatch.setenv("MIDLONG_CORR_CLUSTER_SYMBOLS", "BTC,ETH,SOL")
    monkeypatch.setenv("MIDLONG_MAX_SAME_SYMBOL_POSITIONS", "0")
    mod = importlib.import_module(MOD)
    return importlib.reload(mod)


def _mid_pos(symbol, nature="swing", tier="mid"):
    return {"symbol": symbol, "side": "long", "size": 1.0, "entry_price": 100.0,
            "mark_price": 100.0, "margin": 10.0, "trade_nature": nature, "timeframe_tier": tier}


def _long_pos(symbol):
    return _mid_pos(symbol, nature="trend_follow", tier="long")


def test_long_lane_cap_counts_only_long_positions(monkeypatch):
    mod = _fresh(monkeypatch)
    # 4 笔 mid 仓（占满全局帽）+ 0 笔长车道 → long_lane 开仓应放行
    positions = [_mid_pos(s) for s in ("XPL", "UNI", "BNB", "BTC")]
    ok, reason = mod.check_portfolio_open_allowed(
        symbol="ETH", action="buy", positions=positions, new_notional=100.0,
        long_lane=True,
    )
    assert ok is True, reason


def test_long_lane_cap_still_blocks_when_long_lane_full(monkeypatch):
    mod = _fresh(monkeypatch)
    positions = [_long_pos(s) for s in ("BTC", "ETH", "SOL", "BNB", "XRP", "LINK", "DOGE", "AVAX")]
    ok, reason = mod.check_portfolio_open_allowed(
        symbol="ADA", action="buy", positions=positions, new_notional=100.0,
        long_lane=True,
    )
    assert ok is False
    assert "midlong_open_positions" in reason


def test_mid_lane_keeps_global_cap(monkeypatch):
    mod = _fresh(monkeypatch)
    positions = [_mid_pos(s) for s in ("XPL", "UNI", "BNB", "BTC")]
    ok, reason = mod.check_portfolio_open_allowed(
        symbol="ETH", action="buy", positions=positions, new_notional=100.0,
        long_lane=False,
    )
    assert ok is False
    assert "midlong_open_positions" in reason


def test_long_lane_net_exposure_still_counts_all_midlong(monkeypatch):
    """净敞口不因 long_lane 口径而削弱：mid 仓的名义照常计入 signed 敞口。"""
    mod = _fresh(monkeypatch)
    positions = [_mid_pos("UNI"), _mid_pos("BNB")]
    for p in positions:
        p["size"] = 40.0  # 每笔名义 4000 → 合计 8000，新开 +100 → 远超 1.5×equity
    ok, reason = mod.check_portfolio_open_allowed(
        symbol="ETH", action="buy", positions=positions, new_notional=100.0,
        long_lane=True, portfolio={"balance": {"total_equity": 1000.0}},
    )
    assert ok is False
    assert "net_exposure" in reason
