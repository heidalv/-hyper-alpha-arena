"""
AlphaEnsemble 异构集成（P4.7，方案 §P4.7 / §1.3）。

目标：异构集成 River 在线线性（快速适应）+ LightGBM（方向）+ SAC（size）+ RecurrentPPO（POMDP）。
按 regime 加权融合。集成 Sharpe 优于任一单模型。

当前：子模型接口 + regime 加权融合框架（具体子模型接入在训练管线就绪后）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from backend.services.alpha.regime_refined import Regime
from backend.services.contracts.types import Direction, FactorVector


class SubModel(Protocol):
    """集成子模型接口。"""
    name: str

    def predict_direction(self, factor_vector: FactorVector) -> tuple[Direction, float]:
        """返回 (方向, 置信度)。"""
        ...


@dataclass
class EnsemblePrediction:
    """集成预测结果。"""
    direction: Direction
    confidence: float
    magnitude: float
    contributing_models: dict[str, tuple[Direction, float]] = field(default_factory=dict)
    regime: str = ""


@dataclass
class RegimeWeights:
    """不同 regime 下各子模型的权重。"""
    # 默认权重（平稳/通用）
    default: dict[str, float] = field(default_factory=lambda: {
        "online_linear": 0.25,
        "lightgbm": 0.35,
        "sac": 0.20,
        "recurrent_ppo": 0.20,
    })
    # regime 专属权重覆盖
    overrides: dict[str, dict[str, float]] = field(default_factory=lambda: {
        Regime.TREND_HIGH_VOL.value: {"lightgbm": 0.45, "recurrent_ppo": 0.30, "online_linear": 0.15, "sac": 0.10},
        Regime.RANGE.value: {"online_linear": 0.40, "lightgbm": 0.30, "sac": 0.20, "recurrent_ppo": 0.10},
        Regime.SQUEEZE.value: {"recurrent_ppo": 0.40, "sac": 0.30, "lightgbm": 0.20, "online_linear": 0.10},
    })

    def for_regime(self, regime: str) -> dict[str, float]:
        return self.overrides.get(regime, self.default)


class AlphaEnsemble:
    """
    异构集成（regime 加权融合）。

    每个子模型独立预测方向+置信度；按当前 regime 权重加权融合。
    """

    def __init__(self, regime_weights: RegimeWeights | None = None):
        self._models: dict[str, SubModel] = {}
        self.weights = regime_weights or RegimeWeights()

    def register(self, model: SubModel) -> None:
        self._models[model.name] = model

    def predict(self, factor_vector: FactorVector,
                regime: str = "") -> EnsemblePrediction:
        """集成预测。"""
        if not self._models:
            return EnsemblePrediction(Direction.FLAT, 0.0, 0.0, regime=regime)

        weights = self.weights.for_regime(regime)
        contributions: dict[str, tuple[Direction, float]] = {}
        # 加权投票（方向编码 LONG=+1, SHORT=-1, FLAT=0）
        vote_sum = 0.0
        conf_sum = 0.0
        total_weight = 0.0
        for name, model in self._models.items():
            w = weights.get(name, 0.0)
            if w <= 0:
                continue
            direction, confidence = model.predict_direction(factor_vector)
            contributions[name] = (direction, confidence)
            dir_code = 1.0 if direction == Direction.LONG else (-1.0 if direction == Direction.SHORT else 0.0)
            vote_sum += w * dir_code * confidence
            conf_sum += w * confidence
            total_weight += w

        if total_weight < 1e-9:
            return EnsemblePrediction(Direction.FLAT, 0.0, 0.0, contributions, regime)

        # 融合方向
        norm_vote = vote_sum / total_weight
        if norm_vote > 0.1:
            direction = Direction.LONG
        elif norm_vote < -0.1:
            direction = Direction.SHORT
        else:
            direction = Direction.FLAT

        confidence = abs(norm_vote)
        magnitude = conf_sum / total_weight * 0.02  # 简化幅度估计

        return EnsemblePrediction(
            direction=direction, confidence=confidence,
            magnitude=magnitude, contributing_models=contributions,
            regime=regime,
        )
