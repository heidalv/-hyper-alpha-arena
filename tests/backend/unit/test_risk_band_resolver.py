"""Stage E 风控重构回归测试（对齐 docs/research/decisions.md D1~D9 + cross_review.md P1~P11）。

每条测试顶部都标注它覆盖的决策编号，方便 PR review 一行追溯。
"""
from __future__ import annotations

import math

import pytest


# ════════════════════════════════════════════════════════════════════
# D3 + P3: vol band 解析
# ════════════════════════════════════════════════════════════════════
@pytest.mark.unit
class TestVolBandD3:
    def test_d3_main_symbols_mapped_correctly(self):
        """D3: 7 个实盘币的 vol band 必须精确对齐 decisions.md §D3"""
        from backend.services.risk_band_resolver import get_vol_band
        assert get_vol_band("BTC") == "low"
        assert get_vol_band("ETH") == "low"
        assert get_vol_band("BNB") == "low"
        assert get_vol_band("SOL") == "mid"
        assert get_vol_band("ASTER") == "mid"
        assert get_vol_band("VIRTUAL") == "high"
        assert get_vol_band("XPL") == "x-high"

    def test_d3_accepts_various_symbol_formats(self):
        """D3: btc / BTC / BTCUSDT 都应归到同一 band"""
        from backend.services.risk_band_resolver import get_vol_band
        assert get_vol_band("btc") == get_vol_band("BTC") == get_vol_band("BTCUSDT")

    def test_p3_unknown_symbol_fallback_to_mid(self):
        """P3: 未知 symbol 必须回退 unknown_fallback，且记 warning（不得抛异常）"""
        from backend.services.risk_band_resolver import get_vol_band
        assert get_vol_band("NOTEXIST_12345") == "mid"

    def test_d3_x_high_disabled_downgrades_to_high(self):
        """D3 feature flag: use_x_high=False 时 XPL 退化为 high（回滚兼容）"""
        from backend.services.risk_band_resolver import get_vol_band
        assert get_vol_band("XPL", use_x_high=False) == "high"


# ════════════════════════════════════════════════════════════════════
# D1: TP/SL 默认
# ════════════════════════════════════════════════════════════════════
@pytest.mark.unit
class TestTpSlDefaultsD1:
    def test_d1_low_band_short_tighter_than_current(self):
        """D1: low 带 short sl 必须 ≤ 旧默认 2.5%"""
        from backend.services.risk_band_resolver import get_tp_sl_defaults
        cfg = get_tp_sl_defaults("low", "short")
        assert cfg["sl_pct"] <= 0.025
        assert cfg["tp_pct"] <= 0.035
        assert cfg["tp_pct"] / cfg["sl_pct"] == pytest.approx(25 / 18, abs=0.1)

    def test_d1_x_high_band_has_wider_sl_than_low(self):
        """D1: x-high 带 sl 必须 > low 带（XPL 的 ATR_P50 就是 BTC 的 2-3 倍）"""
        from backend.services.risk_band_resolver import get_tp_sl_defaults
        low = get_tp_sl_defaults("low", "short")
        xhigh = get_tp_sl_defaults("x-high", "short")
        assert xhigh["sl_pct"] > low["sl_pct"] * 2

    def test_d1_long_tier_sl_is_zero_for_atr_driven(self):
        """D1: long tier 固定 pct=0, 走 ATR 动态"""
        from backend.services.risk_band_resolver import get_tp_sl_defaults
        for band in ("low", "mid", "high", "x-high"):
            cfg = get_tp_sl_defaults(band, "long")
            assert cfg["sl_pct"] == 0
            assert cfg["tp_pct"] == 0


# ════════════════════════════════════════════════════════════════════
# D2: ATR 倍数
# ════════════════════════════════════════════════════════════════════
@pytest.mark.unit
class TestAtrMultiplierD2:
    def test_d2_xhigh_mult_smaller_than_low(self):
        """D2: 重尾币种 (XPL) 的 ATR 倍数必须最小，防止被单根 K 线刺穿"""
        from backend.services.risk_band_resolver import get_atr_multiplier
        assert get_atr_multiplier("x-high", "short") < get_atr_multiplier("low", "short")
        assert get_atr_multiplier("x-high", "long") < get_atr_multiplier("low", "long")

    def test_d2_monotonic_decrease_with_band(self):
        """D2: 倍数随 band 单调递减 low > mid > high > x-high"""
        from backend.services.risk_band_resolver import get_atr_multiplier
        for tier in ("short", "mid", "long"):
            seq = [get_atr_multiplier(b, tier) for b in ("low", "mid", "high", "x-high")]
            assert all(seq[i] >= seq[i + 1] for i in range(3)), f"{tier} 不单调: {seq}"


