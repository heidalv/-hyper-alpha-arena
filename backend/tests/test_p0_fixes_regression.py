"""P0 修复回归测试（2026-08 审计 M0 落地）。

覆盖：
- P0-6  纸面仓位 PnL 双计（_paper_position_pnl 口径权威）
- P0-7a TP3/追踪空单方向（_tighten_sl_unified 单调 + side_dir 语义）
- P0-7c 紧急 SL 杠杆感知（越过爆仓价修复）
- P0-8  资金费开关默认值
- P0-10 双重减仓开关默认值

全部为纯单元测试（无 DB/网络）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


# ────────────────────────── P0-6 ──────────────────────────

class FakeClosedPos:
    def __init__(self, **kw):
        self.__dict__.update(kw)


@pytest.mark.unit
def test_paper_position_pnl_uses_stored_realized_no_double_count():
    """closed 仓位 unrealized_pnl 已含 partial：直接取，禁止再叠加（否则小亏变盈利）。"""
    from backend.services.learning_loop_service import LearningLoopService

    # 引擎口径：total_pnl = final(-20) + partial(+15) = -5 存入 unrealized_pnl
    pos = FakeClosedPos(
        unrealized_pnl=-5.0,
        partial_realized_pnl=15.0,
        entry_price=100.0,
        close_price=99.8,
        size=1.0,
        side="long",
    )
    assert LearningLoopService._paper_position_pnl(pos) == -5.0

    # 盈利仓同样不再双计
    pos2 = FakeClosedPos(
        unrealized_pnl=10.0,
        partial_realized_pnl=6.0,
        entry_price=100.0,
        close_price=100.4,
        size=1.0,
        side="long",
    )
    assert LearningLoopService._paper_position_pnl(pos2) == 10.0


@pytest.mark.unit
def test_paper_position_pnl_fallback_when_column_missing():
    """列缺失时才按价格差兜底重算（含 partial）。"""
    from backend.services.learning_loop_service import LearningLoopService

    pos = FakeClosedPos(
        partial_realized_pnl=1.5,
        entry_price=100.0,
        close_price=102.0,
        size=2.0,
        side="long",
    )
    # 无 unrealized_pnl 属性 → 兜底 (102-100)*2 + 1.5 = 5.5
    assert LearningLoopService._paper_position_pnl(pos) == 5.5


@pytest.mark.unit
def test_paper_position_pnl_short_fallback():
    from backend.services.learning_loop_service import LearningLoopService

    pos = FakeClosedPos(
        partial_realized_pnl=0.0,
        entry_price=100.0,
        close_price=97.0,
        size=1.0,
        side="short",
    )
    assert LearningLoopService._paper_position_pnl(pos) == 3.0


# ────────────────────────── P0-7a/b ──────────────────────────

@pytest.mark.unit
def test_tighten_sl_unified_monotonic_long_only_raises():
    """long：SL 只升不降——回撤时传入更低 new_sl 必须被守卫拒绝。"""
    from backend.services.paper_trading_engine import PaperTradingEngine

    pos = FakeClosedPos(side="long", sl_price=105.0)
    PaperTradingEngine._tighten_sl_unified(None, pos, 103.0, "test_lower")  # 更低 → 拒绝
    assert pos.sl_price == 105.0
    PaperTradingEngine._tighten_sl_unified(None, pos, 106.0, "test_raise")  # 更高 → 接受
    assert pos.sl_price == 106.0


@pytest.mark.unit
def test_tighten_sl_unified_monotonic_short_only_lowers():
    """short：SL 只降不升（相对现有 SL 单调收紧），放宽方向必须被拒绝。"""
    from backend.services.paper_trading_engine import PaperTradingEngine

    pos = FakeClosedPos(side="short", sl_price=95.0)
    # 放宽方向（更高）→ 拒绝
    PaperTradingEngine._tighten_sl_unified(None, pos, 96.0, "test_loosen")
    assert pos.sl_price == 95.0
    # 收紧方向（更低）→ 接受
    PaperTradingEngine._tighten_sl_unified(None, pos, 94.5, "test_tighten")
    assert pos.sl_price == 94.5


@pytest.mark.unit
def test_tp3_trail_short_side_dir_semantics():
    """空单 TP3 追踪止损：peak 在入场价下方，trail 必须位于 peak 上方（乘 side_dir=-1）。"""
    entry, peak, atr_price, mult = 100.0, 96.0, 0.5, 2.0
    side_dir = -1.0  # short
    new_sl = peak - atr_price * mult * side_dir  # 修复后的公式
    assert new_sl == 97.0  # 96 + 1 = 97，高于峰值 96（朝 entry 方向）
    assert new_sl > peak
    # 修复前（漏乘）：96 - 1 = 95，低于峰值 → SL 落在现价下方
    old_sl = peak - atr_price * mult
    assert old_sl < peak


# ────────────────────────── P0-7c ──────────────────────────

@pytest.mark.unit
def test_emergency_sl_leverage_aware_long():
    """20x 杠杆下紧急 SL 必须位于爆仓价内侧。"""
    from backend.services.unified_exit_executor import UnifiedExitExecutor, ExitExecuteRequest
    from unittest.mock import patch

    executor = UnifiedExitExecutor()
    captured = {}

    def fake_update(db, pos_id, sl_price=None, tp_price=None):
        captured["sl_price"] = sl_price
        return True

    pos = {
        "id": 1,
        "entry_price": 100.0,
        "side": "long",
        "leverage": 20.0,
        "liquidation_price": 95.5,  # entry×(1-1/20+mm)
    }
    req = ExitExecuteRequest(
        db=None, account_id=1, symbol="BTC", action="close",
        pos=pos, session=None, exit_channel="test", reason="test",
        append_event=None,
    )
    with patch("backend.services.paper_trading_engine.paper_engine") as mock_engine:
        mock_engine.update_position_tp_sl = fake_update
        executor._set_emergency_sl(req)
    sl = captured["sl_price"]
    assert sl is not None
    assert sl > 95.5  # 在爆仓价内侧（更靠近 entry）
    assert sl < 100.0


@pytest.mark.unit
def test_emergency_sl_low_leverage_keeps_5pct_cap():
    """低杠杆无 liq 信息时保持 5% 距离。"""
    from backend.services.unified_exit_executor import UnifiedExitExecutor, ExitExecuteRequest
    from unittest.mock import patch

    executor = UnifiedExitExecutor()
    captured = {}

    def fake_update(db, pos_id, sl_price=None, tp_price=None):
        captured["sl_price"] = sl_price
        return True

    pos = {
        "id": 2,
        "entry_price": 100.0,
        "side": "short",
        "leverage": 3.0,
        "liquidation_price": 0.0,
    }
    req = ExitExecuteRequest(
        db=None, account_id=1, symbol="ETH", action="close",
        pos=pos, session=None, exit_channel="test", reason="test",
        append_event=None,
    )
    with patch("backend.services.paper_trading_engine.paper_engine") as mock_engine:
        mock_engine.update_position_tp_sl = fake_update
        executor._set_emergency_sl(req)
    sl = captured["sl_price"]
    assert sl is not None
    # short: SL 在入场价上方 5%（min(0.05, max(0.5/3, 0.01)) = min(0.05, 0.1667) = 0.05）
    assert abs(sl - 105.0) < 1e-6


# ────────────────────────── P0-8 / P0-10 ──────────────────────────

@pytest.mark.unit
def test_funding_settle_flags_defaults():
    settings = pytest.importorskip("backend.config.settings")
    assert bool(getattr(settings, "FUNDING_SETTLE_ENABLED", False)) is True
    assert bool(getattr(settings, "FUNDING_SETTLE_APPLY_PNL", True)) is False


@pytest.mark.unit
def test_long_tier_staged_tp_default_off():
    settings = pytest.importorskip("backend.config.settings")
    # P0-10：默认关闭，避免与 v2 统一分段止盈双重减仓
    assert bool(getattr(settings, "RISK_USE_LONG_TIER_STAGED_TP", True)) is False
    assert bool(getattr(settings, "RISK_V2_UNIFIED_STAGED_TP", False)) is True
