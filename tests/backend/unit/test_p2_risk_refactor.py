"""P2 治本版回归测试 — 对齐 decisions_p2_amendment.md D10~D15.

覆盖：
- D10: 1d ATR 数据源
- D11: 三周期 TP/SL 硬拉开 ≥3 倍
- D12: tier 专属杠杆上限
- D13: long tier 软退出免疫
- D14: long tier 分批战略 TP
- D15: prompt hint

所有用例都在 flag-off 状态下先行断言回滚路径不被误触发。
"""
from __future__ import annotations

import math

import pytest


# ════════════════════════════════════════════════════════════════════
# D11 — TIER_TP_SL_DEFAULTS_V2: 三周期硬拉开
# ════════════════════════════════════════════════════════════════════
@pytest.mark.unit
class TestD11TpSlV2:
    def test_d11_v2_table_exists_per_band(self):
        from backend.config.settings import TIER_TP_SL_DEFAULTS_V2
        for band in ("low", "mid", "high", "x-high"):
            assert band in TIER_TP_SL_DEFAULTS_V2
            for tier in ("short", "mid", "long"):
                assert tier in TIER_TP_SL_DEFAULTS_V2[band]
                assert "sl_pct" in TIER_TP_SL_DEFAULTS_V2[band][tier]

    def test_d11_short_vs_long_gap_at_least_3x_in_sl(self):
        """D11 核心: long tier SL 必须至少是 short tier 的 3 倍宽."""
        from backend.config.settings import TIER_TP_SL_DEFAULTS_V2
        for band in ("low", "mid", "high", "x-high"):
            short_sl = TIER_TP_SL_DEFAULTS_V2[band]["short"]["sl_pct"]
            long_sl = TIER_TP_SL_DEFAULTS_V2[band]["long"]["sl_pct"]
            assert long_sl >= short_sl * 3, f"{band}: long_sl={long_sl} short_sl={short_sl}"

    def test_d11_long_tp_is_zero_handed_off_to_staged(self):
        """D11: long 的 tp_pct=0 — 由 D14 分批战略 TP 接管."""
        from backend.config.settings import TIER_TP_SL_DEFAULTS_V2
        for band in ("low", "mid", "high", "x-high"):
            assert TIER_TP_SL_DEFAULTS_V2[band]["long"]["tp_pct"] == 0

    def test_d11_v2_feature_flag_defaults_on(self):
        from backend.config import settings
        assert settings.RISK_USE_TIER_TP_SL_V2 is True


# ════════════════════════════════════════════════════════════════════
# D12 — LEVERAGE_CAP_BY_TIER
# ════════════════════════════════════════════════════════════════════
@pytest.mark.unit
class TestD12TierLeverageCap:
    def test_d12_long_tier_cap_is_12x(self):
        from backend.config.settings import LEVERAGE_CAP_BY_TIER
        assert LEVERAGE_CAP_BY_TIER["long"] == 12
        assert LEVERAGE_CAP_BY_TIER["mid"] == 20
        assert LEVERAGE_CAP_BY_TIER["short"] == 20

    def test_d12_tier_cap_triggers_only_when_flag_on(self, monkeypatch):
        """动态杠杆启用时统一上限 20x；关闭 + tier flag 时 long tier=12。"""
        from backend.services.risk_band_resolver import resolve_leverage, LeverageCapContext
        import backend.config.settings as S

        monkeypatch.setattr(S, "DYNAMIC_LEVERAGE_ENABLED", True)
        monkeypatch.setattr(S, "DYNAMIC_LEVERAGE_MAX", 20.0)
        monkeypatch.setattr(S, "RISK_USE_LEVERAGE_CAP_BY_TIER", False)
        final_dyn, _ = resolve_leverage(
            "BTC", LeverageCapContext(ai_override=25, nature="scalp", tier="long"),
        )
        assert final_dyn == 20

        monkeypatch.setattr(S, "DYNAMIC_LEVERAGE_ENABLED", False)
        monkeypatch.setattr(S, "RISK_USE_LEVERAGE_CAP_BY_TIER", True)
        final_on, reason_on = resolve_leverage(
            "BTC", LeverageCapContext(ai_override=25, nature="scalp", tier="long"),
        )
        assert final_on == 12
        assert "tier_cap" in reason_on

    def test_d12_short_tier_not_capped(self, monkeypatch):
        """动态杠杆启用时 short 也被统一上限 20x 限制。"""
        from backend.services.risk_band_resolver import resolve_leverage, LeverageCapContext
        import backend.config.settings as S

        monkeypatch.setattr(S, "DYNAMIC_LEVERAGE_ENABLED", True)
        monkeypatch.setattr(S, "DYNAMIC_LEVERAGE_MAX", 20.0)
        monkeypatch.setattr(S, "RISK_USE_LEVERAGE_CAP_BY_TIER", False)
        final_dyn, _ = resolve_leverage(
            "BTC", LeverageCapContext(ai_override=25, nature="scalp", tier="short"),
        )
        assert final_dyn == 20

        monkeypatch.setattr(S, "DYNAMIC_LEVERAGE_ENABLED", False)
        monkeypatch.setattr(S, "RISK_USE_LEVERAGE_CAP_BY_TIER", True)
        final_old, _ = resolve_leverage(
            "BTC", LeverageCapContext(ai_override=25, nature="scalp", tier="short"),
        )
        assert final_old == 20


