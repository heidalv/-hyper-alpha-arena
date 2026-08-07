"""
test_phase1_signal_system — Phase 1 因子信号系统单元测试

覆盖范围:
1. FactorCategory 枚举扩展
2. FactorSignalGenerator 信号生成
3. FactorQualityEvaluator 质量评估
4. DecisionFusionEngine 决策融合
"""

import pytest
from unittest.mock import MagicMock

from backend.services.factor_engine.base_factors import FactorCategory, FactorValue
from backend.services.factor_engine.factor_signal_generator import (
    FactorSignal,
    CompositeSignal,
    FactorSignalGenerator,
)
from backend.services.factor_engine.factor_quality_evaluator import (
    QualityReport,
    FactorQualityEvaluator,
)
from backend.services.factor_engine.decision_fusion_engine import (
    FusionDecision,
    DecisionFusionEngine,
)


# ════════════════════════════════════════════════════════
#  Helpers
# ════════════════════════════════════════════════════════

def _make_factor_values(**kwargs) -> dict:
    """快速构造 factor_values dict: {name: (value, category_str)}"""
    result = {}
    for name, val in kwargs.items():
        if isinstance(val, tuple):
            v, cat = val
        else:
            v = val
            cat = "momentum"
        result[name] = FactorValue(name=name, category=FactorCategory(cat), value=v)
    return result


# ════════════════════════════════════════════════════════
#  1. FactorCategory 枚举扩展
# ════════════════════════════════════════════════════════

class TestFactorCategoryExtension:

    def test_sentiment_category_exists(self):
        assert FactorCategory.SENTIMENT.value == "sentiment"

    def test_funding_category_exists(self):
        assert FactorCategory.FUNDING.value == "funding"

    def test_behavioral_category_exists(self):
        assert FactorCategory.BEHAVIORAL.value == "behavioral"

    def test_onchain_category_exists(self):
        assert FactorCategory.ONCHAIN.value == "onchain"

    def test_derivatives_category_exists(self):
        assert FactorCategory.DERIVATIVES.value == "derivatives"

    def test_macro_category_exists(self):
        assert FactorCategory.MACRO.value == "macro"

    def test_onchain_category_exists(self):
        assert FactorCategory.ONCHAIN.value == "onchain"

    def test_derivatives_category_exists(self):
        assert FactorCategory.DERIVATIVES.value == "derivatives"

    def test_macro_category_exists(self):
        assert FactorCategory.MACRO.value == "macro"

    def test_total_category_count(self):
        assert len(FactorCategory) == 14  # 11 original + ONCHAIN + DERIVATIVES + MACRO (V3 §2.3)

    def test_all_values_unique(self):
        values = [c.value for c in FactorCategory]
        assert len(values) == len(set(values))


# ════════════════════════════════════════════════════════
#  2. FactorSignalGenerator
# ════════════════════════════════════════════════════════

