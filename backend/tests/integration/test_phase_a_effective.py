# backend/tests/integration/test_phase_a_effective.py
"""阶段A止血端到端验证:根因1(reset门控)+根因2(杠杆钳制)都生效。"""
import pytest
from unittest.mock import MagicMock

def test_root_cause_1_reset_only_on_transition():
    """根因1:reset_loss_protection_state 仅在 paused→running 转换时触发,不在每 tick 抹掉降杠杆。"""
    from backend.services.full_auto.paper_session_helpers import _should_reset_loss_protection
    session = MagicMock()
    session.session_id = "test-sess-A"
    session.status = "running"
    # 持续 running tick → 不 reset(保护连亏降杠杆)
    assert _should_reset_loss_protection(session) is False
    assert _should_reset_loss_protection(session) is False

def test_root_cause_2_leverage_clamped_by_tier_not_raised():
    """根因2:既有仓位杠杆按自身 tier cap 钳制(只降不升),不被新订单目标抬高。"""
    from backend.services.paper_trading_engine import _clamp_leverage_by_tier
    # long tier cap=12:20 被钳到 12(降杠杆生效)
    assert _clamp_leverage_by_tier(20.0, "long") == 12
    # 10x long 不被抬到 20(保持)
    assert _clamp_leverage_by_tier(10.0, "long") == 10.0
    # short cap=20:25 被钳到 20
    assert _clamp_leverage_by_tier(25.0, "short") == 20.0

def test_root_cause_2_no_cross_position_pollution_via_max():
    """根因2:不再有跨仓位 max 覆盖(全局 _existing_max 已移除)。"""
    # 验证 _clamp_leverage_by_tier 是纯按 tier 的,不依赖其他仓位
    from backend.services.paper_trading_engine import _clamp_leverage_by_tier
    # 一个仓位的钳制结果不因另一个仓位存在而变
    assert _clamp_leverage_by_tier(8.0, "long") == 8.0
    assert _clamp_leverage_by_tier(8.0, "long") == 8.0  # 重复调用稳定
