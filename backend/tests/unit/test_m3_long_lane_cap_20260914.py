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
    # [验收轮4] 直接钉 settings 属性（env 已随用户指令 4→6，setenv 不影响已加载的 settings）
    from backend.config import settings as _s
    monkeypatch.setattr(_s, "MIDLONG_MAX_OPEN_POSITIONS", 4, raising=False)
    monkeypatch.setattr(_s, "MIDLONG_MAX_LONG_LANE_POSITIONS", int(max_long_lane), raising=False)
    monkeypatch.setattr(_s, "MIDLONG_PORTFOLIO_GATE_ENABLED", True, raising=False)
    monkeypatch.setattr(_s, "MIDLONG_MAX_NET_EXPOSURE_PCT", 1.5, raising=False)
    monkeypatch.setattr(_s, "MIDLONG_CORR_CLUSTER_MAX", 2, raising=False)
    monkeypatch.setattr(_s, "MIDLONG_CORR_CLUSTER_MAX_LONG", 3, raising=False)
    monkeypatch.setattr(_s, "MIDLONG_CORR_CLUSTER_SYMBOLS", "BTC,ETH,SOL", raising=False)
    monkeypatch.setattr(_s, "MIDLONG_MAX_SAME_SYMBOL_POSITIONS", 0, raising=False)
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


# ── [验收轮2 2026-09-14] 长车道专属簇帽 ──

def _fresh_cluster(monkeypatch, max_long=3):
    monkeypatch.setenv("MIDLONG_CORR_CLUSTER_SYMBOLS", "BTC,ETH,SOL")
    monkeypatch.setenv("MIDLONG_CORR_CLUSTER_MAX", "2")
    monkeypatch.setenv("MIDLONG_CORR_CLUSTER_MAX_LONG", str(max_long))
    return _fresh(monkeypatch)


def test_long_lane_cluster_cap_3_allows_btc_eth_sol(monkeypatch):
    """E1 核心宇宙 BTC+ETH 已持 → SOL 开仓放行（长车道专属帽 3）。"""
    mod = _fresh_cluster(monkeypatch, max_long=3)
    positions = [_long_pos("BTC"), _long_pos("ETH")]
    ok, reason = mod.check_portfolio_open_allowed(
        symbol="SOL", action="buy", positions=positions, new_notional=100.0,
        long_lane=True,
    )
    assert ok is True, reason


def test_mid_lane_keeps_cluster_cap_2(monkeypatch):
    """mid 车道开仓仍受全局簇帽 2 约束（9/9 山寨齐跌实证不回退）。"""
    mod = _fresh_cluster(monkeypatch)
    positions = [_mid_pos("BTC"), _mid_pos("ETH")]
    ok, reason = mod.check_portfolio_open_allowed(
        symbol="SOL", action="buy", positions=positions, new_notional=100.0,
        long_lane=False,
    )
    assert ok is False
    assert "corr_cluster" in reason


def test_long_lane_cluster_cap_still_binds_at_3(monkeypatch):
    """长车道簇帽 3 依然生效：BTC/ETH/SOL 三仓同向后再开 SOL → 拦。"""
    mod = _fresh_cluster(monkeypatch, max_long=3)
    positions = [_long_pos("BTC"), _long_pos("ETH"), _long_pos("SOL")]
    ok, reason = mod.check_portfolio_open_allowed(
        symbol="SOL", action="buy", positions=positions, new_notional=100.0,
        long_lane=True,
    )
    assert ok is False
    assert "corr_cluster" in reason


def test_brain_long_batch_suppressed_when_e1_exclusive():
    """[验收轮2] E1 独占时 mlto_cycle 不再派脑 tier=long 批次（源码护栏）。

    背景：E1 独占后脑的 tier=long 论题在收口处必被拒，实测每 ~2-3 分钟白烧
    一批 dual_call LLM（n=9、8 小时 0 成交）。源码必须显式检查 long_lane_exclusive。
    """
    from pathlib import Path

    src = Path(backend_file()).read_text(encoding="utf-8")
    assert "long_lane_exclusive" in src
    assert "E1 独占长车道" in src
    # 派发条件必须带 `and not _e1_exclusive`
    assert "not _e1_exclusive" in src


