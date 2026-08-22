"""M0-11 复查平仓 min_hold 保护回归（2026-08-22）。

背景（_audit_exit_channels 实测）：
- swing trend_broken 58 笔均持 1.0h 合计 -13.5、master_running_close 30 笔均持 4.0h 合计 -12.4；
- 活到 6h+ 的退出通道全部为正（dust_cleanup 8.8h +14.2 / breakeven 6h +4.8 /
  max_hold_timeout 35.4h +4.1 / emergency 19.3h +5.9 / trend_follow 54.4h +29.3）；
- 设计结构 swing TP8.9%/SL4.6%（RR≈1.94）需要兑现窗口，1-4h 复查只是开仓噪声。

规则：规则/LLM 方向复查给出 close 时，mid 需 ≥12h、long 需 ≥72h（TIER_PROTECTION_PARAMS
min_hold_sec），保证金口径亏损 ≥6% 可紧急豁免；配置缺失 fail-open。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))


# ── M0-11：_review_min_hold_check ──────────────────────────
class TestReviewMinHoldCheck:
    def _check(self, **kw):
        from backend.services.full_auto.midlong_position_manager import (
            _review_min_hold_check,
        )
        defaults = dict(
            position={"leverage": 5.0},
            pos_tier="mid", pnl_pct=-0.01, hold_hours=1.0,
            side="long", sym="BTC",
        )
        defaults.update(kw)
        return _review_min_hold_check(None, **defaults)

    def test_within_min_hold_blocks(self):
        r = self._check(pos_tier="mid", hold_hours=1.0, pnl_pct=-0.01)
        assert r["ok"] is False

    def test_after_min_hold_allows(self):
        r = self._check(pos_tier="mid", hold_hours=12.5, pnl_pct=-0.01)
        assert r["ok"] is True

    def test_long_needs_72h(self):
        r = self._check(pos_tier="long", hold_hours=48.0, pnl_pct=-0.01)
        assert r["ok"] is False
        r2 = self._check(pos_tier="long", hold_hours=73.0, pnl_pct=-0.01)
        assert r2["ok"] is True

    def test_emergency_loss_exempts(self):
        # 保证金口径亏损 6% → 豁免（不必等 min_hold）
        r = self._check(pos_tier="mid", hold_hours=1.0, pnl_pct=-0.08)
        assert r["ok"] is True
        r2 = self._check(pos_tier="mid", hold_hours=1.0, pnl_pct=-0.04)
        assert r2["ok"] is False

    def test_scalp_tier_fail_open(self):
        r = self._check(pos_tier="short", hold_hours=1.0, pnl_pct=-0.01)
        assert r["ok"] is True

    def test_minor_pnl_near_entry_blocks(self):
        # 0.000% 附近被碎平的根因：pnl≈0 不触发紧急豁免 → 必须被 min_hold 拦截
        r = self._check(pos_tier="mid", hold_hours=2.0, pnl_pct=0.0)
        assert r["ok"] is False