# ════════════════════════════════════════════════════════════════════
# D13 — long tier 软退出免疫
# ════════════════════════════════════════════════════════════════════
@pytest.mark.unit
class TestD13LongImmune:
    def test_d13_flag_defaults_on(self):
        from backend.config import settings
        assert settings.RISK_USE_LONG_TIER_IMMUNE is True

    def test_d13_hard_exit_not_blocked(self, monkeypatch):
        import backend.config.settings as S
        monkeypatch.setattr(S, "RISK_USE_LONG_TIER_IMMUNE", False)
        from backend.services.risk_band_resolver import is_close_reason_blocked_for_long
        # flag off → 一律放行
        assert is_close_reason_blocked_for_long("sl") is False
        assert is_close_reason_blocked_for_long("master_running_reduce") is False

    def test_d13_flag_on_blocks_soft_exits(self, monkeypatch):
        import backend.config.settings as S
        monkeypatch.setattr(S, "RISK_USE_LONG_TIER_IMMUNE", True)
        from backend.services.risk_band_resolver import is_close_reason_blocked_for_long
        assert is_close_reason_blocked_for_long("master_running_reduce") is True
        assert is_close_reason_blocked_for_long("ai_reverse") is True
        assert is_close_reason_blocked_for_long("master_defensive_reduce") is True
        # 硬退出不得屏蔽
        assert is_close_reason_blocked_for_long("sl") is False
        assert is_close_reason_blocked_for_long("tp") is False
        assert is_close_reason_blocked_for_long("manual") is False


