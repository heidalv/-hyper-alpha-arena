"""ScalpFusionScorer 单测：短线因子 × AI周期概率引擎融合打分。

覆盖：模型不可用 / 支持方向 / 反对方向 / 低校准质量 / 开关关闭 五种场景，
确保任何异常/数据不足都安全降级为 delta=0（不影响 ScalpFactorRouter 原有行为）。
"""
import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.unit


def _klines_df(n=60, seed=1):
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0, 0.01, n)
    close = 100 * np.cumprod(1 + rets)
    high = close * (1 + np.abs(rng.normal(0, 0.003, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.003, n)))
    volume = rng.uniform(100, 200, n)
    return pd.DataFrame({"high": high, "low": low, "close": close, "volume": volume})


def _fake_result(available=True, prob_up=1 / 3, prob_down=1 / 3, calibration_quality=0.3,
                  direction="up", confidence=0.2, reason=""):
    from backend.services.cycle_direction_probability import CycleProbResult
    return CycleProbResult(
        available=available, tier="short", prob_up=prob_up, prob_down=prob_down,
        prob_range=max(0.0, 1 - prob_up - prob_down), direction=direction,
        confidence=confidence, calibration_quality=calibration_quality,
        top_drivers=["rsi", "atr_pct"], reason=reason,
    )


