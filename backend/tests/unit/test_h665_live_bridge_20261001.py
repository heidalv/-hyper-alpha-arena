# -*- coding: utf-8 -*-
"""[h665] 实盘执行桥纯逻辑测试(不触网)。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import live_bridge as LB  # noqa: E402


def test_resolve_caps_defaults():
    caps = LB._resolve_caps({"meta": {}})
    assert caps["total_notional_usd"] == 500.0
    assert caps["per_symbol_usd"] == 150.0
    assert caps["daily_loss_usd"] == 30.0
    assert caps["reject_break"] == 3
    assert caps["cooldown_s"] == 60.0


def test_resolve_caps_override():
    caps = LB._resolve_caps({"meta": {"live_caps": {
        "total_notional_usd": 100, "reject_break": 2}}})
    assert caps["total_notional_usd"] == 100.0
    assert caps["per_symbol_usd"] == 150.0
    assert caps["reject_break"] == 2


def test_same_quote_semantics():
    # 无 → 无:相同
    assert LB.LiveBridge._same_quote(None, None) is True
    # 无 → 有:不同(需挂单)
    assert LB.LiveBridge._same_quote(None, {"px": 1.0, "qty": 2.0}) is False
    # 价差超过 5bp:不同
    assert LB.LiveBridge._same_quote({"px": 100.0, "qty": 2.0},
                                     {"px": 100.1, "qty": 2.0}) is False
    # 量差 10% < 15%:相同(不撤改)
    assert LB.LiveBridge._same_quote({"px": 100.0, "qty": 2.0},
                                     {"px": 100.001, "qty": 2.2}) is True


def test_check_caps_total():
    lane = {"lane_id": "t", "mode": "live", "status": "active", "meta": {}}
    b = LB.LiveBridge(lane)
    b._day_start_equity = None
    ok, why = b.check_caps([{"symbol": "BNB", "notional": 450.0}],
                           new_notional=100.0)
    assert not ok and "total_notional_cap" in why
    ok2, _ = b.check_caps([{"symbol": "BNB", "notional": 100.0}],
                          new_notional=100.0)
    assert ok2


def test_check_caps_per_symbol():
    lane = {"lane_id": "t", "mode": "live", "status": "active", "meta": {}}
    b = LB.LiveBridge(lane)
    b._day_start_equity = None
    ok, why = b.check_caps([{"symbol": "BNB", "notional": 140.0}],
                           new_notional=20.0, symbol="BNB")
    assert not ok and "per_symbol_cap" in why


def test_config_drift_gate():
    """[h665c] 配置漂移硬闸:实盘与模拟任一 params 键不同 ⇒ 拒绝启动。"""
    import backend.api.hft_routes as R
    from backend.services import lane_registry as reg

    orig = reg.get_lane
    reg.get_lane = lambda lid: (
        {"meta": {"symbols": ["BNB", "UNI", "ENA"],
                  "params": {"spread_mult": 0.5, "trend_pause_bp": 15,
                             "compound_ratio": 0.0}}}
        if lid == "mm_asterdex"
        else {"meta": {"symbols": ["BNB", "UNI", "ENA"],
                       "params": {"spread_mult": 0.5, "trend_pause_bp": 30,
                                  "compound_ratio": 0.0}}})
    try:
        drift = R._config_drift()
        assert drift == ["params.trend_pause_bp"], f"应报漂移,实际 {drift}"
    finally:
        reg.get_lane = orig
    # 一致时不报漂移
    drift0 = R._config_drift()
    assert drift0 == [], f"真实车道应已同步一致,实际 {drift0}"


def test_check_caps_equity_linked():
    """[h674] 账户级限制随权益浮动:权益 200 ⇒ 总上限=min(500,2×200)=400、
    单币=min(150,0.5×200)=100。"""
    lane = {"lane_id": "t", "mode": "live", "status": "active", "meta": {}}
    b = LB.LiveBridge(lane)
    b._day_start_equity = None
    b.snapshot["balance"] = {"total_equity": 200.0}
    eff = b._effective_caps()
    assert eff["total"] == 400.0
    assert eff["per_symbol"] == 100.0
    ok, why = b.check_caps([{"symbol": "BNB", "notional": 350.0}],
                           new_notional=60.0)
    assert not ok and "total_notional_cap" in why
    ok2, _ = b.check_caps([{"symbol": "BNB", "notional": 350.0}],
                          new_notional=40.0)
    assert ok2


def test_bridge_noop_without_keys():
    # 无 env 无表(测试环境):enabled=False ⇒ 所有下单操作 no_op
    lane = {"lane_id": "t", "mode": "live", "status": "active", "meta": {}}
    b = LB.LiveBridge(lane)
    assert b.enabled is False
    res = b.sync_quotes("BNB", {"bid": {"px": 1.0, "qty": 1.0}})
    assert res["ok"] is False and res["reason"] == "no_keys_or_adapter"
    assert b.poll_fills("BNB") == []