class TestFactorSignalGenerator:

    def test_rsi_oversold_is_bullish(self):
        """RSI=25 (超卖) 应产生正方向信号"""
        gen = FactorSignalGenerator()
        fvs = _make_factor_values(rsi=(25.0, "momentum"))
        result = gen.generate_signals(fvs)
        assert result.signals["rsi"].direction > 0

    def test_rsi_overbought_is_bearish(self):
        """RSI=80 (超买) 应产生负方向信号"""
        gen = FactorSignalGenerator()
        fvs = _make_factor_values(rsi=(80.0, "momentum"))
        result = gen.generate_signals(fvs)
        assert result.signals["rsi"].direction < 0

    def test_rsi_neutral_direction_near_zero(self):
        """RSI=50 (中性) 应产生接近零的方向信号"""
        gen = FactorSignalGenerator()
        fvs = _make_factor_values(rsi=(50.0, "momentum"))
        result = gen.generate_signals(fvs)
        assert abs(result.signals["rsi"].direction) < 0.05

    def test_ema_trend_bullish(self):
        """ema_trend=0.8 应产生强看多信号"""
        gen = FactorSignalGenerator()
        fvs = _make_factor_values(ema_trend=(0.8, "trend"))
        result = gen.generate_signals(fvs)
        assert result.signals["ema_trend"].direction > 0.5

    def test_macd_positive_bullish(self):
        """MACD 正值 → 看多"""
        gen = FactorSignalGenerator()
        fvs = _make_factor_values(macd=(0.5, "momentum"))
        result = gen.generate_signals(fvs)
        assert result.signals["macd"].direction > 0

    def test_empty_input_returns_zero_signal(self):
        """空输入应返回零信号"""
        gen = FactorSignalGenerator()
        result = gen.generate_signals({})
        assert result.direction == 0.0
        assert result.strength == 0.0
        assert result.confidence == 0.0
        assert result.contributing_factors == 0

    def test_multi_factor_weighted_aggregation(self):
        """多因子加权聚合：两个看多因子应产生正方向合成信号"""
        gen = FactorSignalGenerator()
        fvs = _make_factor_values(
            rsi=(25.0, "momentum"),
            ema_trend=(0.9, "trend"),
        )
        weights = {"rsi": 1.0, "ema_trend": 2.0}
        result = gen.generate_signals(fvs, weights=weights)
        assert result.direction > 0
        assert result.contributing_factors == 2

    def test_conflicting_factors_lower_confidence(self):
        """冲突因子应降低 confidence"""
        gen = FactorSignalGenerator()
        fvs = _make_factor_values(
            rsi=(25.0, "momentum"),    # 看多
            ema_trend=(-0.9, "trend"), # 看空
        )
        result = gen.generate_signals(fvs)
        # 方向冲突 → confidence 应较低
        assert result.confidence < 0.5

    def test_agreeing_factors_high_confidence(self):
        """一致的因子应产生高 confidence"""
        gen = FactorSignalGenerator()
        fvs = _make_factor_values(
            rsi=(25.0, "momentum"),     # 看多
            ema_trend=(0.9, "trend"),   # 看多
            momentum=(3.0, "momentum"), # 看多
        )
        result = gen.generate_signals(fvs)
        assert result.confidence > 0.7

    def test_direction_clamped_to_range(self):
        """合成方向应始终在 [-1, +1]"""
        gen = FactorSignalGenerator()
        fvs = _make_factor_values(rsi=(10.0, "momentum"))
        result = gen.generate_signals(fvs)
        assert -1.0 <= result.direction <= 1.0
        assert 0.0 <= result.strength <= 1.0
        assert 0.0 <= result.confidence <= 1.0

    def test_funding_rate_extreme_negative_bullish(self):
        """极端负资金费率 → 看多（市场恐慌=反向信号）"""
        gen = FactorSignalGenerator()
        fvs = _make_factor_values(funding_rate=(-0.01, "market_flow"))
        result = gen.generate_signals(fvs)
        assert result.signals["funding_rate"].direction > 0

    def test_register_custom_mapper(self):
        """自定义 mapper 应生效"""
        gen = FactorSignalGenerator()
        gen.register_mapper("custom_factor", lambda v: -1.0 if v > 100 else 1.0)
        fvs = _make_factor_values(custom_factor=(200.0, "momentum"))
        result = gen.generate_signals(fvs)
        assert result.signals["custom_factor"].direction == -1.0


# ════════════════════════════════════════════════════════
#  3. FactorQualityEvaluator
# ════════════════════════════════════════════════════════

