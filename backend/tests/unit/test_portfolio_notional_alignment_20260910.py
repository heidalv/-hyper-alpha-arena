# -*- coding: utf-8 -*-
"""[2026-09-10 §60] 组合闸「名义口径」契约测试。

背景（§60.1 实证）：组合闸的输入 `estimate_open_notional()` 用 `equity×margin×leverage`
口径，而真实建仓按 `PositionConstruction` 的「止损距离风险预算」口径
（`equity×risk_pct/sl_pct×分档`）。两者在当前配置下差约一个数量级 ⇒ 22,042 次净敞口拦截里
70% 的 `after_pct` 落在 200–500%、无一条 <100%，即"按幻影仓位拒单"。

本测试锁住：
  1. 同口径估计与 PositionConstruction 的实际产物一致（用真实 cap 公式核对）；
  2. 倍差有多离谱（记录性断言：旧口径 ≥ 5× 同口径，防止有人"修回去"）；
  3. 诊断函数不改变闸的判据（`check_portfolio_open_allowed` 仍用传入的 new_notional）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from backend.services.mlto.midlong_portfolio_risk import (  # noqa: E402
    check_portfolio_open_allowed,
    estimate_open_notional,
    estimate_open_notional_aligned,
)

EQUITY = 4700.0
MID_SL = 0.045
MID_RISK = 0.0075


def _pos(sym, side="long", size=1.0, px=100.0, tier="mid", nature="swing"):
    return {"symbol": sym, "side": side, "size": size, "entry_price": px,
            "mark_price": px, "trade_nature": nature, "timeframe_tier": tier}


def _portfolio(positions, equity=EQUITY):
    return {"balance": {"total_equity": equity}, "positions": positions}


# ── ① 同口径估计：与 PositionConstruction 的 risk_per_trade 约束一致 ──

def test_aligned_estimate_matches_position_construction_rule():
    """`equity × risk / SL` —— 与 paper 引擎真实产物（$825 @ equity≈4950）同量级。"""
    est = estimate_open_notional_aligned(equity=EQUITY, sl_pct=MID_SL, risk_pct=MID_RISK)
    assert est == pytest.approx(EQUITY * MID_RISK / MID_SL, rel=1e-9)
    assert 700 <= est <= 900, est  # 16.7% 权益：与实测 $825 同档


def test_aligned_estimate_scales_with_sl_and_tranche():
    a = estimate_open_notional_aligned(equity=EQUITY, sl_pct=0.02, risk_pct=MID_RISK)
    b = estimate_open_notional_aligned(equity=EQUITY, sl_pct=0.04, risk_pct=MID_RISK)
    assert a == pytest.approx(2 * b, rel=1e-9), "止损越近 → 名义越大（同风险预算）"
    c = estimate_open_notional_aligned(equity=EQUITY, sl_pct=MID_SL, risk_pct=MID_RISK,
                                       tranche_mult=0.15)
    d = estimate_open_notional_aligned(equity=EQUITY, sl_pct=MID_SL, risk_pct=MID_RISK,
                                       tranche_mult=0.30)
    assert d == pytest.approx(2 * c, rel=1e-9), "分档系数线性"


# ── ② 倍差（记录事实，防止"修回去"）──

def test_legacy_estimate_is_order_of_magnitude_above_aligned():
    legacy = estimate_open_notional(equity=EQUITY, margin_frac=0.15, leverage=10.0)
    aligned = estimate_open_notional_aligned(equity=EQUITY, sl_pct=MID_SL, risk_pct=MID_RISK)
    ratio = legacy / aligned
    assert legacy == pytest.approx(EQUITY * 0.15 * 10.0, rel=1e-9)  # 150% 权益
    assert ratio >= 5.0, f"旧口径 {legacy:.0f} vs 同口径 {aligned:.0f}，倍差仅 {ratio:.1f}x"


# ── ③ 幻影仓位的后果：BUILD 档单笔就"顶满/超顶"上限 ──

def test_build_stage_estimate_alone_exceeds_cap_on_empty_book():
    """空仓 + BUILD 档（margin 0.30 × 10x = 300% 权益）⇒ 单笔即被 150% 上限拒。"""
    from backend.config import settings
    cap = float(getattr(settings, "MIDLONG_MAX_NET_EXPOSURE_PCT", 1.5))
    legacy = estimate_open_notional(equity=EQUITY, margin_frac=0.30, leverage=10.0)
    ok, why = check_portfolio_open_allowed(
        symbol="BTC", action="buy", portfolio=_portfolio([]), new_notional=legacy,
    )
    assert legacy / EQUITY > cap, legacy / EQUITY
    assert ok is False and "net_exposure" in why, why


def test_aligned_estimate_would_pass_the_same_situation():
    """同口径下（16.7% 权益）空仓开仓不会被净敞口闸拦 —— 这就是"幻影拒单"的量化差异。"""
    aligned = estimate_open_notional_aligned(equity=EQUITY, sl_pct=MID_SL, risk_pct=MID_RISK)
    ok, why = check_portfolio_open_allowed(
        symbol="BTC", action="buy", portfolio=_portfolio([]), new_notional=aligned,
    )
    assert ok is True, why


# ── ④ 诊断函数不改变闸的判据 ──

def test_gate_still_uses_passed_notional(monkeypatch):
    """闸只看传入的 `new_notional`（诊断口径不参与判据，避免"顺手改行为"）。"""
    from backend.config import settings
    monkeypatch.setattr(settings, "MIDLONG_PORTFOLIO_GATE_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "MIDLONG_MAX_NET_EXPOSURE_PCT", 1.5, raising=False)
    monkeypatch.setattr(settings, "MIDLONG_CORR_CLUSTER_SYMBOLS", "", raising=False)
    monkeypatch.setattr(settings, "MIDLONG_MAX_OPEN_POSITIONS", 9, raising=False)

    small_ok, _ = check_portfolio_open_allowed(
        symbol="BTC", action="buy", portfolio=_portfolio([]), new_notional=100.0)
    big_ok, big_why = check_portfolio_open_allowed(
        symbol="BTC", action="buy", portfolio=_portfolio([]), new_notional=EQUITY * 5)
    assert small_ok is True and big_ok is False and "net_exposure" in big_why
