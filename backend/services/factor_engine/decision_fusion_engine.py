"""
DecisionFusionEngine — 决策融合引擎

将 FactorSignalGenerator 产生的合成信号、
FactorQualityEvaluator 的质量报告、以及编排器状态
融合为最终交易决策建议。
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .base_factors import FactorCategory, FactorValue
from .factor_signal_generator import FactorSignal, FactorSignalGenerator
from .factor_quality_evaluator import FactorQualityEvaluator

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════
#  数据结构
# ════════════════════════════════════════════════════════════

@dataclass
class FusionDecision:
    """融合决策结果"""
    action: str                          # "buy" / "sell" / "hold" / "close"
    confidence: float                    # [0, 1]
    signal_direction: float              # 来自 CompositeSignal
    signal_strength: float               # 来自 CompositeSignal
    data_quality: str                    # 来自 QualityReport
    regime: str
    reasoning: str
    factor_details: Dict[str, FactorSignal] = field(default_factory=dict)


# ════════════════════════════════════════════════════════════
#  DecisionFusionEngine
# ════════════════════════════════════════════════════════════

class DecisionFusionEngine:
    """多源信号融合引擎"""

    # 方向阈值：超过此值视为明确方向
    DIRECTION_THRESHOLD: float = 0.3
    # 强度阈值：超过此值才产生非 hold 决策
    STRENGTH_THRESHOLD: float = 0.4

    def __init__(self):
        self._signal_gen = FactorSignalGenerator()
        self._quality_eval = FactorQualityEvaluator()

    def fuse(
        self,
        factor_values: Dict[str, FactorValue],
        weights: Optional[Dict[str, float]] = None,
        regime: str = "unknown",
        orchestrator_action: Optional[str] = None,
        position_side: Optional[str] = None,
        expected_factors: Optional[List[str]] = None,
    ) -> FusionDecision:
        """
        融合因子信号、数据质量、编排器状态为最终决策。

        Args:
            factor_values: 因子名 -> FactorValue
            weights: 因子名 -> 权重
            regime: 当前市场状态
            orchestrator_action: 编排器动作（"frozen"/"hold"/"buy"/"sell"等）
            position_side: 当前仓位方向（"long"/"short"/None）
            expected_factors: 期望的因子列表（用于质量评估）

        Returns:
            FusionDecision
        """
        # 1. 编排器 frozen 硬约束
        if orchestrator_action == "frozen":
            return FusionDecision(
                action="hold",
                confidence=0.0,
                signal_direction=0.0,
                signal_strength=0.0,
                data_quality="unknown",
                regime=regime,
                reasoning="orchestrator frozen, skip",
            )

        # 2. 信号生成
        composite = self._signal_gen.generate_signals(
            factor_values, weights=weights, regime=regime,
        )

        # 3. 质量评估
        if expected_factors is None:
            expected_factors = list(factor_values.keys())
        quality_report = self._quality_eval.evaluate(
            factor_values, expected_factors,
        )

        # 4. 方向判定
        action = self._determine_action(
            composite.direction, composite.strength,
            position_side,
        )

        # 5. 置信度计算
        confidence = self._compute_confidence(
            composite.confidence, composite.strength,
            quality_report.overall_quality,
        )

        # 6. 推理生成
        reasoning = self._build_reasoning(
            action, composite, quality_report,
        )

        return FusionDecision(
            action=action,
            confidence=confidence,
            signal_direction=composite.direction,
            signal_strength=composite.strength,
            data_quality=quality_report.overall_quality,
            regime=regime,
            reasoning=reasoning,
            factor_details=composite.signals,
        )

    def _determine_action(
        self,
        direction: float,
        strength: float,
        position_side: Optional[str],
    ) -> str:
        """根据方向和强度判定动作"""
        if abs(direction) < self.DIRECTION_THRESHOLD:
            return "hold"
        if strength < self.STRENGTH_THRESHOLD:
            return "hold"

        if direction > 0:
            # 看多信号
            if position_side == "short":
                return "close"
            return "buy"
        else:
            # 看空信号
            if position_side == "long":
                return "close"
            return "sell"

    def _compute_confidence(
        self,
        signal_confidence: float,
        signal_strength: float,
        data_quality: str,
    ) -> float:
        """计算最终置信度"""
        confidence = signal_confidence * signal_strength

        if data_quality == "low":
            confidence *= 0.5
        elif data_quality == "medium":
            confidence *= 0.8

        return max(0.0, min(1.0, confidence))

    def _build_reasoning(
        self,
        action: str,
        composite,
        quality_report,
    ) -> str:
        """生成人类可读的决策推理"""
        parts = [f"action={action}"]
        parts.append(f"dir={composite.direction:+.2f}")
        parts.append(f"str={composite.strength:.2f}")
        parts.append(f"conf={composite.confidence:.2f}")
        parts.append(f"quality={quality_report.overall_quality}")

        # 列出主要贡献因子（按 |direction| 排序取 top-3）
        sorted_sigs = sorted(
            composite.signals.values(),
            key=lambda s: abs(s.direction),
            reverse=True,
        )
        top = sorted_sigs[:3]
        if top:
            factors_str = ", ".join(
                f"{s.factor_id}({s.direction:+.2f})" for s in top
            )
            parts.append(f"top=[{factors_str}]")

        if quality_report.missing_factors:
            parts.append(f"missing={len(quality_report.missing_factors)}")

        return " | ".join(parts)