# ════════════════════════════════════════════════════════════════════
# D4 + P1 + P4 + P6: 杠杆上限
# ════════════════════════════════════════════════════════════════════
@pytest.mark.unit
class TestLeverageCapD4:
    def test_d4_manual_configured_xpl_gets_20x_band_cap(self, monkeypatch):
        """手动配置币种：波动带仍为 x-high，但杠杆 band_cap 放宽至 20x"""
        import backend.services.trading_pairs_config as tpc
        monkeypatch.setattr(
            tpc, "get_user_trading_pairs_set",
            lambda **_: frozenset({"BTC", "ETH", "SOL", "BNB", "VIRTUAL", "ASTER", "XPL"}),
        )
        from backend.services.risk_band_resolver import (
            get_vol_band, resolve_leverage, LeverageCapContext,
        )
        assert get_vol_band("XPL") == "x-high"
        final, reason = resolve_leverage(
            "XPL", LeverageCapContext(ai_override=20, nature="scalp", count_same_bucket_open=0),
        )
        # 2026-06-22: nature 上限统一 20x，由动态杠杆/SL/V5 硬顶兜底防插针
        assert final == 20
        assert reason in ("band_cap", "manual_symbol_cap", "ai_override")

    def test_d4_auto_coin_symbol_keeps_vol_band_cap(self, monkeypatch):
        """AI 自动选币（不在 user_trading_pairs）仍走波动带杠杆上限"""
        import backend.services.trading_pairs_config as tpc
        monkeypatch.setattr(
            tpc, "get_user_trading_pairs_set",
            lambda **_: frozenset({"BTC", "ETH", "SOL", "BNB", "VIRTUAL", "ASTER", "XPL"}),
        )
        from backend.services.risk_band_resolver import resolve_leverage, LeverageCapContext
        final, reason = resolve_leverage(
            "ENA", LeverageCapContext(ai_override=20, nature="scalp", count_same_bucket_open=0),
        )
        # ENA unknown → mid band_cap=15，nature=20 → 15
        assert final == 15

    def test_d4_btc_band_cap_20x(self, monkeypatch):
        """D4: BTC (low 带) band_cap=20x；动态杠杆关时 ai=25 → 夹到 20x。"""
        import backend.config.settings as S
        monkeypatch.setattr(S, "DYNAMIC_LEVERAGE_ENABLED", False)
        from backend.services.risk_band_resolver import resolve_leverage, LeverageCapContext
        final, _ = resolve_leverage(
            "BTC", LeverageCapContext(ai_override=25, nature="scalp", count_same_bucket_open=0),
        )
        assert final == 20

    def test_p1_bucket_dilution_reduces_effective_cap(self, monkeypatch):
        """P1: 同桶已开 3 仓时 effective band cap = 20 / sqrt(4) = 10。"""
        import backend.config.settings as S
        monkeypatch.setattr(S, "DYNAMIC_LEVERAGE_ENABLED", False)
        from backend.services.risk_band_resolver import resolve_leverage, LeverageCapContext
        final, _ = resolve_leverage(
            "BTC", LeverageCapContext(ai_override=20, nature="scalp", count_same_bucket_open=3),
        )
        assert final == 10

    def test_p6_swing_nature_cap_is_15x(self, monkeypatch):
        """P6: swing nature 与 band 均为 20x 时取 20x（2026-06-22 统一上限）。"""
        import backend.config.settings as S
        monkeypatch.setattr(S, "DYNAMIC_LEVERAGE_ENABLED", False)
        from backend.services.risk_band_resolver import resolve_leverage, LeverageCapContext
        final, _ = resolve_leverage(
            "BTC", LeverageCapContext(ai_override=20, nature="swing", count_same_bucket_open=0),
        )
        assert final == 20

    def test_p4_ai_override_clamped_not_bypassing(self, monkeypatch):
        """P4: AI override 超过 cap 时被夹紧到 band/nature 上限。"""
        import backend.config.settings as S
        monkeypatch.setattr(S, "DYNAMIC_LEVERAGE_ENABLED", False)
        from backend.services.risk_band_resolver import resolve_leverage, LeverageCapContext
        final_low, _ = resolve_leverage(
            "BTC", LeverageCapContext(ai_override=8, nature="scalp"),
        )
        final_high, _ = resolve_leverage(
            "BTC", LeverageCapContext(ai_override=30, nature="scalp"),
        )
        assert final_low == 8
        assert final_high == 20


