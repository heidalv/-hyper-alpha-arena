"""
test_regime_weighting_not_silently_broken — Regime 自适应权重契约回归测试。

背景（规划文档 §3.3）：`DynamicFactorWeighting` 此前存在三处静默失效，导致 regime
自适应权重自系统上线起从未真正生效过一次（2026-07-17 修复，见
factor_evaluation_pipeline.py 内注释）：
    1. `DynamicFactorWeighting()` 构造时少传必需的 `factor_engine` 位置参数，
       直接 TypeError，被外层 `except Exception` 静默吞掉（仅 debug 级日志）。
    2. 调用的是不存在的实例方法 `get_adaptive_weights`（那其实是模块级便捷函数），
       真正的实例方法叫 `calculate_adaptive_weights`。
    3. 返回值是 `AdaptiveWeights` 数据类而非 dict，`isinstance(x, dict)` 恒为
       False，导致权重覆盖逻辑形同虚设，全程使用等权因子。

本文件的职责就是防止这三类"静默失效"以任何形式复发——任何一条回归，测试都应该
挂红，而不是像当时一样悄悄降级成等权且没人发现。
"""
from __future__ import annotations

import pytest

from backend.services.factor_engine.base_factors import (
    FactorCategory, FactorValue, factor_engine,
)
from backend.services.factor_engine.factor_weighting import (
    DynamicFactorWeighting, AdaptiveWeights, MarketRegime,
)
from backend.services.factor_engine.factor_evaluation_pipeline import (
    FactorEvaluationPipeline,
)


def _fv(value: float, normalized: float = 0.0) -> FactorValue:
    return FactorValue(
        name="x", category=FactorCategory.MOMENTUM, value=value, normalized=normalized,
    )


class TestDynamicFactorWeightingContract:
    """回归#1/#2/#3：构造签名 / 方法名 / 返回类型契约。"""

    def test_constructor_requires_factor_engine_positional_arg(self):
        """回归#1：构造函数签名一旦变回"无需 factor_engine"或调用方漏传，
        这里必须先在测试里炸出来，而不是在生产里被 except 吞掉。"""
        with pytest.raises(TypeError):
            DynamicFactorWeighting()  # type: ignore[call-arg]

        # 传入 factor_engine 才能正常构造
        dfw = DynamicFactorWeighting(factor_engine)
        assert dfw.engine is factor_engine

    def test_calculate_adaptive_weights_method_exists(self):
        """回归#2：真正的方法名是 calculate_adaptive_weights，不是
        get_adaptive_weights（那是模块级函数，不是实例方法）。"""
        dfw = DynamicFactorWeighting(factor_engine)
        assert hasattr(dfw, "calculate_adaptive_weights")
        assert callable(dfw.calculate_adaptive_weights)

    def test_calculate_adaptive_weights_returns_adaptiveweights_not_dict(self):
        """回归#3：返回值是 AdaptiveWeights 数据类，不是 dict；调用方必须
        用 `.weights` 取出内部字典，不能对返回值本身 isinstance(..., dict)。"""
        dfw = DynamicFactorWeighting(factor_engine)
        factor_values = {
            "supertrend": _fv(1.0, 0.8),
            "momentum": _fv(0.5, 0.6),
            "atr": _fv(0.02, 0.1),
        }
        result = dfw.calculate_adaptive_weights(factor_values, market_data=None)

        assert isinstance(result, AdaptiveWeights)
        assert not isinstance(result, dict)
        assert isinstance(result.weights, dict)
        assert len(result.weights) > 0
        assert isinstance(result.regime, MarketRegime)


class TestRegimeWeightsAreRegimeSpecific:
    """防止 REGIME_WEIGHTS 被误改成"各 regime 权重完全相同"（等价于回到等权）。"""

    def test_breakout_and_noise_weight_different_top_factors(self):
        dfw = DynamicFactorWeighting(factor_engine)
        breakout_w = dfw.get_regime_weights(MarketRegime.BREAKOUT)
        noise_w = dfw.get_regime_weights(MarketRegime.NOISE)

        assert breakout_w and noise_w
        top_breakout = max(breakout_w, key=breakout_w.get)
        top_noise = max(noise_w, key=noise_w.get)
        # 不要求两个 regime 权重字典完全不相交，但至少 top1 因子应不同，
        # 否则 regime 区分度形同虚设。
        assert top_breakout != top_noise or breakout_w[top_breakout] != noise_w[top_noise]


class TestPipelineActuallyUsesRegimeWeighting:
    """整条链路的最终回归护栏：FactorEvaluationPipeline 是否真的把
    DynamicFactorWeighting 接上了、并且真的把它的输出乘进了最终权重。"""

    def test_pipeline_initializes_weighting_component(self):
        """回归#1 的端到端护栏：只要构造失败被吞掉，_weighting 就会是 None。"""
        pipeline = FactorEvaluationPipeline()
        assert pipeline._weighting is not None, (
            "DynamicFactorWeighting 初始化失败（可能是构造参数/导入路径又出现"
            "新的静默异常），regime 自适应权重已经失效！"
        )
        assert isinstance(pipeline._weighting, DynamicFactorWeighting)

    def test_pipeline_compute_weights_applies_nonuniform_regime_multiplier(self):
        """回归#2/#3 的端到端护栏：直接调用 _compute_weights，验证不同因子的
        最终权重不是清一色相等——如果 regime 权重没被真正乘进去，
        所有因子会退化成完全相同的等权（1.0 归一化后仍相等）。"""
        pipeline = FactorEvaluationPipeline()

        # 用真实 REGIME_WEIGHTS 里出现过的因子名，确保 regime 权重字典命中。
        factor_values = {
            "supertrend": _fv(1.0, 0.9),
            "sma_cross": _fv(0.5, 0.5),
            "momentum": _fv(0.3, 0.3),
            "atr": _fv(0.02, 0.1),
            "volume_zscore": _fv(1.5, 0.4),
            "taker_ratio": _fv(0.6, 0.2),
        }
        weights = pipeline._compute_weights(factor_values, market_data={"volatility": 0.8, "trend_strength": 0.9})

        assert weights, "权重字典为空"
        distinct_values = {round(v, 6) for v in weights.values()}
        assert len(distinct_values) > 1, (
            "所有因子权重完全相等 → regime 自适应权重可能又静默失效，退化成等权！"
        )
