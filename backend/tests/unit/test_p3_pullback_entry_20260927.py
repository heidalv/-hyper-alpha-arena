# -*- coding: utf-8 -*-
"""[P3 大轮回 2026-09-27] §6.1 回踩限价入场契约（μ±0.3σ 挂单 + 15 分钟 TTL 放弃）。"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.full_auto.paper_execution import pullback_entry  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setenv("MIDLONG_PULLBACK_LIMIT_ENABLED", "true")


def test_intraday_long_places_pullback_limit():
    ot, px = pullback_entry(decision={"volatility_pct": 0.015}, side="buy",
                            price=100.0, tier="mid")
    assert ot == "limit"
    # 0.3 × 1.5% = 0.45% 回踩 → 99.55
    assert px == pytest.approx(99.55, abs=1e-6)


def test_intraday_short_places_above_market():
    ot, px = pullback_entry(decision={"volatility_pct": 0.015}, side="sell",
                            price=100.0, tier="mid")
    assert ot == "limit"
    assert px == pytest.approx(100.45, abs=1e-6)


def test_trend_lane_stays_market():
    ot, px = pullback_entry(decision={"volatility_pct": 0.015}, side="buy",
                            price=100.0, tier="long")
    assert ot == "market" and px == 100.0


def test_switch_off_rolls_back(monkeypatch):
    monkeypatch.setenv("MIDLONG_PULLBACK_LIMIT_ENABLED", "false")
    ot, px = pullback_entry(decision={}, side="buy", price=100.0, tier="mid")
    assert ot == "market" and px == 100.0


def test_missing_vol_uses_default():
    ot, px = pullback_entry(decision={}, side="buy", price=100.0, tier="short")
    assert ot == "limit"
    assert px == pytest.approx(100.0 * (1 - 0.3 * 0.015), abs=1e-6)


def test_vol_floor_protection():
    """波动率异常低（0.05%）时仍有最小回踩下限（0.3×0.001），不会挂在市价上；
    显式 0 视为缺失 → 用默认 1.5%。"""
    ot, px = pullback_entry(decision={"volatility_pct": 0.0005}, side="buy",
                            price=100.0, tier="mid")
    assert ot == "limit"
    assert px == pytest.approx(100.0 * (1 - 0.0003), abs=1e-6)
    assert pullback_entry(decision={"volatility_pct": 0.0}, side="buy",
                          price=100.0, tier="mid")[1] == pytest.approx(99.55, abs=1e-6)


def test_no_price_falls_back_market():
    assert pullback_entry(decision={}, side="buy", price=0, tier="mid") == ("market", 0.0)


def test_ttl_wiring_in_engine():
    """引擎 pending 限价单必须带 TTL 撤单（15 分钟放弃）。"""
    src = (ROOT / "backend" / "services" / "paper_trading_engine.py"
           ).read_text(encoding="utf-8")
    assert "PAPER_LIMIT_ENTRY_TTL_S" in src
    assert "pullback_ttl_expired" in src


def test_pullback_applies_on_both_executor_paths():
    """[P6 bug 修复④] 回踩必须在旧路径（默认）与新统一执行器路径都生效。"""
    src = (ROOT / "backend" / "services" / "full_auto" / "paper_execution.py"
           ).read_text(encoding="utf-8")
    assert "order_type=_entry_ot" in src, "旧路径必须消费回踩限价"
    assert 'order_type="market"' not in src, "旧路径不得再硬编码市价"
    i_branch = src.find("if host.is_unified_executor_on():")
    i_pb = src.find("_entry_ot, _entry_px = pullback_entry(")
    assert 0 < i_pb < i_branch, "回踩计算必须发生在执行器分支之前（两条路径共用）"


def test_pending_fill_restores_position_context():
    """[P6 bug 修复⑤] 挂单补单必须恢复 tier/nature/metadata（否则 P0 关联断裂）。"""
    src = (ROOT / "backend" / "services" / "paper_trading_engine.py"
           ).read_text(encoding="utf-8")
    assert "metadata_json" in src and "order.metadata_json" in src
    assert "position_metadata=_pm_restore" in src
    assert '"timeframe_tier": getattr(o, "timeframe_tier", None)' in src


# ── [P6 bug 修复 2026-09-28] 一次定价的止损输入 ──

class _Plan:
    def __init__(self, sl=None):
        self.stop_loss_price = sl


def test_stop_for_price_prefers_plan_sl():
    from backend.services.full_auto.paper_execution import _resolve_stop_for_price
    assert _resolve_stop_for_price(_Plan(97.0), 98.0) == 97.0, "plan 最终 SL 优先"
    assert _resolve_stop_for_price(_Plan(None), 98.0) == 98.0, "plan 无 SL 时回退 decision"
    assert _resolve_stop_for_price(_Plan(0), 0) is None
    assert _resolve_stop_for_price(object(), 96.5) == 96.5, "plan 无该属性时回退 decision"
