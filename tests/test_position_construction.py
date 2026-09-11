# -*- coding: utf-8 -*-
"""PositionConstruction 单一权威（backend/services/position_construction.py）纯逻辑单测（不连库）。

覆盖：
  - 车道归一化（tier / trade_nature → lane）
  - construct：vol-target 名义、单币帽、单笔风险帽、杠杆由波动反推且 ≤ max、置信度 0.5–1.0 缩放、簇帽、gross 帽、余量为零
  - clamp：只缩不放、杠杆夹紧、caps_applied 记录、blocked 语义
  - 环境变量覆盖（全局 + 按车道）
"""
from __future__ import annotations

import os
import sys
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services import position_construction as pc  # noqa: E402


def _has_cap(caps, name: str) -> bool:
    return any(str(c).startswith(name) for c in caps)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("PC_") or k == "POSITION_CONSTRUCTION_ENFORCE":
            monkeypatch.delenv(k, raising=False)
    yield


def test_normalize_lane():
    assert pc.normalize_lane("short", None) == "short"
    assert pc.normalize_lane("scalp", None) == "short"
    assert pc.normalize_lane("mid", None) == "mid"
    assert pc.normalize_lane("long", None) == "long"
    assert pc.normalize_lane(None, "research") == "research"
    assert pc.normalize_lane(None, "arbitrage") == "arb"
    assert pc.normalize_lane("arbitrage", None) == "arb"
    assert pc.normalize_lane(None, "trend_follow") == "long"
    assert pc.normalize_lane(None, None) == "mid"


def test_lane_limits_defaults_and_env_override(monkeypatch):
    lim = pc.LaneLimits.for_lane("long")
    assert lim.vol_target == pytest.approx(0.35)
    assert lim.max_weight_per_symbol == pytest.approx(0.35)
    assert lim.max_leverage == pytest.approx(3.0)
    assert lim.cluster_cap == pytest.approx(0.50)
    assert lim.risk_per_trade_pct == pytest.approx(0.0125)
    assert pc.LaneLimits.for_lane("short").risk_per_trade_pct == pytest.approx(0.0075)
    monkeypatch.setenv("PC_MAX_LEVERAGE", "2")
    monkeypatch.setenv("PC_RISK_PER_TRADE_PCT_LONG", "0.02")
    lim2 = pc.LaneLimits.for_lane("long")
    assert lim2.max_leverage == pytest.approx(2.0)
    assert lim2.risk_per_trade_pct == pytest.approx(0.02)
    # 车道覆盖优先于全局
    monkeypatch.setenv("PC_MAX_LEVERAGE_LONG", "1.5")
    assert pc.LaneLimits.for_lane("long").max_leverage == pytest.approx(1.5)
    assert pc.LaneLimits.for_lane("mid").max_leverage == pytest.approx(2.0)
    # 非法值忽略 → 退回全局
    monkeypatch.setenv("PC_MAX_LEVERAGE_LONG", "abc")
    assert pc.LaneLimits.for_lane("long").max_leverage == pytest.approx(2.0)


def test_construct_vol_target_and_symbol_cap():
    eq = 10_000.0
    # 实现波动 70% → 名义 = eq × 0.35/0.70 = 50% → 单币帽 35%
    plan = pc.construct(lane="long", symbol="BTCUSDT", equity=eq, price=100.0, realized_vol=0.70, stop_distance_pct=None)
    assert plan.ok
    assert plan.notional == pytest.approx(0.35 * eq)
    assert _has_cap(plan.caps_applied, "max_weight_per_symbol")
    # 实现波动 140% → 25% 名义，无帽
    plan2 = pc.construct(lane="long", symbol="BTCUSDT", equity=eq, price=100.0, realized_vol=1.40, stop_distance_pct=None)
    assert plan2.notional == pytest.approx(0.25 * eq)
    assert plan2.caps_applied == []
    assert plan2.quantity == pytest.approx(plan2.notional / 100.0)
    assert plan2.weight == pytest.approx(0.25)


