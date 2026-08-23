"""S0-6-scalp 软退出免疫扩展 单元测试（2026-08-23）。

验证改动 1（scalp 软退出免疫）：
  1. ``risk_band_resolver.is_close_reason_blocked_for_midlong`` 现覆盖 short/scalp tier，
     只屏蔽 Master 软退出（master_running* / master_defensive_reduce / ai_reverse），
     硬退出（sl/tp/emergency_drawdown/liquidation/manual）一律不屏蔽。
  2. ``unified_exit_executor._is_hard_exit`` 硬退出兜底判定。
  3. ``unified_exit_executor.should_block`` 接线：short 软退出被免疫拦截，硬退出放行。
  4. mid/long 原有行为回归（不因扩展而改变）。

运行：
  cd backend && python -m pytest tests/unit/test_scalp_soft_exit_immune_20260823.py -q
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest
from unittest.mock import patch


# ════════════════════════════════════════════════════════════════════
# 1. risk_band_resolver: short/scalp tier 软退出免疫
# ════════════════════════════════════════════════════════════════════
class TestShortScalpImmuneResolver:
    """is_close_reason_blocked_for_midlong 对 short/scalp tier 的行为。"""

    def _fn(self):
        from backend.services.risk_band_resolver import is_close_reason_blocked_for_midlong
        return is_close_reason_blocked_for_midlong

    def test_short_master_running_close_blocked(self):
        fn = self._fn()
        assert fn("master_running_close", "short") is True

    def test_short_master_running_blocked(self):
        fn = self._fn()
        assert fn("master_running", "short") is True

    def test_short_master_running_reduce_blocked(self):
        fn = self._fn()
        assert fn("master_running_reduce", "short") is True

    def test_short_master_defensive_reduce_blocked(self):
        fn = self._fn()
        assert fn("master_defensive_reduce", "short") is True

    def test_short_ai_reverse_blocked(self):
        fn = self._fn()
        assert fn("ai_reverse", "short") is True

    def test_scalp_alias_treated_as_short(self):
        """scalp 作为 short tier 的口语别名也应被免疫。"""
        fn = self._fn()
        assert fn("master_running_close", "scalp") is True

    # ── 硬退出绝不免疫 ──
    def test_short_sl_not_blocked(self):
        fn = self._fn()
        assert fn("sl", "short") is False

    def test_short_tp_not_blocked(self):
        fn = self._fn()
        assert fn("tp", "short") is False
        assert fn("tp_target", "short") is False
        assert fn("tp_staged", "short") is False

    def test_short_emergency_drawdown_not_blocked(self):
        fn = self._fn()
        assert fn("emergency_drawdown", "short") is False

    def test_short_liquidation_not_blocked(self):
        fn = self._fn()
        assert fn("liquidation", "short") is False

    def test_short_manual_not_blocked(self):
        fn = self._fn()
        assert fn("manual", "short") is False

    def test_short_profit_lock_not_blocked(self):
        fn = self._fn()
        assert fn("profit_lock", "short") is False
        assert fn("profit_lock_1", "short") is False

    def test_short_hardfact_not_blocked(self):
        fn = self._fn()
        assert fn("hardfact", "short") is False

    # ── 空/未知输入 ──
    def test_short_empty_reason_not_blocked(self):
        fn = self._fn()
        assert fn("", "short") is False
        assert fn(None, "short") is False

    def test_unknown_tier_not_blocked(self):
        fn = self._fn()
        assert fn("master_running_close", "swing") is False
        assert fn("master_running_close", "") is False

    # ── 开关沿用 S0-6 的 mid flag（RISK_USE_MID_TIER_IMMUNE）──
    def test_short_immune_follows_mid_flag_off(self):
        fn = self._fn()
        with patch("backend.config.settings.RISK_USE_MID_TIER_IMMUNE", False):
            assert fn("master_running_close", "short") is False

    def test_short_immune_follows_mid_flag_on(self):
        fn = self._fn()
        with patch("backend.config.settings.RISK_USE_MID_TIER_IMMUNE", True):
            assert fn("master_running_close", "short") is True


# ════════════════════════════════════════════════════════════════════
# 2. mid/long 原有行为回归
# ════════════════════════════════════════════════════════════════════
class TestMidLongRegression:
    def test_mid_still_blocks_soft_exit(self):
        from backend.services.risk_band_resolver import is_close_reason_blocked_for_midlong
        assert is_close_reason_blocked_for_midlong("master_running_close", "mid") is True
        assert is_close_reason_blocked_for_midlong("ai_reverse", "mid") is True

    def test_long_still_blocks_soft_exit(self):
        from backend.services.risk_band_resolver import is_close_reason_blocked_for_midlong
        assert is_close_reason_blocked_for_midlong("ai_reverse", "long") is True
        assert is_close_reason_blocked_for_midlong("master_running_close", "long") is True

    def test_mid_hard_exits_still_not_blocked(self):
        from backend.services.risk_band_resolver import is_close_reason_blocked_for_midlong
        assert is_close_reason_blocked_for_midlong("sl", "mid") is False
        assert is_close_reason_blocked_for_midlong("tp", "mid") is False
        assert is_close_reason_blocked_for_midlong("emergency_drawdown", "mid") is False
        assert is_close_reason_blocked_for_midlong("liquidation", "mid") is False


# ════════════════════════════════════════════════════════════════════
# 3. unified_exit_executor._is_hard_exit 硬退出兜底
# ════════════════════════════════════════════════════════════════════
class TestIsHardExitGuard:
    def _fn(self):
        from backend.services.unified_exit_executor import _is_hard_exit
        return _is_hard_exit

    def test_liquidation_channel_is_hard(self):
        assert self._fn()("", "liquidation") is True

    def test_emergency_drawdown_reason_is_hard(self):
        assert self._fn()("emergency_drawdown", "") is True

    def test_daily_loss_reason_is_hard(self):
        # 日亏硬顶（daily_loss / 日亏）绝不免疫
        assert self._fn()("daily_loss_exceeded", "") is True
        assert self._fn()("日亏-5%", "") is True

    def test_hardfact_reason_is_hard(self):
        assert self._fn()("hardfact matched", "") is True

    def test_sl_channel_is_hard(self):
        assert self._fn()("", "sl") is True

    def test_tp_channel_is_hard(self):
        assert self._fn()("", "tp") is True

    def test_master_soft_close_is_not_hard(self):
        assert self._fn()("", "master_running_close") is False
        assert self._fn()("master_running_close", "master_running_close") is False

    def test_ai_reverse_is_not_hard(self):
        assert self._fn()("", "ai_reverse") is False

    def test_empty_is_not_hard(self):
        assert self._fn()("", "") is False


# ════════════════════════════════════════════════════════════════════
# 4. unified_exit_executor.should_block 接线
# ════════════════════════════════════════════════════════════════════
class TestShouldBlockScalpImmuneWiring:
    def _req(self, tier, action, channel, reason=""):
        from backend.services.unified_exit_executor import ExitExecuteRequest
        return ExitExecuteRequest(
            db=None,
            account_id=1,
            symbol="TESTCOIN",
            action=action,
            pos={"timeframe_tier": tier},
            exit_channel=channel,
            reason=reason,
        )

    def _should_block(self, req):
        from backend.services.unified_exit_executor import unified_exit_executor
        return unified_exit_executor.should_block(req)

    def test_short_close_master_running_close_blocked(self):
        req = self._req("short", "close", "master_running_close")
        gate = self._should_block(req)
        assert gate.blocked is True
        assert gate.event_type == "midlong_soft_exit_immune"

    def test_short_reduce_master_running_reduce_blocked(self):
        req = self._req("short", "reduce", "master_running_reduce")
        gate = self._should_block(req)
        assert gate.blocked is True

    def test_mid_close_still_blocked(self):
        req = self._req("mid", "close", "master_running_close")
        gate = self._should_block(req)
        assert gate.blocked is True

    def test_short_liquidation_not_blocked_by_immune(self):
        """硬退出（liquidation）不得被软退出免疫拦截。"""
        req = self._req("short", "close", "liquidation")
        gate = self._should_block(req)
        assert gate.blocked is False

    def test_short_emergency_drawdown_not_blocked_by_immune(self):
        req = self._req("short", "close", "emergency_drawdown")
        gate = self._should_block(req)
        assert gate.blocked is False


if __name__ == "__main__":
    pytest.main([__file__, "-q", "--tb=short"])