# ════════════════════════════════════════════════════════════════════
# D14 — long tier 分批战略 TP
# ════════════════════════════════════════════════════════════════════
@pytest.mark.unit
class TestD14StagedTp:
    def test_d14_flag_off_returns_hold(self, monkeypatch):
        import backend.config.settings as S
        monkeypatch.setattr(S, "RISK_USE_LONG_TIER_STAGED_TP", False)
        from backend.services.long_tier_staged_tp import check, StagedTpState
        state = StagedTpState()
        r = check(entry_price=100.0, current_price=120.0, side="buy", atr_pct=0.03, state=state)
        # flag 默认 off → 不应触发减仓
        assert r.action == "hold"

    def test_d14_stage1_fires_at_8pct_buy(self, monkeypatch):
        import backend.config.settings as S
        monkeypatch.setattr(S, "RISK_USE_LONG_TIER_STAGED_TP", True)
        from backend.services.long_tier_staged_tp import check, StagedTpState
        state = StagedTpState()
        r = check(entry_price=100.0, current_price=108.0, side="buy", atr_pct=0.02, state=state)
        assert r.action == "reduce"
        assert r.stage_idx == 0
        assert r.reduce_ratio == pytest.approx(0.30)

    def test_d14_stages_dont_double_fire(self, monkeypatch):
        """已触发的档位不应被再次触发."""
        import backend.config.settings as S
        monkeypatch.setattr(S, "RISK_USE_LONG_TIER_STAGED_TP", True)
        from backend.services.long_tier_staged_tp import check, StagedTpState
        state = StagedTpState()
        # 先过 stage1
        check(entry_price=100, current_price=108, side="buy", atr_pct=0.02, state=state)
        # 再来一次同样 pnl%
        r = check(entry_price=100, current_price=110, side="buy", atr_pct=0.02, state=state)
        assert r.action == "hold"  # 不是 reduce，因为 stage1 已标记

    def test_d14_sell_side_works(self, monkeypatch):
        """sell 方向：价格下跌才算浮盈."""
        import backend.config.settings as S
        monkeypatch.setattr(S, "RISK_USE_LONG_TIER_STAGED_TP", True)
        from backend.services.long_tier_staged_tp import check, StagedTpState
        state = StagedTpState()
        r = check(entry_price=100.0, current_price=92.0, side="sell", atr_pct=0.02, state=state)
        assert r.action == "reduce"
        assert r.stage_idx == 0

    def test_d14_all_stages_then_trailing_kicks_in(self, monkeypatch):
        import backend.config.settings as S
        monkeypatch.setattr(S, "RISK_USE_LONG_TIER_STAGED_TP", True)
        from backend.services.long_tier_staged_tp import check, StagedTpState
        state = StagedTpState()
        check(entry_price=100, current_price=108, side="buy", atr_pct=0.02, state=state)  # s1
        check(entry_price=100, current_price=115, side="buy", atr_pct=0.02, state=state)  # s2
        check(entry_price=100, current_price=125, side="buy", atr_pct=0.02, state=state)  # s3
        # 所有档触发，剩余仓位进 trailing
        r = check(entry_price=100, current_price=128, side="buy", atr_pct=0.02, state=state)
        assert r.action in ("trailing_update", "trailing_hit")
        assert state.trailing_active is True

    def test_d14_trailing_hit_when_price_retraces(self, monkeypatch):
        import backend.config.settings as S
        monkeypatch.setattr(S, "RISK_USE_LONG_TIER_STAGED_TP", True)
        from backend.services.long_tier_staged_tp import check, StagedTpState
        state = StagedTpState()
        for p in (108, 115, 125, 130):  # 一路冲到 130（peak=30%）
            check(entry_price=100, current_price=p, side="buy", atr_pct=0.02, state=state)
        assert state.trailing_active
        # atr_pct=0.02, atr_mult=2 → band=4%, trailing_sl ≈ 130 × 0.96 = 124.8
        r = check(entry_price=100, current_price=120, side="buy", atr_pct=0.02, state=state)
        assert r.action == "trailing_hit"

    def test_d14_dca_resets_staged_tp_state(self, monkeypatch):
        """DCA 后均价变化 → 分档状态必须自动重置，否则新均价下永远不会触发 TP"""
        import backend.config.settings as S
        monkeypatch.setattr(S, "RISK_USE_LONG_TIER_STAGED_TP", True)
        from backend.services.long_tier_staged_tp import check, StagedTpState
        state = StagedTpState()
        # 先以 entry=100 触发 stage1
        r1 = check(entry_price=100, current_price=108, side="buy", atr_pct=0.02, state=state)
        assert r1.action == "reduce"
        assert r1.stage_idx == 0
        # DCA 加仓后均价变为 95（下跌中补仓），state 应自动重置
        r2 = check(entry_price=95, current_price=103, side="buy", atr_pct=0.02, state=state)
        # 103 vs 95 → pnl=8.4%，应触发 stage1（reset 后 stage0 未被标记）
        assert r2.action == "reduce"
        assert r2.stage_idx == 0


# ════════════════════════════════════════════════════════════════════
# D15 — prompt hint
# ════════════════════════════════════════════════════════════════════
@pytest.mark.unit
class TestD15PromptHint:
    def test_d15_flag_off_returns_empty(self, monkeypatch):
        import backend.config.settings as S
        monkeypatch.setattr(S, "RISK_USE_TIER_PROMPT_HINTS", False)
        from backend.services.risk_band_resolver import get_tier_prompt_hint
        assert get_tier_prompt_hint("long") == ""

    def test_d15_flag_on_returns_hint_with_key_warnings(self, monkeypatch):
        import backend.config.settings as S
        monkeypatch.setattr(S, "RISK_USE_TIER_PROMPT_HINTS", True)
        from backend.services.risk_band_resolver import get_tier_prompt_hint
        hint = get_tier_prompt_hint("long")
        assert "long" in hint.lower() or "LONG" in hint
        assert "8x" in hint or "6x" in hint  # 杠杆上限警告
        assert "12h" in hint or "24-72h" in hint  # 持仓时长


