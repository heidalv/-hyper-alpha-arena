# -*- coding: utf-8 -*-
"""[2026-09-12 F39] 论题 should_close 反转确认闸契约。

现场（用户实测反馈）：ASTER/XRP 在开仓 2-3h、-1.7%~-2.9% 小亏时被
thesis_should_close / thesis_invalidation 全平，而其失效条件（1d 收盘破位）
远未触发；14 天反事实：163 笔亏损平仓 55%/58%/61% 在 +6h/+24h/+48h 收复
平仓价——无反转的小亏全离场≈抛硬币。

契约：flag-only should_close 必须三选一才放行全平：
- 价格确认（同向失效价被突破 / 论题方向翻反）；
- min_hold 已满（mid 12h / long 72h）且仍浮亏；
- 紧急亏损 ≥ tier 紧急阈值。
否则 (False, ...) 不放行——行情没反转就不离场。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.full_auto import midlong_position_manager as mpm  # noqa: E402


def _th(direction="long", inv_price=None, should_close=True):
    inv = {"price": inv_price, "condition": "x"} if inv_price else {}
    return SimpleNamespace(direction=direction, invalidation=inv, should_close=should_close)


def _pos(mark, side="long", tier="mid"):
    return {"symbol": "XRP", "side": side, "mark_price": mark,
            "timeframe_tier": tier, "trade_nature": "swing"}


def _call(mark, *, side="long", tier="mid", direction="long", inv_price=None,
          pnl=-0.02, hold=3.0):
    return mpm.thesis_should_close_confirmed(
        None, position=_pos(mark, side, tier), thesis=_th(direction, inv_price),
        tier=tier, pnl_pct=pnl, hold_hours=hold,
    )


def test_young_small_loss_no_confirmation_blocks():
    """年轻仓 + 小亏 + 无价格确认 + 无方向翻反 → 不放行（核心场景）。"""
    ok, why = _call(1.36, inv_price=1.30, pnl=-0.02, hold=3.0, tier="long")
    assert ok is False and "wait_price_confirmation" in why, (ok, why)


def test_inv_price_breach_confirms():
    """同向失效价被突破 → 放行（真反转）。"""
    ok, why = _call(1.29, inv_price=1.30, pnl=-0.02, hold=3.0)
    assert ok is True and "inv_price_confirmed" in why, (ok, why)


def test_direction_flip_confirms():
    """论题方向翻反 → 放行（主脑反转判定）。"""
    ok, why = _call(1.36, side="long", direction="short", pnl=-0.02, hold=3.0)
    assert ok is True and "thesis_direction_flipped" in why, (ok, why)


def test_min_hold_elapsed_with_loss_confirms():
    """min_hold 已满（long 72h）且浮亏 → 放行。"""
    ok, why = _call(1.36, inv_price=1.30, pnl=-0.02, hold=80.0, tier="long")
    assert ok is True and "min_hold_ok" in why, (ok, why)


def test_emergency_loss_exempts():
    """紧急亏损（long 5% 阈值，-6% 保证金）→ 放行。"""
    ok, why = _call(1.36, inv_price=1.30, pnl=-0.06, hold=3.0, tier="long")
    assert ok is True, (ok, why)


def test_mid_tier_min_hold_is_12h():
    """mid 12h：11h 小亏无确认 → 拦截；13h → 放行。"""
    ok, why = _call(1.36, inv_price=1.30, pnl=-0.01, hold=11.0, tier="mid")
    assert ok is False, (ok, why)
    ok2, why2 = _call(1.36, inv_price=1.30, pnl=-0.01, hold=13.0, tier="mid")
    assert ok2 is True and "min_hold_ok" in why2, (ok2, why2)


def test_manage_flow_wired_to_gate():
    """模式锁：manage_position 必须接入确认闸与开关。"""
    import inspect
    src = inspect.getsource(mpm.manage_position)
    assert "thesis_should_close_confirmed" in src
    assert "MIDLONG_THESIS_CLOSE_CONFIRM_ENABLED" in src
