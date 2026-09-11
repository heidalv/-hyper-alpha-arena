# -*- coding: utf-8 -*-
"""2026-08-23 短线赚钱改造 A/B 单元测试：TP/SL 参数对齐 + MR TP cap + 空头条件化。"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pandas as pd
import pytest


# ────────────────────────── A: TP/SL 参数对齐 ──────────────────────────

def test_structure_stop_sl_tp_aligned_bounds():
    """高波动（ATR 2.4%）时 SL 封顶 1.15%、TP=1.5%（RR≈1.30 过 V5 闸）。"""
    from backend.services.scalp.structure_stop_calculator import structure_stop_calculator
    md = {
        "price": 100.0,
        "volatility_value": 0.024,
        "atr_pct": 0.024,
        "regime": {"name": "trending"},
        "klines": None,
    }
    sl_pct, tp_pct, sl_price, tp_price = structure_stop_calculator.compute_sl_tp(
        md, side="long", entry=100.0, market_aware=False,
    )
    assert abs(sl_pct - 0.0115) < 1e-9, f"SL 应封顶 1.15%: {sl_pct}"
    assert abs(tp_pct - 0.015) < 1e-9, f"TP 应为 1.5%: {tp_pct}"
    assert tp_pct / sl_pct >= 1.3, f"RR≥1.3: {tp_pct/sl_pct:.3f}"
    assert abs(sl_price - 98.85) < 1e-9
    assert abs(tp_price - 101.5) < 1e-9


def test_structure_stop_sl_floor_and_rr():
    """低波动币 SL 不低于 0.7%，RR 恒 1.5（0.9%:1.35% 也在界内）。"""
    from backend.services.scalp.structure_stop_calculator import structure_stop_calculator
    md = {
        "price": 50.0,
        "volatility_value": 0.004,   # 0.4% → compute_atr_pct clamp 到 0.6%
        "atr_pct": 0.004,
        "klines": None,
    }
    sl_pct, tp_pct, _, _ = structure_stop_calculator.compute_sl_tp(
        md, side="short", entry=50.0, market_aware=False,
    )
    assert sl_pct >= 0.007, f"SL 下限 0.7%: {sl_pct}"
    assert sl_pct <= 0.0115
    assert tp_pct >= 0.009 and tp_pct <= 0.015
    assert tp_pct / sl_pct >= 1.2, f"RR≥1.2: {tp_pct/sl_pct:.3f}"


def test_structure_stop_short_side_prices():
    """空头 SL/TP 价格方向正确。"""
    from backend.services.scalp.structure_stop_calculator import structure_stop_calculator
    md = {"price": 100.0, "volatility_value": 0.01, "atr_pct": 0.01, "klines": None}
    sl_pct, tp_pct, sl_price, tp_price = structure_stop_calculator.compute_sl_tp(
        md, side="short", entry=100.0,
    )
    assert sl_price > 100.0 > tp_price
    assert abs(sl_price - 100 * (1 + sl_pct)) < 1e-9
    assert abs(tp_price - 100 * (1 - tp_pct)) < 1e-9


# ────────────────────────── A: MR TP cap ──────────────────────────

def _mr_market_data(price, swing_low, swing_high):
    import numpy as np
    n = 48
    closes = np.linspace(swing_low + 0.5, price, n)
    rows = []
    for i in range(n):
        c = float(closes[i])
        rows.append({
            "open": c, "high": c * 1.004, "low": c * 0.996, "close": c,
        })
    df = pd.DataFrame(rows)
    # 尾部两根制造区间高沿（swing_levels 取尾部 48 根 min/max）
    df.loc[df.index[-1], "high"] = swing_high
    df.loc[df.index[-2], "high"] = swing_high
    df.loc[df.index[0], "low"] = swing_low
    df.loc[df.index[1], "low"] = swing_low
    return {
        "price": price,
        "mark_price": price,
        "klines": df,
    }


def test_mr_tp_sl_capped_20260823(monkeypatch):
    """宽区间高位超买做空：TP≤1.5%、SL∈[0.7%,1.2%]（旧口径 TP 可到 2.7%+）。"""
    from backend.services.scalp import scalp_ranging_mr as mr
    from backend.services.factor_engine.base_factors import factor_engine

    price = 104.0
    swing_low, swing_high = 95.0, 105.0  # 10% 宽区间，价格贴近高沿
    md = _mr_market_data(price, swing_low, swing_high)
    # RSI 强制超买：mock compute_rsi
    monkeypatch.setattr(factor_engine, "compute_rsi", lambda df: 85.0)
    monkeypatch.setattr(mr, "SCALP_MR_HIGH_BAND", 0.70)
    monkeypatch.setattr(mr, "SCALP_MR_LOW_BAND", 0.30)
    monkeypatch.setattr(mr, "SCALP_MR_RSI_OB", 60.0)
    monkeypatch.setattr(mr, "SCALP_MR_RSI_OS", 40.0)
    monkeypatch.setattr(mr, "SCALP_MR_MIN_RANGE_PCT", 0.01)
    monkeypatch.setattr(mr, "SCALP_MR_MAX_RANGE_PCT", 0.20)
    monkeypatch.setattr(mr, "SCALP_MR_TP_RANGE_FRAC", 0.55)
    # 关掉 learned 覆盖（无训练文件时本就原样返回，但显式隔离）。
    # [2026-09-02] 加 raising=False：apply_learned_mr 已在重构中移除
    # （backend/data/tp_sl_learned/ 下的 JSON 也已全部删除，代码库无任何引用），
    # 原写法会因属性不存在直接 AttributeError 而非跳过隔离。保留此行是为了
    # 若将来重新引入 learned 覆盖，本用例仍自动隔离它。
    monkeypatch.setattr(mr, "apply_learned_mr", lambda tp, sl: (tp, sl),
                        raising=False)

    sig = mr.evaluate_ranging_mr("BTC", md)
    assert sig.action == "sell", f"应触发空头 MR: {sig.action} {sig.reasoning}"
    # [2026-08-24 深挖B] SL 夹幅 0.7-1.15%→1.2-1.6%（猎杀带数据实证）；
    # [2026-08-26] TP 硬上限 1.2%→0.9%，RR 自洽抬升后 TP 可到 1.6%。
    assert 0.012 - 1e-9 <= sig.sl_pct <= 0.016 + 1e-9, f"MR SL∈[1.2%,1.6%]: {sig.sl_pct}"
    assert 0.009 - 1e-9 <= sig.tp_pct <= 0.016 + 1e-9, f"MR TP∈[0.9%,1.6%]: {sig.tp_pct}"
    assert sig.tp_pct / sig.sl_pct >= 0.75 - 1e-9, f"MR RR≥0.75: {sig.tp_pct/sig.sl_pct:.3f}"


# ────────────────────────── B: 空头条件化 ──────────────────────────

class _FakeAdvisory:
    def __init__(self):
        self.penalty = 0
        self.advisory_verdict = "neutral"
        self.range_position_5m = 0.5
        self.swing_low_5m = 99.0
        self.swing_high_5m = 101.0
        self.stop_clusters = []
        self.updated_at = time.time()


class _FakeRegime:
    def __init__(self, name):
        self.regime = name
        self.allow_open = True
        self.size_multiplier = 1.0
        self.detail = ""


def _make_signal(action="sell", direction="short", score=70):
    from backend.services.scalp_factor_router import ScalpSignal
    return ScalpSignal(
        action=action, confidence=score, factor_score=score,
        direction=direction, entry_price=100.0,
        sl_pct=0.01, tp_pct=0.015,
        sl_price=99.0, tp_price=101.5,
        reasoning="", source="test",
    )


def _gate_md(funding=0.0003, mid_bias="bearish"):
    rows = []
    for i in range(30):
        rows.append({"open": 100.0, "high": 100.5, "low": 99.7, "close": 100.3})
    return {
        "price": 100.0,
        "klines": pd.DataFrame(rows),
        "funding_rate": funding,
        "orchestrator": {"mid_bias": mid_bias, "short_bias": "neutral"},
        "regime": {"name": "trending"},
    }


def _patch_gate_deps(monkeypatch, regime_name="trending"):
    import importlib
    gate_mod = importlib.import_module("backend.services.scalp.scalp_execution_gate")
    monkeypatch.setattr(
        gate_mod, "classify_regime",
        lambda md: _FakeRegime(regime_name),
    )
    monkeypatch.setattr(
        gate_mod.scalp_advisory_cache, "get",
        lambda symbol: _FakeAdvisory(),
    )
    return gate_mod.scalp_execution_gate


def test_gate_short_requires_trend_down_pass(monkeypatch):
    """三条件齐备：trending + 4h bearish + funding 极端正 → 空头放行。"""
    gate = _patch_gate_deps(monkeypatch, "trending")
    md = _gate_md(funding=0.0003, mid_bias="bearish")
    dec = gate.evaluate("BTC", _make_signal(), md, account_id=14, mode="paper")
    assert dec.allowed, f"应放行: {dec.reason}"
    assert dec.tier in ("direct", "veto")


def test_gate_short_two_conditions_half_size_when_ranging(monkeypatch):
    """[2026-08-23 折中] 震荡市 + 4h偏空 + funding极端正 → 半仓参与（不 100% 禁开）。"""
    gate = _patch_gate_deps(monkeypatch, "ranging")
    md = _gate_md(funding=0.0003, mid_bias="bearish")
    dec = gate.evaluate("BTC", _make_signal(), md, account_id=14, mode="paper")
    assert dec.allowed, f"两条件应半仓放行: {dec.reason}"
    assert abs(dec.size_multiplier - 0.5) < 1e-9, f"size×0.5: {dec.size_multiplier}"


def test_gate_short_one_condition_probe_when_4h_bullish(monkeypatch):
    import importlib as _imp
    _settings = _imp.import_module("backend.config.settings")
    monkeypatch.setattr(_settings, "SCALP_SHORT_LIVE_STRICT", True, raising=False)

    # [2026-08-29 全面修复·撤回放宽] 默认 SCALP_SHORT_LIVE_STRICT=true：
    # 单条件（此处 funding 极端正但 4h 偏多）live 空头也硬拦——30 天空头
    # 全亏(-824 毛)/盈亏比1.10，"零成交"不能靠放行负 EV 方向解决。
    gate = _patch_gate_deps(monkeypatch, "trending")
    md = _gate_md(funding=0.0003, mid_bias="bullish")
    dec = gate.evaluate("BTC", _make_signal(), md, account_id=14, mode="live")
    assert not dec.allowed
    assert "空头条件未齐" in dec.reason


def test_gate_short_one_condition_probe_when_funding_low(monkeypatch):
    import importlib as _imp
    _settings = _imp.import_module("backend.config.settings")
    monkeypatch.setattr(_settings, "SCALP_SHORT_LIVE_STRICT", True, raising=False)

    # 单条件（4h 偏空但 funding 低于门槛）live 空头同样硬拦。
    gate = _patch_gate_deps(monkeypatch, "trending")
    md = _gate_md(funding=0.0, mid_bias="bearish")
    dec = gate.evaluate("BTC", _make_signal(), md, account_id=14, mode="live")
    assert not dec.allowed
    assert "空头条件未齐" in dec.reason


def test_gate_short_zero_condition_high_score_probe(monkeypatch):
    import importlib as _imp
    _settings = _imp.import_module("backend.config.settings")
    monkeypatch.setattr(_settings, "SCALP_SHORT_LIVE_STRICT", True, raising=False)

    """零条件高分：默认严格模式硬拦（回滚开关 SCALP_SHORT_LIVE_STRICT=false
    时恢复 0.125x 试探行为）。"""
    gate = _patch_gate_deps(monkeypatch, "trending")
    md = _gate_md(funding=0.0, mid_bias="bullish")
    dec = gate.evaluate("BTC", _make_signal(score=70), md, account_id=14, mode="live")
    assert not dec.allowed
    assert "空头条件未齐" in dec.reason


def test_gate_short_zero_condition_relaxed_when_strict_off(monkeypatch):
    """SCALP_SHORT_LIVE_STRICT=false 时恢复 8/29 上午的分档试探（回滚路径可用）。"""
    import importlib
    _settings = importlib.import_module("backend.config.settings")
    monkeypatch.setattr(_settings, "SCALP_SHORT_LIVE_STRICT", False, raising=False)
    gate = _patch_gate_deps(monkeypatch, "trending")
    md = _gate_md(funding=0.0, mid_bias="bullish")
    dec = gate.evaluate("BTC", _make_signal(score=70), md, account_id=14, mode="live")
    assert dec.allowed
    assert abs(dec.size_multiplier - 0.125) < 1e-9


def test_gate_short_zero_condition_low_score_blocked(monkeypatch):
    """两条件都不满足且低分时 live 仍硬拦。"""
    gate = _patch_gate_deps(monkeypatch, "trending")
    md = _gate_md(funding=0.0, mid_bias="bullish")
    dec = gate.evaluate("BTC", _make_signal(score=40), md, account_id=14, mode="live")
    assert not dec.allowed
    assert "空头条件未齐" in dec.reason


def test_gate_long_unaffected(monkeypatch):
    """多头不受空头条件影响（ranging 下仍走后续流程）。"""
    gate = _patch_gate_deps(monkeypatch, "ranging")
    md = _gate_md(funding=0.0, mid_bias="bullish")
    dec = gate.evaluate(
        "BTC", _make_signal(action="buy", direction="long", score=70),
        md, account_id=14, mode="paper",
    )
    assert dec.allowed, f"多头应放行: {dec.reason}"


def test_gate_short_flag_off_rollback(monkeypatch):
    """SCALP_SHORT_REQUIRES_TREND_DOWN=false 回滚 → 空头回到旧行为。"""
    import backend.config.settings as settings_mod
    gate = _patch_gate_deps(monkeypatch, "ranging")
    monkeypatch.setattr(settings_mod, "SCALP_SHORT_REQUIRES_TREND_DOWN", False)
    md = _gate_md(funding=0.0, mid_bias="bullish")
    dec = gate.evaluate("BTC", _make_signal(), md, account_id=14, mode="paper")
    assert dec.allowed, f"开关关闭应放行: {dec.reason}"