def test_construct_base_weight_from_trend_core_is_respected_then_capped():
    eq = 10_000.0
    plan = pc.construct(lane="long", symbol="BTCUSDT", equity=eq, price=100.0, realized_vol=0.5,
                        stop_distance_pct=None, base_weight=0.20)
    assert plan.notional == pytest.approx(0.20 * eq)
    plan2 = pc.construct(lane="long", symbol="BTCUSDT", equity=eq, price=100.0, realized_vol=0.5,
                         stop_distance_pct=None, base_weight=0.60)
    assert plan2.notional == pytest.approx(0.35 * eq)


def test_construct_risk_per_trade_cap():
    eq = 10_000.0
    # 止损距离 10%，长线风险帽 1.25% → 名义 ≤ 1250
    plan = pc.construct(lane="long", symbol="ETHUSDT", equity=eq, price=100.0, realized_vol=0.5, stop_distance_pct=0.10)
    assert plan.notional == pytest.approx(0.0125 * eq / 0.10)
    assert _has_cap(plan.caps_applied, "risk_per_trade")
    assert plan.stop_distance_pct == pytest.approx(0.10)
    assert plan.risk_pct == pytest.approx(0.0125, abs=1e-6)


def test_construct_leverage_from_vol():
    eq = 10_000.0
    # 波动 = 目标波动 → 3x；波动翻倍 → 1.5x；极高 → 1x
    p1 = pc.construct(lane="long", symbol="X", equity=eq, price=1.0, realized_vol=0.35, stop_distance_pct=None)
    p2 = pc.construct(lane="long", symbol="X", equity=eq, price=1.0, realized_vol=0.70, stop_distance_pct=None)
    p3 = pc.construct(lane="long", symbol="X", equity=eq, price=1.0, realized_vol=2.0, stop_distance_pct=None)
    assert p1.leverage == pytest.approx(3.0)
    assert p2.leverage == pytest.approx(1.5)
    assert p3.leverage == pytest.approx(1.0)
    assert p1.margin == pytest.approx(p1.notional / p1.leverage)
    # 无波动数据 → 1x，名义退化为单币帽一半
    p4 = pc.construct(lane="long", symbol="X", equity=eq, price=1.0, realized_vol=None, stop_distance_pct=None)
    assert p4.leverage == pytest.approx(1.0)
    assert p4.notional == pytest.approx(0.5 * 0.35 * eq)
    assert _has_cap(p4.caps_applied, "no_vol_fallback")


def test_construct_confidence_scales_only_half_to_one():
    eq = 10_000.0
    kw = dict(lane="mid", symbol="X", equity=eq, price=1.0, realized_vol=1.0, stop_distance_pct=None)
    base = pc.construct(confidence=None, **kw)
    lo = pc.construct(confidence=0.0, **kw)
    mid = pc.construct(confidence=0.5, **kw)
    hi = pc.construct(confidence=1.0, **kw)
    assert lo.notional == pytest.approx(0.5 * base.notional)
    assert mid.notional == pytest.approx(0.75 * base.notional)
    assert hi.notional == pytest.approx(base.notional)
    assert lo.confidence_scale == pytest.approx(0.5) and hi.confidence_scale == pytest.approx(1.0)
    # 百分数口径（85）按 0.85 处理，且永不放大
    over = pc.construct(confidence=85.0, **kw)
    assert over.notional == pytest.approx((0.5 + 0.5 * 0.85) * base.notional)
    assert over.notional <= base.notional


def test_construct_cluster_and_gross_caps_and_room_zero():
    eq = 10_000.0
    kw = dict(lane="long", symbol="BTCUSDT", equity=eq, price=1.0, realized_vol=0.35, stop_distance_pct=None)
    # 簇帽 50%：已有 4500 同簇 → 余量 500
    plan = pc.construct(cluster_open_notional=4500.0, **kw)
    assert plan.notional == pytest.approx(500.0)
    assert _has_cap(plan.caps_applied, "cluster_cap")
    # gross 帽 1.0：已有 9900 → 余量 100
    plan2 = pc.construct(lane_open_notional=9900.0, **kw)
    assert plan2.notional == pytest.approx(100.0)
    assert _has_cap(plan2.caps_applied, "gross_cap")
    # 单币已满 → 余量 0 → 不可开
    plan3 = pc.construct(symbol_open_notional=3500.0, **kw)
    assert not plan3.ok and plan3.notional == 0.0
    assert _has_cap(plan3.caps_applied, "max_weight_per_symbol")
    # 权益为 0 → 不可开
    plan4 = pc.construct(lane="long", symbol="BTCUSDT", equity=0.0, price=1.0, realized_vol=0.35, stop_distance_pct=None)
    assert not plan4.ok and _has_cap(plan4.caps_applied, "no_equity_or_price")


