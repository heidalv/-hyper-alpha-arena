"""[阶段3f] Hardfat Shadow 并行(决策13)— 单元测试。

阶段3e 让 AI invalidation 驱动退出,hardfact 降为底线。灰度发布期间两者
并行:hardfat 仍 EVALUATE+LOG,但不拦截;AI invalidation 驱动实际退出。

本测试验证 RISK_P3_HARDFAT_SHADOW:
  A. false(默认) = enforce:hardfact 拦截 AI close/reduce(现状)
  B. true  = shadow:hardfat 只记日志不拦,AI exit 照常执行
  C. true 时 hardfat 已 allow 的路径不受影响(直接放行)
  D. Tier1(Agent exit)与 Tier2(master close)两条路径都支持
  E. min_hold_protection 不受 shadow 影响(独立门控,非 hardfat)
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

# 确保可 import backend.*
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from backend.services.unified_exit_executor import (
    ExitExecuteRequest,
    ExitGateResult,
    UnifiedExitExecutor,
    _hardfat_shadow_enabled,
)


# ════════════════════════════════════════════════════════════════════
# 辅助:构造一个会被 hardfat 拦截的请求(小亏 + 无硬事实 + 非白名单 reason)
# ════════════════════════════════════════════════════════════════════
def _make_blocking_pos():
    """一个无任何硬事实的仓位:小亏、SL 未穿透、risk 低、reason 无白名单。"""
    return {
        "timeframe_tier": "mid",
        "entry_price": 100.0,
        "mark_price": 99.5,      # 轻微浮亏 0.5%(margin=100)→ loss_pct≈0.5% < mid 4%
        "sl_price": 90.0,        # SL 距离 10,当前 0.5 → breach=0.05 < 1.5
        "unrealized_pnl": -0.5,  # 小亏
        "margin": 100.0,
        "opened_at": "2020-01-01T00:00:00+00:00",  # 远早于 min_hold
        "side": "long",
    }


def _make_tier2_req(action="close"):
    return ExitExecuteRequest(
        db=None, account_id=1, symbol="TEST", action=action,
        pos=_make_blocking_pos(),
        exit_channel="master_running",
        reason="ai_suggest_close",
        reasoning="LLM thinks risk up",
        confidence=None,
        tier_level=2,
    )


def _make_tier1_req(action="close"):
    return ExitExecuteRequest(
        db=None, account_id=1, symbol="TEST", action=action,
        pos=_make_blocking_pos(),
        exit_channel="trend_review",
        reason="trend_review",  # 注意:Agent 白名单会命中;下面测试会绕开
        reasoning="",
        confidence=None,
        tier_level=1,
    )


@pytest.fixture
def _shadow_off():
    """确保 shadow=false(enforce 模式)。"""
    with patch.dict(os.environ, {"RISK_P3_HARDFAT_SHADOW": "false"}, clear=False):
        yield


@pytest.fixture
def _shadow_on():
    with patch.dict(os.environ, {"RISK_P3_HARDFAT_SHADOW": "true"}, clear=False):
        yield


# ════════════════════════════════════════════════════════════════════
# A. flag 读取
# ════════════════════════════════════════════════════════════════════
class TestFlagParsing:
    def test_default_off(self):
        with patch.dict(os.environ, {}, clear=True):
            assert _hardfat_shadow_enabled() is False

    @pytest.mark.parametrize("val", ["true", "True", "1", "yes", "YES"])
    def test_truthy_values(self, val):
        with patch.dict(os.environ, {"RISK_P3_HARDFAT_SHADOW": val}, clear=True):
            assert _hardfat_shadow_enabled() is True

    @pytest.mark.parametrize("val", ["false", "False", "0", "no", "", "off"])
    def test_falsy_values(self, val):
        with patch.dict(os.environ, {"RISK_P3_HARDFAT_SHADOW": val}, clear=True):
            assert _hardfat_shadow_enabled() is False


def _tier2_ctx(shadow_on: bool):
    """构造 Tier2 enforce 上下文(确保 _eff_flag 不被 pace shadow 降级)。

    关键:paper_pace_controller.get_knobs().master_close_mode 默认 "shadow",
    会把 _eff_flag 降级为 "shadow" → decide_by_flag 返回 should_block=False,
    走原有 shadow 路径,不会到达新的 hardfat_shadow 分支。
    要测试 RISK_P3_HARDFAT_SHADOW,必须让 _eff_flag 保持 "enforce"。
    """
    env_val = "true" if shadow_on else "false"
    env_patch = {"RISK_P3_HARDFAT_SHADOW": env_val}
    knobs = SimpleNamespace(master_close_mode="enforce")
    return patch.dict(os.environ, env_patch, clear=False), \
           patch("backend.config.settings.RISK_P3_ENABLED", True), \
           patch(
               "backend.config.settings.RISK_P3_MASTER_CLOSE_REQUIRES_HARDFACT",
               "enforce",
           ), \
           patch(
               "backend.services.master_close_guard.check_master_min_hold_block",
               return_value=SimpleNamespace(allow=True, detail="ok"),
           ), \
           patch(
               "backend.services.paper_pace_controller.paper_pace_controller.get_knobs",
               return_value=knobs,
           )


# ════════════════════════════════════════════════════════════════════
# B. Tier2 master close:shadow=false 拦截,shadow=true 放行
# ════════════════════════════════════════════════════════════════════
class TestTier2Shadow:
    def test_shadow_off_blocks_as_before(self):
        """enforce 模式:hardfat 无硬事实 → 拦截(现状)。"""
        ex = UnifiedExitExecutor()
        req = _make_tier2_req("close")
        e, p1, p2, p3, p4 = _tier2_ctx(shadow_on=False)
        with e, p1, p2, p3, p4:
            res = ex.check_hardfact_gate(req, tier_level=2)
        assert res.blocked is True
        assert res.event_type != "hardfat_shadow_passthrough"

    def test_shadow_on_does_not_block(self):
        """shadow 模式:hardfat 意见=拦截,但不拦截,AI exit 照常执行。"""
        ex = UnifiedExitExecutor()
        req = _make_tier2_req("close")
        e, p1, p2, p3, p4 = _tier2_ctx(shadow_on=True)
        with e, p1, p2, p3, p4:
            res = ex.check_hardfact_gate(req, tier_level=2)
        assert res.blocked is False
        assert res.event_type == "hardfat_shadow_passthrough"

    def test_shadow_on_reduce_short_still_passthrough(self):
        """短线 reduce 即便 _eff_flag 被强制 enforce,shadow=true 仍放行。

        阶段3f 决策:shadow 是最高优先级(灰度对比),覆盖短线 reduce 强制 enforce。
        """
        ex = UnifiedExitExecutor()
        pos = _make_blocking_pos()
        pos["timeframe_tier"] = "short"
        pos["unrealized_pnl"] = -0.5  # 小亏
        req = ExitExecuteRequest(
            db=None, account_id=1, symbol="TEST", action="reduce",
            pos=pos, exit_channel="master_running_reduce",
            reason="ai_reduce", reasoning="", tier_level=2,
        )
        e, p1, p2, p3, p4 = _tier2_ctx(shadow_on=True)
        with e, p1, p2, p3, p4:
            res = ex.check_hardfact_gate(req, tier_level=2)
        assert res.blocked is False
        assert res.event_type == "hardfat_shadow_passthrough"


# ════════════════════════════════════════════════════════════════════
# C. Tier1 Agent exit:同样支持 shadow
# ════════════════════════════════════════════════════════════════════
class TestTier1Shadow:
    def test_tier1_shadow_off_blocks(self, _shadow_off):
        """Tier1 enforce:无白名单 + 无硬事实 → 拦截。"""
        ex = UnifiedExitExecutor()
        # 用非白名单 channel(避免命中 agent_exit_hardfact 白名单)
        req = ExitExecuteRequest(
            db=None, account_id=1, symbol="TEST", action="close",
            pos=_make_blocking_pos(),
            exit_channel="master_running",  # 非白名单
            reason="some_random_reason", reasoning="", tier_level=1,
        )
        res = ex.check_hardfact_gate(req, tier_level=1)
        assert res.blocked is True
        assert res.event_type == "agent_exit_blocked"

    def test_tier1_shadow_on_passthrough(self, _shadow_on):
        ex = UnifiedExitExecutor()
        req = ExitExecuteRequest(
            db=None, account_id=1, symbol="TEST", action="close",
            pos=_make_blocking_pos(),
            exit_channel="master_running",  # 非白名单 → hardfat 会拦
            reason="some_random_reason", reasoning="", tier_level=1,
        )
        res = ex.check_hardfact_gate(req, tier_level=1)
        assert res.blocked is False
        assert res.event_type == "hardfat_shadow_passthrough"

    def test_tier1_whitelist_still_allowed_under_shadow(self, _shadow_on):
        """Tier1:白名单命中 → hf.allow=True → 直接放行(不走 shadow 分支)。"""
        ex = UnifiedExitExecutor()
        req = ExitExecuteRequest(
            db=None, account_id=1, symbol="TEST", action="close",
            pos=_make_blocking_pos(),
            exit_channel="trend_review",  # 白名单
            reason="trend_review", reasoning="", tier_level=1,
        )
        res = ex.check_hardfact_gate(req, tier_level=1)
        assert res.blocked is False
        assert res.event_type == ""  # allow 路径,event_type 默认空


# ════════════════════════════════════════════════════════════════════
# D. min_hold_protection 独立于 shadow(非 hardfat)
# ════════════════════════════════════════════════════════════════════
class TestMinHoldIndependent:
    def test_min_hold_blocks_even_in_shadow(self, _shadow_on):
        """min_hold_protection 是独立门控,shadow 不绕过它。"""
        ex = UnifiedExitExecutor()
        req = _make_tier2_req("close")
        with patch("backend.config.settings.RISK_P3_ENABLED", True), \
             patch(
                 "backend.config.settings.RISK_P3_MASTER_CLOSE_REQUIRES_HARDFACT",
                 "enforce",
             ), \
             patch(
                 "backend.services.master_close_guard.check_master_min_hold_block",
                 return_value=SimpleNamespace(
                     allow=False, detail="min_hold_protection: held 5min < 180min",
                 ),
             ):
            res = ex.check_hardfact_gate(req, tier_level=2)
        assert res.blocked is True
        assert res.event_type == "min_hold_protection"
