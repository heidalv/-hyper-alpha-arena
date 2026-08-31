"""S0-6 mid/short 出场免疫回归测试（2026-08-31 根因修复）。

根因：8/13 shadow 实验把 .env RISK_USE_MID_TIER_IMMUNE 置 false 后未回退，
中线 master_running_close 畅行无阻（8/27-31 实损 10 笔 -21.54，8/31 当天仍
2 笔 -4.42）。本测试钉住：enforce 时 mid/short 对 master 软退出免疫，
硬退出（sl/tp）不受影响；flag off 时放行（影子模式秒回退）。
"""
import pytest

from backend.services.risk_band_resolver import is_close_reason_blocked_for_midlong


def _set_flag(monkeypatch, on: bool):
    import backend.config.settings as s
    monkeypatch.setattr(s, "RISK_USE_MID_TIER_IMMUNE", on)


class TestMidlongImmunityResolver:
    def test_mid_blocks_master_running_close_when_on(self, monkeypatch):
        _set_flag(monkeypatch, True)
        assert is_close_reason_blocked_for_midlong("master_running_close", "mid")
        assert is_close_reason_blocked_for_midlong("master_running_reduce", "mid")
        assert is_close_reason_blocked_for_midlong("master_running", "mid")
        assert is_close_reason_blocked_for_midlong("ai_reverse", "mid")

    def test_short_scalp_immune_via_same_flag(self, monkeypatch):
        _set_flag(monkeypatch, True)
        assert is_close_reason_blocked_for_midlong("master_running_close", "short")
        assert is_close_reason_blocked_for_midlong("master_running_close", "scalp")

    def test_hard_exits_not_in_protected_list(self, monkeypatch):
        _set_flag(monkeypatch, True)
        for reason in ("sl", "tp", "tp_target", "profit_lock_1", "emergency_drawdown",
                       "manual", "liquidation", "thesis_invalidation", "trend_broken"):
            assert not is_close_reason_blocked_for_midlong(reason, "mid"), reason

    def test_flag_off_never_blocks(self, monkeypatch):
        _set_flag(monkeypatch, False)
        assert not is_close_reason_blocked_for_midlong("master_running_close", "mid")
        assert not is_close_reason_blocked_for_midlong("master_running_close", "short")

    def test_unknown_tier_not_blocked(self, monkeypatch):
        _set_flag(monkeypatch, True)
        assert not is_close_reason_blocked_for_midlong("master_running_close", "???")


class TestUnifiedExitImmunity:
    def _req(self, tier="mid", action="close", channel="master_running_close"):
        from backend.services.unified_exit_executor import ExitExecuteRequest
        return ExitExecuteRequest(
            db=None, account_id=1, symbol="BNB", action=action,
            pos={"timeframe_tier": tier, "side": "long"},
            exit_channel=channel, reason="master_running",
            append_event=lambda *a, **k: None, session=None,
        )

    def test_mid_master_close_blocked_by_immunity(self, monkeypatch):
        _set_flag(monkeypatch, True)
        from backend.services.unified_exit_executor import unified_exit_executor
        gate = unified_exit_executor.should_block(self._req(tier="mid"))
        assert gate.blocked
        assert gate.event_type == "midlong_soft_exit_immune"

    def test_mid_master_reduce_blocked_by_immunity(self, monkeypatch):
        _set_flag(monkeypatch, True)
        from backend.services.unified_exit_executor import unified_exit_executor
        gate = unified_exit_executor.should_block(
            self._req(tier="mid", action="reduce", channel="master_running_reduce")
        )
        assert gate.blocked
        assert gate.event_type == "midlong_soft_exit_immune"

    def test_flag_off_passes_immunity_layer(self, monkeypatch):
        _set_flag(monkeypatch, False)
        from backend.services.unified_exit_executor import unified_exit_executor
        gate = unified_exit_executor.should_block(self._req(tier="mid"))
        # flag off → 免疫层不拦截（后续保护层可能拦，但绝不是免疫事件）
        assert gate.event_type != "midlong_soft_exit_immune"

    def test_short_tier_also_immune(self, monkeypatch):
        _set_flag(monkeypatch, True)
        from backend.services.unified_exit_executor import unified_exit_executor
        gate = unified_exit_executor.should_block(self._req(tier="short"))
        assert gate.blocked
        assert gate.event_type == "midlong_soft_exit_immune"