def test_clamp_only_shrinks_and_records_caps():
    eq = 10_000.0
    # 提议 6000 名义 @10x，止损 2%：单币帽 3500；风险帽 1.25%×10000/0.02 = 6250（不绑）；杠杆 → 3x
    res = pc.clamp(lane="long", symbol="BTCUSDT", equity=eq, price=100.0, notional=6000.0, leverage=10.0,
                   stop_distance_pct=0.02)
    assert res.changed and not res.blocked
    assert res.notional == pytest.approx(3500.0)
    assert res.leverage == pytest.approx(3.0)
    assert _has_cap(res.caps_applied, "max_weight_per_symbol") and _has_cap(res.caps_applied, "max_leverage")
    assert not _has_cap(res.caps_applied, "risk_per_trade")
    assert res.quantity == pytest.approx(3500.0 / 100.0)
    # 提议在帽内 → 原样通过
    res2 = pc.clamp(lane="long", symbol="BTCUSDT", equity=eq, price=100.0, notional=1000.0, leverage=2.0,
                    stop_distance_pct=0.05)
    assert not res2.changed and res2.notional == pytest.approx(1000.0) and res2.leverage == pytest.approx(2.0)
    assert res2.caps_applied == []


def test_clamp_risk_cap_binds_for_wide_stop():
    eq = 10_000.0
    # 止损 25%，短线风险帽 0.75% → 名义 ≤ 300
    res = pc.clamp(lane="short", symbol="DOGEUSDT", equity=eq, price=1.0, notional=2000.0, leverage=3.0,
                   stop_distance_pct=0.25)
    assert res.notional == pytest.approx(0.0075 * eq / 0.25)
    assert _has_cap(res.caps_applied, "risk_per_trade")
    assert not res.blocked


def test_clamp_blocked_when_no_room_or_too_small():
    eq = 10_000.0
    res = pc.clamp(lane="mid", symbol="BTCUSDT", equity=eq, price=1.0, notional=500.0, leverage=1.0,
                   symbol_open_notional=3500.0)
    assert res.blocked and res.notional == 0.0 and res.reason
    # 夹紧后低于最小名义（0.2% 权益 = 20）→ 拒
    res2 = pc.clamp(lane="mid", symbol="BTCUSDT", equity=eq, price=1.0, notional=500.0, leverage=1.0,
                    symbol_open_notional=3490.0)
    assert res2.blocked
    # 名义为 0 的提议 → blocked（调用方本就不该下）
    res3 = pc.clamp(lane="mid", symbol="BTCUSDT", equity=eq, price=1.0, notional=0.0, leverage=1.0)
    assert res3.blocked and not res3.changed


def test_clamp_lane_env_override(monkeypatch):
    eq = 10_000.0
    monkeypatch.setenv("PC_MAX_WEIGHT_PER_SYMBOL_SHORT", "0.10")
    res = pc.clamp(lane="short", symbol="BTCUSDT", equity=eq, price=1.0, notional=2000.0, leverage=2.0)
    assert res.notional == pytest.approx(1000.0)
    # 其他车道不受影响
    res2 = pc.clamp(lane="mid", symbol="BTCUSDT", equity=eq, price=1.0, notional=2000.0, leverage=2.0)
    assert res2.notional == pytest.approx(2000.0)


def test_enforce_flag(monkeypatch):
    assert pc.enforce_enabled() is True
    monkeypatch.setenv("POSITION_CONSTRUCTION_ENFORCE", "false")
    assert pc.enforce_enabled() is False


def test_cluster_of_and_describe():
    assert pc.cluster_of("BTCUSDT") == "majors"
    assert pc.cluster_of("ETH-USD") == "majors"
    assert pc.cluster_of("SOLUSDT") == "l1_beta"
    assert pc.cluster_of("DOGEUSDT") == "meme"
    assert pc.cluster_of("LINKUSDT") == "defi"
    assert pc.cluster_of("XYZUSDT") is None
    d = pc.describe_limits()
    assert set(d["lanes"]) == set(pc.LANES)
    assert d["enforce"] is True