def backend_file():
    import backend.services.full_auto.mlto_cycle as m
    return m.__file__


# ── [验收轮3 2026-09-14] 中线饿死根治：并发帽/簇帽车道化 + factor_route 权威放行 ──

def test_mid_cap_counts_only_mid_positions(monkeypatch):
    """E1 5 个长仓不得占用中线并发帽：中线 1 仓时中线开仓放行。"""
    mod = _fresh(monkeypatch)
    positions = [_long_pos(s) for s in ("BTC", "ETH", "SOL", "BNB", "XRP")]
    positions.append(_mid_pos("UNI"))
    ok, reason = mod.check_portfolio_open_allowed(
        symbol="XPL", action="buy", positions=positions, new_notional=100.0,
        long_lane=False,
    )
    assert ok is True, reason


def test_mid_cap_still_blocks_when_mid_lane_full(monkeypatch):
    """中线自身 4 仓满 → 中线开仓被拦（车道帽独立生效）。"""
    mod = _fresh(monkeypatch)
    positions = [_mid_pos(s) for s in ("XPL", "UNI", "BNB", "BTC")]
    positions.extend(_long_pos(s) for s in ("ETH", "SOL"))  # 长仓不占中线帽
    ok, reason = mod.check_portfolio_open_allowed(
        symbol="ASTER", action="buy", positions=positions, new_notional=100.0,
        long_lane=False,
    )
    assert ok is False
    assert "midlong_open_positions" in reason


def test_mid_cluster_counts_only_mid_positions(monkeypatch):
    """E1 持 BTC/ETH 时，中线 ETH 开仓不再被跨车道簇计数误伤（中线簇 0 仓）。"""
    mod = _fresh_cluster(monkeypatch)
    positions = [_long_pos("BTC"), _long_pos("ETH")]
    ok, reason = mod.check_portfolio_open_allowed(
        symbol="ETH", action="buy", positions=positions, new_notional=100.0,
        long_lane=False,
    )
    assert ok is True, reason


def test_mid_cluster_own_cap_2_still_binds(monkeypatch):
    """中线自身簇 2 仓满 → 中线簇开仓被拦（9/9 实证不回退）。"""
    mod = _fresh_cluster(monkeypatch)
    positions = [_mid_pos("BTC"), _mid_pos("ETH")]
    ok, reason = mod.check_portfolio_open_allowed(
        symbol="SOL", action="buy", positions=positions, new_notional=100.0,
        long_lane=False,
    )
    assert ok is False
    assert "corr_cluster" in reason


def test_factor_route_authority_allowed_in_paper_ab(monkeypatch):
    """[验收轮3] 脑开启 + paper + AB 开关 → factor_route 允许开仓（A/B 车道真正通车）。"""
    import backend.services.full_auto.midlong_executor as me

    monkeypatch.setattr(
        "backend.config.settings.midlong_brain_enabled", lambda: True,
    )
    monkeypatch.setenv("MIDLONG_MID_FACTOR_ROUTE_AB", "true")
    assert me.authority_allows_open("mlto", "factor_route", trading_mode="paper") is True
    assert me.authority_allows_open("mlto", "mlto", trading_mode="paper") is True


def test_factor_route_authority_rollbacks(monkeypatch):
    """AB=false / live → factor_route 仍被拦（旧行为）。"""
    import backend.services.full_auto.midlong_executor as me

    monkeypatch.setattr(
        "backend.config.settings.midlong_brain_enabled", lambda: True,
    )
    monkeypatch.setenv("MIDLONG_MID_FACTOR_ROUTE_AB", "false")
    assert me.authority_allows_open("mlto", "factor_route", trading_mode="paper") is False
    monkeypatch.setenv("MIDLONG_MID_FACTOR_ROUTE_AB", "true")
    assert me.authority_allows_open("mlto", "factor_route", trading_mode="live") is False