class TestScalpFusionScorer:
    def test_disabled_flag_is_noop(self, monkeypatch):
        """SCALP_FUSION_ENABLED=false → 全程不调用引擎，delta=0。"""
        import backend.services.scalp.scalp_fusion_scorer as sfs_mod
        monkeypatch.setattr(sfs_mod, "SCALP_FUSION_ENABLED", False)
        result = sfs_mod.scalp_fusion_scorer.compute_fusion_adjustment("long", _klines_df())
        assert result.delta == 0
        assert result.available is False
        assert result.breakdown == {}

    def test_neutral_direction_is_noop(self, monkeypatch):
        import backend.services.scalp.scalp_fusion_scorer as sfs_mod
        monkeypatch.setattr(sfs_mod, "SCALP_FUSION_ENABLED", True)
        result = sfs_mod.scalp_fusion_scorer.compute_fusion_adjustment("neutral", _klines_df())
        assert result.delta == 0

    def test_insufficient_klines_is_noop(self, monkeypatch):
        import backend.services.scalp.scalp_fusion_scorer as sfs_mod
        monkeypatch.setattr(sfs_mod, "SCALP_FUSION_ENABLED", True)
        short_df = _klines_df(n=10)
        result = sfs_mod.scalp_fusion_scorer.compute_fusion_adjustment("long", short_df)
        assert result.delta == 0
        assert "K线不足" in result.breakdown.get("cycle_prob_reason", "")

    def test_model_unavailable_is_noop(self, monkeypatch):
        """cycle_prob short 模型未训练/未加载 → 安全降级，不报错，delta=0。"""
        import backend.services.scalp.scalp_fusion_scorer as sfs_mod
        import backend.services.cycle_direction_probability as cdp_mod
        monkeypatch.setattr(sfs_mod, "SCALP_FUSION_ENABLED", True)
        monkeypatch.setattr(
            cdp_mod.cycle_probability_engine, "estimate",
            lambda tier, features: _fake_result(available=False, reason="模型未训练"),
        )
        result = sfs_mod.scalp_fusion_scorer.compute_fusion_adjustment("long", _klines_df())
        assert result.delta == 0
        assert result.available is False
        assert "模型未训练" in result.breakdown.get("cycle_prob_reason", "")

    def test_agreement_produces_positive_delta(self, monkeypatch):
        """AI概率引擎支持因子方向(看多且prob_up>prob_down) + 校准尚可 → delta为正。"""
        import backend.services.scalp.scalp_fusion_scorer as sfs_mod
        import backend.services.cycle_direction_probability as cdp_mod
        monkeypatch.setattr(sfs_mod, "SCALP_FUSION_ENABLED", True)
        monkeypatch.setattr(sfs_mod, "SCALP_FUSION_MAX_DELTA", 15)
        monkeypatch.setattr(sfs_mod, "SCALP_FUSION_MIN_CALIBRATION", 0.0)
        monkeypatch.setattr(
            cdp_mod.cycle_probability_engine, "estimate",
            lambda tier, features: _fake_result(
                prob_up=0.6, prob_down=0.2, calibration_quality=0.5, direction="up",
            ),
        )
        result = sfs_mod.scalp_fusion_scorer.compute_fusion_adjustment("long", _klines_df())
        assert result.available is True
        assert result.delta > 0
        assert result.breakdown["cycle_prob_dir"] == "up"
        assert result.breakdown["cycle_prob_calibration"] == pytest.approx(0.5)

    def test_disagreement_produces_negative_delta(self, monkeypatch):
        """AI概率引擎反对因子方向(看多但prob_down>prob_up) + 校准尚可 → delta为负。"""
        import backend.services.scalp.scalp_fusion_scorer as sfs_mod
        import backend.services.cycle_direction_probability as cdp_mod
        monkeypatch.setattr(sfs_mod, "SCALP_FUSION_ENABLED", True)
        monkeypatch.setattr(sfs_mod, "SCALP_FUSION_MAX_DELTA", 15)
        monkeypatch.setattr(sfs_mod, "SCALP_FUSION_MIN_CALIBRATION", 0.0)
        monkeypatch.setattr(
            cdp_mod.cycle_probability_engine, "estimate",
            lambda tier, features: _fake_result(
                prob_up=0.2, prob_down=0.6, calibration_quality=0.5, direction="down",
            ),
        )
        result = sfs_mod.scalp_fusion_scorer.compute_fusion_adjustment("long", _klines_df())
        assert result.available is True
        assert result.delta < 0

    def test_low_calibration_shrinks_delta_towards_zero(self, monkeypatch):
        """校准质量趋近0 → 即便概率强烈支持/反对，delta也应趋近0（校准感知设计）。"""
        import backend.services.scalp.scalp_fusion_scorer as sfs_mod
        import backend.services.cycle_direction_probability as cdp_mod
        monkeypatch.setattr(sfs_mod, "SCALP_FUSION_ENABLED", True)
        monkeypatch.setattr(sfs_mod, "SCALP_FUSION_MAX_DELTA", 15)
        monkeypatch.setattr(sfs_mod, "SCALP_FUSION_MIN_CALIBRATION", 0.0)
        monkeypatch.setattr(
            cdp_mod.cycle_probability_engine, "estimate",
            lambda tier, features: _fake_result(
                prob_up=0.9, prob_down=0.05, calibration_quality=0.01, direction="up",
            ),
        )
        result = sfs_mod.scalp_fusion_scorer.compute_fusion_adjustment("long", _klines_df())
        assert result.available is True
        # 0.01 校准质量下，理论 delta = agreement(~0.85) * 0.01 * 15 ≈ 0.13 → round为0
        assert abs(result.delta) <= 1

    def test_min_calibration_gate_skips_when_below_floor(self, monkeypatch):
        """SCALP_FUSION_MIN_CALIBRATION 设为硬门槛时，低于门槛应直接跳过融合。"""
        import backend.services.scalp.scalp_fusion_scorer as sfs_mod
        import backend.services.cycle_direction_probability as cdp_mod
        monkeypatch.setattr(sfs_mod, "SCALP_FUSION_ENABLED", True)
        monkeypatch.setattr(sfs_mod, "SCALP_FUSION_MAX_DELTA", 15)
        monkeypatch.setattr(sfs_mod, "SCALP_FUSION_MIN_CALIBRATION", 0.2)
        monkeypatch.setattr(
            cdp_mod.cycle_probability_engine, "estimate",
            lambda tier, features: _fake_result(
                prob_up=0.9, prob_down=0.05, calibration_quality=0.1, direction="up",
            ),
        )
        result = sfs_mod.scalp_fusion_scorer.compute_fusion_adjustment("long", _klines_df())
        assert result.delta == 0
        assert "跳过融合" in result.breakdown.get("cycle_prob_reason", "")

    def test_delta_clamped_to_max(self, monkeypatch):
        """delta 应被 clamp 在 ±SCALP_FUSION_MAX_DELTA 以内。"""
        import backend.services.scalp.scalp_fusion_scorer as sfs_mod
        import backend.services.cycle_direction_probability as cdp_mod
        monkeypatch.setattr(sfs_mod, "SCALP_FUSION_ENABLED", True)
        monkeypatch.setattr(sfs_mod, "SCALP_FUSION_MAX_DELTA", 15)
        monkeypatch.setattr(sfs_mod, "SCALP_FUSION_MIN_CALIBRATION", 0.0)
        monkeypatch.setattr(
            cdp_mod.cycle_probability_engine, "estimate",
            lambda tier, features: _fake_result(
                prob_up=1.0, prob_down=0.0, calibration_quality=1.0, direction="up",
            ),
        )
        result = sfs_mod.scalp_fusion_scorer.compute_fusion_adjustment("long", _klines_df())
        assert abs(result.delta) <= 15

    def test_exception_safely_degrades(self, monkeypatch):
        """引擎抛异常时应安全降级为 delta=0，不向上抛错误影响 ScalpFactorRouter。"""
        import backend.services.scalp.scalp_fusion_scorer as sfs_mod
        import backend.services.cycle_direction_probability as cdp_mod
        monkeypatch.setattr(sfs_mod, "SCALP_FUSION_ENABLED", True)

        def _boom(tier, features):
            raise RuntimeError("模拟引擎异常")

        monkeypatch.setattr(cdp_mod.cycle_probability_engine, "estimate", _boom)
        result = sfs_mod.scalp_fusion_scorer.compute_fusion_adjustment("long", _klines_df())
        assert result.delta == 0
        assert "异常降级" in result.breakdown.get("cycle_prob_reason", "")