# ════════════════════════════════════════════════════════════════════
# D5 + P9: 相关性桶
# ════════════════════════════════════════════════════════════════════
@pytest.mark.unit
class TestCorrelationBucketD5:
    def test_d5_majors_bucket_contains_btc_eth(self):
        from backend.services.risk_band_resolver import get_correlation_bucket
        assert get_correlation_bucket("BTC")["name"] == "majors"
        assert get_correlation_bucket("ETH")["name"] == "majors"

    def test_d5_xpl_in_independent_bucket(self):
        """D5: XPL 必须单独成桶（相关系数 ≤ 0.42）"""
        from backend.services.risk_band_resolver import get_correlation_bucket
        b = get_correlation_bucket("XPL")
        assert b["name"] == "indep"
        assert b["max_concurrent_positions"] == 1

    def test_d5_bucket_cap_enforced(self):
        """D5: majors 桶已开 3 个仓位时，第 4 个必须被拒"""
        from backend.services.risk_band_resolver import check_bucket_can_open
        allowed, _ = check_bucket_can_open("SOL", {"majors": 3})
        assert allowed is False
        allowed, _ = check_bucket_can_open("SOL", {"majors": 2})
        assert allowed is True


# ════════════════════════════════════════════════════════════════════
# D9 + P2: 样本不足币种打折
# ════════════════════════════════════════════════════════════════════
@pytest.mark.unit
class TestSampleInsufficientD9:
    def test_d9_main_symbols_no_scale(self):
        from backend.services.risk_band_resolver import get_sample_insufficient_scale
        for sym in ("BTC", "ETH", "BNB", "SOL", "VIRTUAL"):
            assert get_sample_insufficient_scale(sym) == 1.0

    def test_d9_aster_xpl_bootstrap_scale(self):
        """D9: ASTER / XPL 在无 n_daily_bars 参数时退 bootstrap"""
        from backend.services.risk_band_resolver import get_sample_insufficient_scale
        assert get_sample_insufficient_scale("ASTER") == pytest.approx(0.77, abs=0.01)
        assert get_sample_insufficient_scale("XPL") == pytest.approx(0.82, abs=0.01)

    def test_p2_scale_is_sqrt_ratio(self):
        """P2: scale = sqrt(n/min)，n=365 时 = 1.0"""
        from backend.services.risk_band_resolver import get_sample_insufficient_scale
        assert get_sample_insufficient_scale("ASTER", n_daily_bars=365) == pytest.approx(1.0, abs=0.01)
        expected = math.sqrt(100 / 365)
        assert get_sample_insufficient_scale("XPL", n_daily_bars=100) == pytest.approx(max(0.5, expected), abs=0.01)


# ════════════════════════════════════════════════════════════════════
# D8 + P8: trade_nature 兜底
# ════════════════════════════════════════════════════════════════════
@pytest.mark.unit
class TestTradeNatureD8:
    def test_d8_given_nature_passes_through(self):
        from backend.services.risk_band_resolver import resolve_trade_nature
        nat, filled = resolve_trade_nature("swing")
        assert nat == "swing"
        assert filled is False

    def test_p8_short_hold_infers_scalp(self):
        from backend.services.risk_band_resolver import resolve_trade_nature
        nat, filled = resolve_trade_nature(None, expected_hold_hours=1.0)
        assert nat == "scalp"
        assert filled is True

    def test_p8_long_hold_infers_swing(self):
        from backend.services.risk_band_resolver import resolve_trade_nature
        nat, filled = resolve_trade_nature(None, expected_hold_hours=24)
        assert nat == "swing"
        assert filled is True

    def test_d8_no_nature_no_hold_defaults_intraday(self):
        from backend.services.risk_band_resolver import resolve_trade_nature
        nat, filled = resolve_trade_nature(None, expected_hold_hours=None)
        assert nat == "intraday"
        assert filled is True


# ════════════════════════════════════════════════════════════════════
# 整体 Stage E 总开关行为
# ════════════════════════════════════════════════════════════════════
@pytest.mark.unit
class TestStageEMasterSwitch:
    def test_stage_e_on_by_default(self):
        """P5: Stage E 风控已全面上线，flag 默认 on"""
        from backend.config import settings
        assert settings.RISK_STAGE_E_ENABLED is True
        assert settings.RISK_USE_VOL_BAND_DEFAULTS is True

    def test_hard_rollback_overrides_enabled(self, monkeypatch):
        """LEGACY_RISK_HARD_ROLLBACK=true 时 stage_e_active 必须返回 False"""
        from backend.services.risk_band_resolver import stage_e_active
        import backend.config.settings as S
        monkeypatch.setattr(S, "RISK_STAGE_E_ENABLED", True)
        monkeypatch.setattr(S, "LEGACY_RISK_HARD_ROLLBACK", True)
        assert stage_e_active() is False
