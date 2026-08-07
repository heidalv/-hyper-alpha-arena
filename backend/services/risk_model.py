"""
Unified Risk Model Interface — 统一风控模型基类 (F2-4)

将分散在 risk_control_service, deterministic_risk_gate, master_close_guard
等文件的 7 种断路器抽象为统一的 RiskModel 接口。

设计目标：
- 每个断路器实现为一个 BaseRiskModel 子类
- CompositeRiskAssessor 组合多个模型并聚合评估结果
- 渐进迁移：先从 DeterministicRiskGate 开始
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class RiskAssessment:
    """统一的风险评估结果

    Attributes:
        risk_score: 0-100，越高越危险
        block_reasons: 拦截原因列表（非空 = 拒绝交易）
        warnings: 警告列表（不拦截但应记录）
        position_adjustment: 仓位调整因子 (1.0=不变, 0.5=减半, 0=禁止)
    """
    risk_score: float = 0.0
    block_reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    position_adjustment: float = 1.0
    model_name: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_blocked(self) -> bool:
        return len(self.block_reasons) > 0

    @property
    def has_warnings(self) -> bool:
        return len(self.warnings) > 0


class BaseRiskModel(ABC):
    """统一风控模型基类

    所有断路器都应继承此类，实现 assess() 方法。
    """

    def __init__(self, name: str = ""):
        self.name = name or self.__class__.__name__

    @abstractmethod
    def assess(self, context: Dict[str, Any]) -> RiskAssessment:
        """评估当前上下文的风险

        Args:
            context: 包含评估所需的所有上下文信息，如:
                - account_equity
                - current_positions
                - new_order
                - market_data
                - symbol
                - account_id
                - daily_pnl
                - risk_score (from external source)

        Returns:
            RiskAssessment: 统一的风险评估结果
        """
        ...

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r})"


class CompositeRiskAssessor:
    """组合多个 RiskModel，聚合评估结果

    聚合规则：
    - risk_score: 取最大值（最危险模型的评分）
    - block_reasons: 合并所有模型的拦截原因
    - warnings: 合并所有模型的警告
    - position_adjustment: 取最小值（最保守的仓位调整）
    """

    def __init__(self, models: Optional[List[BaseRiskModel]] = None):
        self.models: List[BaseRiskModel] = models or []

    def add_model(self, model: BaseRiskModel) -> None:
        self.models.append(model)

    def remove_model(self, name: str) -> None:
        self.models = [m for m in self.models if m.name != name]

    def assess(self, context: Dict[str, Any]) -> RiskAssessment:
        """顺序评估所有模型，聚合结果"""
        if not self.models:
            return RiskAssessment(
                risk_score=0.0,
                model_name="composite(empty)",
            )

        assessments: List[RiskAssessment] = []
        for model in self.models:
            try:
                assessment = model.assess(context)
                assessments.append(assessment)
            except Exception as e:
                logger.error(
                    f"[RiskModel] 模型 {model.name} 评估异常: {e}",
                    exc_info=True,
                )
                # 异常时保守处理：标记为高风险
                assessments.append(RiskAssessment(
                    risk_score=80.0,
                    block_reasons=[f"{model.name} 评估异常: {str(e)[:80]}"],
                    model_name=model.name,
                ))

        total_score = max((a.risk_score for a in assessments), default=0.0)
        all_blocks = [r for a in assessments for r in a.block_reasons]
        all_warnings = [r for a in assessments for r in a.warnings]
        min_adjustment = min(
            (a.position_adjustment for a in assessments), default=1.0
        )

        return RiskAssessment(
            risk_score=min(total_score, 100.0),
            block_reasons=all_blocks,
            warnings=all_warnings,
            position_adjustment=min_adjustment,
            model_name="composite",
            metadata={
                "model_count": len(self.models),
                "individual_scores": {
                    a.model_name: a.risk_score for a in assessments
                },
                "blocked_by": [
                    a.model_name for a in assessments if a.is_blocked
                ],
            },
        )

    def assess_quick(self, context: Dict[str, Any]) -> bool:
        """快速检查：有拦截立即返回 False"""
        for model in self.models:
            try:
                assessment = model.assess(context)
                if assessment.is_blocked:
                    return False
            except Exception:
                return False
        return True


# 模块级单例
composite_assessor = CompositeRiskAssessor()