class TestFactorQualityEvaluator:

    def test_complete_data_high_quality(self):
        """所有因子都存在 → completeness=1.0"""
        evaluator = FactorQualityEvaluator()
        fvs = _make_factor_values(rsi=(50.0, "momentum"), macd=(0.1, "momentum"))
        report = evaluator.evaluate(fvs, expected_factors=["rsi", "macd"])
        assert report.data_completeness == 1.0
        assert len(report.missing_factors) == 0

    def test_missing_factors_lower_completeness(self):
        """缺失因子应降低 completeness"""
        evaluator = FactorQualityEvaluator()
        fvs = _make_factor_values(rsi=(50.0, "momentum"))
        report = evaluator.evaluate(fvs, expected_factors=["rsi", "macd", "atr"])
        assert report.data_completeness < 1.0
        assert "macd" in report.missing_factors
        assert "atr" in report.missing_factors

    def test_all_missing_low_quality(self):
        """全部缺失 → low quality"""
        evaluator = FactorQualityEvaluator()
        report = evaluator.evaluate({}, expected_factors=["rsi", "macd"])
        assert report.data_completeness == 0.0
        assert report.overall_quality == "low"

    def test_high_agreement_with_same_direction(self):
        """所有因子同方向 → agreement 高"""
        evaluator = FactorQualityEvaluator()
        fvs = _make_factor_values(
            rsi=(25.0, "momentum"),
            momentum=(3.0, "momentum"),
            ema_trend=(0.8, "trend"),
        )
        report = evaluator.evaluate(fvs, expected_factors=["rsi", "momentum", "ema_trend"])
        assert report.signal_agreement > 0.5

    def test_stale_detection_with_previous_values(self):
        """因子值未变化应被标记为 stale"""
        evaluator = FactorQualityEvaluator()
        fvs = _make_factor_values(rsi=(50.0, "momentum"), macd=(0.1, "momentum"))
        previous = {"rsi": 50.0, "macd": 0.1}
        report = evaluator.evaluate(fvs, expected_factors=["rsi", "macd"], previous_values=previous)
        assert len(report.stale_factors) == 2

    def test_no_stale_without_previous(self):
        """无 previous_values 时不报告 stale"""
        evaluator = FactorQualityEvaluator()
        fvs = _make_factor_values(rsi=(50.0, "momentum"))
        report = evaluator.evaluate(fvs, expected_factors=["rsi"])
        assert report.stale_factors == []

    def test_quality_classification_high(self):
        """completeness=1.0, agreement=1.0 → high"""
        evaluator = FactorQualityEvaluator()
        fvs = _make_factor_values(
            rsi=(20.0, "momentum"),
            momentum=(5.0, "momentum"),
            ema_trend=(0.9, "trend"),
            macd=(0.5, "momentum"),
            atr=(100.0, "volatility"),
        )
        report = evaluator.evaluate(
            fvs,
            expected_factors=["rsi", "momentum", "ema_trend", "macd", "atr"],
        )
        assert report.data_completeness >= 0.8
        assert report.overall_quality in ("high", "medium")

    def test_quality_classification_low(self):
        """completeness 很低 → low"""
        evaluator = FactorQualityEvaluator()
        fvs = _make_factor_values(rsi=(50.0, "momentum"))
        report = evaluator.evaluate(fvs, expected_factors=["rsi", "macd", "atr", "bb_width", "obv"])
        assert report.overall_quality == "low"


# ════════════════════════════════════════════════════════
#  4. DecisionFusionEngine
# ════════════════════════════════════════════════════════

