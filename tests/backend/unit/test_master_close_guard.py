"""Unit tests for P3 M1 — master_close_guard.check_master_close_hardfact."""

import pytest

from backend.services.master_close_guard import (
    HardfactResult,
    check_master_close_hardfact,
    decide_by_flag,
)


class TestHardfactRules:
    def test_loss_pct_short_tier_met(self):
        r = check_master_close_hardfact(
            tier="short", action="close",
            entry_price=100, mark_price=97, sl_price=90,
            unrealized_pnl=-3, margin=100,
        )
        assert r.allow is True
        assert "loss_pct" in r.matched_rule

    def test_loss_pct_long_tier_not_met(self):
        # long tier 现要求浮亏 ≥ 9%（阈值已从 7% 收紧到 9%，见 settings
        # MASTER_CLOSE_MIN_LOSS_PCT_BY_TIER）；只亏 4% 不算硬事实，必须拦截。
        r = check_master_close_hardfact(
            tier="long", action="close",
            entry_price=100, mark_price=96, sl_price=85,
            unrealized_pnl=-4, margin=100,
        )
        # 结果必须是"拦截"（allow=False）。matched_rule 可能为空（无硬事实）或
        # 由 YAML 决策策略引擎给出具名 block 规则（如 block_master_close_tiny_loss）——
        # 两种都属于"拦截"，测试只校验不放行，不再假设 matched_rule 恒为空。
        assert r.allow is False

    def test_loss_pct_long_tier_met(self):
        # long tier 阈值 9%；用 12% 浮亏且价格已走到 SL 的 80%（越过 V5.2 的
        # 60% SL 逼近度门控 + 10% 保证金地板），确保命中"浮亏达阈值"硬事实放行。
        r = check_master_close_hardfact(
            tier="long", action="reduce",
            entry_price=100, mark_price=88, sl_price=85,
            unrealized_pnl=-12, margin=100,
        )
        assert r.allow is True
        assert "loss_pct" in r.matched_rule

    def test_sl_breach_allows(self):
        # 价格已经深度穿过 SL (1.6x)，应放行
        r = check_master_close_hardfact(
            tier="long", action="close",
            entry_price=100, mark_price=84, sl_price=90,
            unrealized_pnl=-16, margin=100,  # loss_pct 也会 hit 规则①
        )
        assert r.allow is True

    def test_sl_breach_under_threshold_not_enough(self):
        # breach=0.5 < 1.5，loss_pct=5% < 7%(long) → 不放行
        r = check_master_close_hardfact(
            tier="long", action="close",
            entry_price=100, mark_price=95, sl_price=90,
            unrealized_pnl=-5, margin=100,
        )
        assert r.allow is False

    def test_hard_reason_whitelist(self):
        r = check_master_close_hardfact(
            tier="long", action="close",
            entry_price=100, mark_price=101, sl_price=90,
            unrealized_pnl=+1, margin=100,
            reason_hint="profit_lock hit at stage 1",
        )
        assert r.allow is True
        assert "profit_lock" in r.matched_rule

    def test_risk_score_high_allows(self):
        r = check_master_close_hardfact(
            tier="mid", action="reduce",
            entry_price=100, mark_price=100, sl_price=95,
            unrealized_pnl=0, margin=100,
            risk_score=90,
        )
        assert r.allow is True
        assert "risk_score" in r.matched_rule

    def test_all_fail_rejects(self):
        # long, 浮盈, SL 未穿, 无 hard reason, risk_score 未高
        r = check_master_close_hardfact(
            tier="long", action="close",
            entry_price=100, mark_price=102, sl_price=93,
            unrealized_pnl=+2, margin=100,
            reason_hint="AI thinks sentiment changed",
        )
        assert r.allow is False
        assert r.matched_rule or "no hardfact" in r.detail or "close blocked" in r.detail or "block" in (r.detail or "").lower()

    def test_unknown_tier_falls_back_to_mid(self):
        # tier="xyz" → 回退用 mid 阈值（现为 6%，已从 4% 收紧）；浮亏 7% ≥ 6% 应放行。
        # close 动作不走 V5.2 reduce 逼近度门控，直接命中浮亏硬事实。
        r = check_master_close_hardfact(
            tier="xyz", action="close",
            entry_price=100, mark_price=93, sl_price=90,
            unrealized_pnl=-7, margin=100,
        )
        assert r.allow is True


class TestDecideByFlag:
    def _deny(self) -> HardfactResult:
        return HardfactResult(allow=False, matched_rule="", detail="no hardfact")

    def _allow(self) -> HardfactResult:
        return HardfactResult(allow=True, matched_rule="loss_pct", detail="")

    def test_off_never_blocks(self):
        block, tag = decide_by_flag(self._deny(), "off")
        assert block is False
        assert tag == ""

    def test_shadow_records_but_doesnt_block(self):
        block, tag = decide_by_flag(self._deny(), "shadow")
        assert block is False
        assert tag == "master_close_would_block_shadow"

    def test_enforce_blocks(self):
        block, tag = decide_by_flag(self._deny(), "enforce")
        assert block is True
        assert tag == "master_close_blocked_no_hardfact"

    def test_allow_path_never_blocks_anywhere(self):
        for fv in ("off", "shadow", "enforce"):
            block, tag = decide_by_flag(self._allow(), fv)
            assert block is False
            assert tag == ""

    def test_unknown_flag_defaults_to_passthrough(self):
        block, tag = decide_by_flag(self._deny(), "weird")
        assert block is False
        assert tag == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