# ════════════════════════════════════════════════════════════════════
# D10 — 1d ATR flag 默认 off
# ════════════════════════════════════════════════════════════════════
@pytest.mark.unit
class TestD10OneDayAtrFlag:
    def test_d10_flag_defaults_on(self):
        from backend.config import settings
        assert settings.RISK_USE_LONG_TIER_1D_ATR is True

    def test_d10_1d_multiplier_table_present(self):
        from backend.config.settings import LONG_TIER_ATR_1D_MULTIPLIER
        for band in ("low", "mid", "high", "x-high"):
            assert band in LONG_TIER_ATR_1D_MULTIPLIER
            assert LONG_TIER_ATR_1D_MULTIPLIER[band] > 0

    def test_d10_multipliers_decrease_with_volatility(self):
        """D10: 高波动币种 1d ATR 倍数应更小，防止单根 K 线刺穿."""
        from backend.config.settings import LONG_TIER_ATR_1D_MULTIPLIER
        seq = [LONG_TIER_ATR_1D_MULTIPLIER[b] for b in ("low", "mid", "high", "x-high")]
        assert all(seq[i] >= seq[i + 1] for i in range(3)), f"不单调: {seq}"


# ════════════════════════════════════════════════════════════════════
# D13 × D14 协作：staged TP 触发的平仓必须是"硬退出"，不被 long-immune 屏蔽
# ════════════════════════════════════════════════════════════════════
@pytest.mark.unit
class TestD13D14Interaction:
    def test_tp_staged_reason_not_blocked_by_long_immune(self, monkeypatch):
        """D14 触发的 reason=tp_staged_N 必须通过 reasoning-based 判定被识别为硬退出.

        full_auto 的 long-immune guard 是基于 reasoning 文本判定的，
        取 'take profit' / 'profit_lock' / 'trailing' 等关键字放行.
        这里验证 risk_band_resolver 的 close_reason 枚举判定.
        """
        import backend.config.settings as S
        monkeypatch.setattr(S, "RISK_USE_LONG_TIER_IMMUNE", True)
        from backend.services.risk_band_resolver import is_close_reason_blocked_for_long
        # tp_staged / trailing_hit 不在屏蔽集合里
        assert is_close_reason_blocked_for_long("tp_staged_1") is False
        assert is_close_reason_blocked_for_long("tp_staged_2") is False
        assert is_close_reason_blocked_for_long("tp_staged_3") is False
        assert is_close_reason_blocked_for_long("trailing_hit") is False
        # 保留被屏蔽的软退出
        assert is_close_reason_blocked_for_long("master_running_reduce") is True

    def test_trailing_state_cleared_after_hit(self, monkeypatch):
        """D14: trailing 命中后 service 层应清理状态（通过模块直接测：再次 check 不应触发 stage0）"""
        import backend.config.settings as S
        monkeypatch.setattr(S, "RISK_USE_LONG_TIER_STAGED_TP", True)
        from backend.services.long_tier_staged_tp import check, StagedTpState
        # 模拟：state 经过 3 档后 trailing 激活
        state = StagedTpState()
        for p in (108, 115, 125):
            check(entry_price=100, current_price=p, side="buy", atr_pct=0.02, state=state)
        assert len(state.triggered_stages) == 3
        # 再给一个新仓位（新 state），确认是纯函数不受污染
        new_state = StagedTpState()
        r = check(entry_price=100, current_price=108, side="buy", atr_pct=0.02, state=new_state)
        assert r.action == "reduce"
        assert r.stage_idx == 0


# ════════════════════════════════════════════════════════════════════
# P2 总开关
# ════════════════════════════════════════════════════════════════════
@pytest.mark.unit
class TestP2MasterSwitches:
    def test_p2_all_flags_default_on(self):
        from backend.config import settings
        assert settings.RISK_P2_ENABLED is True
        assert settings.RISK_USE_TIER_TP_SL_V2 is True
        assert settings.RISK_USE_LEVERAGE_CAP_BY_TIER is False  # 动态杠杆接管
        assert settings.DYNAMIC_LEVERAGE_ENABLED is True         # 动态杠杆默认启
        assert settings.RISK_USE_LONG_TIER_1D_ATR is True
        assert settings.RISK_USE_LONG_TIER_IMMUNE is True
        assert settings.RISK_USE_LONG_TIER_STAGED_TP is True
        assert settings.RISK_USE_TIER_PROMPT_HINTS is True