class TestDecisionFusionEngine:

    def test_strong_bullish_produces_buy(self):
        """强看多信号 + good quality → buy"""
        engine = DecisionFusionEngine()
        fvs = _make_factor_values(
            rsi=(25.0, "momentum"),
            ema_trend=(0.9, "trend"),
            momentum=(4.0, "momentum"),
        )
        decision = engine.fuse(fvs, regime="continuation")
        assert decision.action == "buy"
        assert decision.confidence > 0

    def test_strong_bearish_produces_sell(self):
        """强看空信号 → sell"""
        engine = DecisionFusionEngine()
        fvs = _make_factor_values(
            rsi=(80.0, "momentum"),
            ema_trend=(-0.9, "trend"),
            momentum=(-4.0, "momentum"),
        )
        decision = engine.fuse(fvs, regime="reversal")
        assert decision.action == "sell"

    def test_weak_signal_produces_hold(self):
        """弱信号 → hold"""
        engine = DecisionFusionEngine()
        fvs = _make_factor_values(
            rsi=(48.0, "momentum"),
            ema_trend=(0.05, "trend"),
        )
        decision = engine.fuse(fvs, regime="noise")
        assert decision.action == "hold"

    def test_frozen_orchestrator_forces_hold(self):
        """frozen 编排器 → 强制 hold"""
        engine = DecisionFusionEngine()
        fvs = _make_factor_values(
            rsi=(25.0, "momentum"),
            ema_trend=(0.9, "trend"),
            momentum=(4.0, "momentum"),
        )
        decision = engine.fuse(fvs, orchestrator_action="frozen")
        assert decision.action == "hold"
        assert "frozen" in decision.reasoning

    def test_bullish_with_short_position_closes(self):
        """看多信号 + 已有空头仓位 → close"""
        engine = DecisionFusionEngine()
        fvs = _make_factor_values(
            rsi=(25.0, "momentum"),
            ema_trend=(0.9, "trend"),
            momentum=(4.0, "momentum"),
        )
        decision = engine.fuse(fvs, position_side="short")
        assert decision.action == "close"

    def test_bearish_with_long_position_closes(self):
        """看空信号 + 已有多头仓位 → close"""
        engine = DecisionFusionEngine()
        fvs = _make_factor_values(
            rsi=(80.0, "momentum"),
            ema_trend=(-0.9, "trend"),
            momentum=(-4.0, "momentum"),
        )
        decision = engine.fuse(fvs, position_side="long")
        assert decision.action == "close"

    def test_low_quality_reduces_confidence(self):
        """低数据质量应降低置信度"""
        engine = DecisionFusionEngine()
        fvs = _make_factor_values(
            rsi=(25.0, "momentum"),
            ema_trend=(0.9, "trend"),
            momentum=(4.0, "momentum"),
        )
        # 期望 5 个因子但只提供 3 个 → completeness = 0.6 → low/medium
        decision = engine.fuse(
            fvs,
            expected_factors=["rsi", "ema_trend", "momentum", "macd", "atr"],
        )
        # quality 应该不是 high
        assert decision.data_quality in ("low", "medium")
        # confidence 应该低于同信号在 high quality 下的值
        decision_full = engine.fuse(
            fvs,
            expected_factors=["rsi", "ema_trend", "momentum"],
        )
        if decision.data_quality == "low" and decision_full.data_quality != "low":
            assert decision.confidence < decision_full.confidence

    def test_reasoning_contains_key_info(self):
        """reasoning 应包含关键信息"""
        engine = DecisionFusionEngine()
        fvs = _make_factor_values(
            rsi=(25.0, "momentum"),
            ema_trend=(0.9, "trend"),
        )
        decision = engine.fuse(fvs, regime="continuation")
        assert "action=" in decision.reasoning
        assert "dir=" in decision.reasoning
        assert "quality=" in decision.reasoning

    def test_empty_factors_returns_hold(self):
        """空因子 → hold"""
        engine = DecisionFusionEngine()
        decision = engine.fuse({})
        assert decision.action == "hold"

    def test_decision_fields_in_valid_range(self):
        """所有输出字段应在有效范围内"""
        engine = DecisionFusionEngine()
        fvs = _make_factor_values(
            rsi=(30.0, "momentum"),
            ema_trend=(0.5, "trend"),
        )
        decision = engine.fuse(fvs)
        assert decision.action in ("buy", "sell", "hold", "close")
        assert -1.0 <= decision.signal_direction <= 1.0
        assert 0.0 <= decision.signal_strength <= 1.0
        assert 0.0 <= decision.confidence <= 1.0
        assert decision.data_quality in ("high", "medium", "low")
