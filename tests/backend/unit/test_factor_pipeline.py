"""FactorEvaluationPipeline 单元测试"""
import pytest


pytestmark = pytest.mark.unit


def test_pipeline_compute_weights_equal_when_no_components():
    """无 weighting/decay 组件时 → 等权。"""
    from backend.services.factor_engine.factor_evaluation_pipeline import FactorEvaluationPipeline

    p = FactorEvaluationPipeline()
    p._weighting = None
    p._decay_monitor = None
    weights = p._compute_weights({"rsi": None, "macd": None, "atr": None}, None)
    # 归一化后应接近等权
    vals = list(weights.values())
    assert max(vals) - min(vals) < 0.01  # 等权


def test_pipeline_decay_penalty_reduces_weight():
    """衰变惩罚降低因子权重。"""
    from backend.services.factor_engine.factor_evaluation_pipeline import FactorEvaluationPipeline
    from unittest.mock import MagicMock

    p = FactorEvaluationPipeline()
    p._weighting = None
    # mock decay monitor: rsi 的惩罚 = 0.3（衰减严重）
    mock_decay = MagicMock()
    def penalty(name):
        return 0.3 if name == "rsi" else 1.0
    mock_decay.get_factor_weight_penalty = penalty
    p._decay_monitor = mock_decay

    weights = p._compute_weights({"rsi": None, "macd": None}, None)
    # rsi 权重应低于 macd（被衰变惩罚压低）
    assert weights["rsi"] < weights["macd"]


def test_pipeline_normalizes_weights():
    """权重归一化后总和合理。"""
    from backend.services.factor_engine.factor_evaluation_pipeline import FactorEvaluationPipeline

    p = FactorEvaluationPipeline()
    p._weighting = None
    p._decay_monitor = None
    weights = p._compute_weights({"a": None, "b": None, "c": None, "d": None}, None)
    # 归一化后每个 ≈ 1.0（n 个因子 × 平均 1.0）
    for v in weights.values():
        assert 0.5 < v < 2.0  # 合理范围


def test_pipeline_empty_input_returns_none():
    """空输入返回 None。"""
    from backend.services.factor_engine.factor_evaluation_pipeline import FactorEvaluationPipeline

    p = FactorEvaluationPipeline()
    result = p.compute_weighted_signals({}, None)
    assert result is None


def test_pipeline_singleton():
    """全局单例。"""
    from backend.services.factor_engine.factor_evaluation_pipeline import factor_pipeline, FactorEvaluationPipeline
    assert isinstance(factor_pipeline, FactorEvaluationPipeline)
