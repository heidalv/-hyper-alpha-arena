"""阶段 2（块 C 信号融合方向/权重修复）回归测试（2026-08-14）。

锁定：
- P1-E1 funding 方向映射（单位约定 + 不再恒满格看空）
- P1-E2 generate_signals 幽灵因子过滤（is_directional/has_data）
- P1-E3 regime 权重→乘数转换
- P2-8 编排器 hold/frozen 否决
- P2-7 质量评估器 completeness / agreement
"""
from __future__ import annotations

import logging

import pytest

from backend.services.factor_engine.base_factors import FactorCategory, FactorValue


def _fv(name, value, *, normalized=0.0, has_data=True, is_directional=True,
        category=FactorCategory.MOMENTUM) -> FactorValue:
    return FactorValue(
        name=name, category=category, value=float(value),
        normalized=float(normalized), has_data=has_data,
        is_directional=is_directional,
    )


# ═══════════════════════════════════════════════════════════
# P1-E1：funding 方向映射
# ═══════════════════════════════════════════════════════════

def test_funding_direction_no_longer_saturated_short():
    from backend.services.factor_engine.factor_signal_generator import _funding_rate_direction

    # 0.01% 的正常费率：修复前 = -1.0（满格看空）；修复后应为温和看空（-0.2）
    d = _funding_rate_direction(0.01)
    assert -1.0 < d < 0.0
    assert abs(d - (-0.2)) < 1e-9


def test_funding_direction_saturation_and_zero():
    from backend.services.factor_engine.factor_signal_generator import _funding_rate_direction

    assert _funding_rate_direction(0.05) == pytest.approx(-1.0)   # 饱和点
    assert _funding_rate_direction(0.0) == 0.0                     # 零费率中性
    assert _funding_rate_direction(-0.03) == pytest.approx(0.6)    # 负费率→看多
    assert _funding_rate_direction(0.5) == pytest.approx(-1.0)     # 上限截断


# ═══════════════════════════════════════════════════════════
# P1-E2：幽灵因子过滤
# ═══════════════════════════════════════════════════════════

def test_generate_signals_filters_nondirectional_and_nodata():
    from backend.services.factor_engine.factor_signal_generator import FactorSignalGenerator

    gen = FactorSignalGenerator()
    values = {
        "ok_factor": _fv("ok_factor", 0.5, normalized=1.0),
        "price_abs": _fv("price_abs", 50000.0, normalized=1.0, is_directional=False,
                         category=FactorCategory.VOLUME),
        "nodata": _fv("nodata", 0.5, normalized=1.0, has_data=False),
    }
    composite = gen.generate_signals(values, regime="test")
    assert "ok_factor" in composite.signals
    assert "price_abs" not in composite.signals      # 无方向因子必须被过滤
    assert "nodata" not in composite.signals          # 无数据因子必须被过滤


# ═══════════════════════════════════════════════════════════
# P1-E3：regime 权重乘数
# ═══════════════════════════════════════════════════════════

def test_regime_weights_to_multipliers():
    from backend.services.factor_engine.factor_weighting import regime_weights_to_multipliers

    m = regime_weights_to_multipliers({"a": 0.9, "b": 0.1})
    assert m == {"a": pytest.approx(1.8), "b": pytest.approx(0.2)}   # 均值 0.5 → ×2
    assert regime_weights_to_multipliers({}) == {}
    assert regime_weights_to_multipliers(None) == {}
    assert regime_weights_to_multipliers({"a": 0.0}) == {}           # 无正值


# ═══════════════════════════════════════════════════════════
# P2-8：编排器否决
# ═══════════════════════════════════════════════════════════

def test_fusion_orchestrator_hold_blocks():
    from backend.services.factor_engine.decision_fusion_engine import DecisionFusionEngine

    engine = DecisionFusionEngine()
    values = {"rsi": _fv("rsi", 20.0, normalized=-1.0)}   # 强看多信号
    for action in ("hold", "frozen"):
        decision = engine.fuse(values, orchestrator_action=action)
        assert decision.action == "hold"
        assert decision.confidence == 0.0


# ═══════════════════════════════════════════════════════════
# P2-7：质量评估器
# ═══════════════════════════════════════════════════════════

def test_quality_completeness_hit_ratio():
    from backend.services.factor_engine.factor_quality_evaluator import FactorQualityEvaluator

    evaluator = FactorQualityEvaluator()
    # 期望 A/B/C，实际只有 A（+X 多余因子）：修复前 completeness=2/3，修复后 1/3
    report = evaluator.evaluate(
        {"A": _fv("A", 1.0), "X": _fv("X", 1.0)},
        expected_factors=["A", "B", "C"],
    )
    assert report.data_completeness == pytest.approx(1.0 / 3.0)
    assert set(report.missing_factors) == {"B", "C"}

    # 全命中 → 1.0
    report2 = evaluator.evaluate(
        {"A": _fv("A", 1.0), "B": _fv("B", 1.0), "C": _fv("C", 1.0)},
        expected_factors=["A", "B", "C"],
    )
    assert report2.data_completeness == pytest.approx(1.0)


def test_quality_agreement_uses_mapped_direction():
    from backend.services.factor_engine.factor_quality_evaluator import FactorQualityEvaluator

    evaluator = FactorQualityEvaluator()
    # rsi=20（超卖→看多，映射 +0.6）与 funding_rate=0.02（0.02% 正费率→看空，
    # 映射 -0.4）：真实方向相反 → agreement=0。
    # 旧实现取原始值符号（20>0、0.02>0）会得出 agreement=1.0 的错误结论。
    report = evaluator.evaluate(
        {
            "rsi": _fv("rsi", 20.0),
            "funding_rate": _fv("funding_rate", 0.02, category=FactorCategory.FUNDING),
        },
        expected_factors=["rsi", "funding_rate"],
    )
    assert report.signal_agreement == pytest.approx(0.0)

    # rsi=80（超买→看空）与正 funding（看空）同为看空 → agreement=1.0
    report2 = evaluator.evaluate(
        {
            "rsi": _fv("rsi", 80.0),
            "funding_rate": _fv("funding_rate", 0.02, category=FactorCategory.FUNDING),
        },
        expected_factors=["rsi", "funding_rate"],
    )
    assert report2.signal_agreement == pytest.approx(1.0)
