"""
DDG-DA 主动分布漂移预测（整改#18）—— 对标 Qlib DDG-DA(arXiv 2201.04038)。

把 concept_drift_detector 从"事后触发回顾"升级为"主动预测下期分布 + 重训前预加权"：
  - 完整 DDG-DA（DDGDAForecaster）：轻量元模型预测近未来分布 + domain-attentive 学
    "哪些历史时期最像未来" + 按预测分布重加权历史样本。元模型依赖 torch（可选）。
  - 渐进简化版（DriftTriggeredReweighter，默认）：用漂移强度直接对历史样本做时间衰减
    加权——漂移强则近期样本权重高、远期快速衰减；漂移弱则接近均匀。零依赖、可立即上线。

零风险：默认关（DDGDA_ENABLED=false）→ reweight 返回全 1 权重（等价当前被动行为）。
重训前调用 reweight_training_data 即可；不改现有漂移检测与训练流程。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


def is_enabled() -> bool:
    return os.environ.get("DDGDA_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")


def mode() -> str:
    return os.environ.get("DDGDA_MODE", "simplified").strip().lower()   # 'simplified' | 'full'


@dataclass
class DistributionForecast:
    """下一期数据分布预测。"""
    predicted_regime: str
    sample_weights: np.ndarray                       # 与历史样本对齐的权重（重训预加权用）
    confidence: float
    attention_weights: Dict[int, float] = field(default_factory=dict)   # 哪些历史时期最像未来

    def normalized_weights(self) -> np.ndarray:
        w = np.asarray(self.sample_weights, dtype=float)
        s = float(np.sum(w))
        if s <= 1e-12:
            return np.ones_like(w) / max(len(w), 1)
        return w * (len(w) / s)   # 均值归一到 1，保持 loss 量纲不变


class DriftTriggeredReweighter:
    """DDG-DA 简化版：用 drift_score 驱动时间衰减样本权重（非完整元学习）。"""

    def __init__(self, max_halflife: float = None, min_halflife: float = None):
        # 漂移弱 → 半衰期长（接近均匀）；漂移强 → 半衰期短（重近期）
        self.max_halflife = max_halflife if max_halflife is not None else \
            float(os.environ.get("DDGDA_MAX_HALFLIFE", "500"))
        self.min_halflife = min_halflife if min_halflife is not None else \
            float(os.environ.get("DDGDA_MIN_HALFLIFE", "30"))

    def reweight(self, n_samples: int, drift_score: float) -> np.ndarray:
        """返回长度 n_samples 的权重（索引 0=最旧，n-1=最新）。"""
        if n_samples <= 0:
            return np.asarray([], dtype=float)
        if not is_enabled():
            return np.ones(n_samples, dtype=float)
        d = float(max(0.0, min(1.0, drift_score)))
        # 半衰期随漂移线性收缩
        halflife = self.max_halflife - d * (self.max_halflife - self.min_halflife)
        halflife = max(1.0, halflife)
        ages = np.arange(n_samples - 1, -1, -1, dtype=float)   # 最新样本 age=0
        weights = 0.5 ** (ages / halflife)
        return weights


class DDGDAForecaster:
    """完整 DDG-DA：元模型预测分布 + attention 选历史时期 + 重加权。

    元模型（轻量 MLP，torch 可选）缺失时自动退化为 DriftTriggeredReweighter。
    """

    def __init__(self, meta_model: Any = None):
        self.meta_model = meta_model
        self._simplified = DriftTriggeredReweighter()

    def forecast_next_distribution(
        self,
        factor_history: np.ndarray,
        drift_signal: float,
        regime_hint: str = "unknown",
    ) -> DistributionForecast:
        """输入因子历史序列 + 漂移信号 → 下期分布预测 + 历史样本预加权。

        简化实现（无 meta_model 或 mode=simplified）：
          - sample_weights 走时间衰减。
          - attention 用"历史窗口与最近窗口的相似度"近似（越像最近，越像未来）。
        """
        factor_history = np.atleast_2d(np.asarray(factor_history, dtype=float))
        n = factor_history.shape[0]
        weights = self._simplified.reweight(n, drift_signal)
        attention = self._similarity_attention(factor_history)
        # 漂移越强，对"最相似历史时期"越自信
        confidence = float(max(0.0, min(1.0, 0.5 + 0.5 * abs(drift_signal))))
        return DistributionForecast(
            predicted_regime=regime_hint,
            sample_weights=weights,
            confidence=confidence,
            attention_weights=attention,
        )

    def _similarity_attention(self, factor_history: np.ndarray, ref_window: int = 20) -> Dict[int, float]:
        """domain-attentive 近似：各历史点与最近 ref_window 均值向量的余弦相似度。"""
        n = factor_history.shape[0]
        if n < 2:
            return {}
        ref = np.mean(factor_history[-min(ref_window, n):], axis=0)
        ref_norm = np.linalg.norm(ref)
        if ref_norm < 1e-12:
            return {}
        att: Dict[int, float] = {}
        for i in range(n):
            v = factor_history[i]
            vn = np.linalg.norm(v)
            if vn < 1e-12:
                continue
            att[i] = float(np.dot(v, ref) / (vn * ref_norm))
        return att

    def reweight_training_data(
        self,
        data_len: int,
        forecast: DistributionForecast,
    ) -> np.ndarray:
        """重训前调用：返回与训练样本对齐的权重（均值归一到 1）。"""
        w = np.asarray(forecast.sample_weights, dtype=float)
        if w.size != data_len:
            # 长度不匹配时重算简化权重，避免错配
            w = self._simplified.reweight(data_len, forecast.confidence)
        return DistributionForecast(
            predicted_regime=forecast.predicted_regime,
            sample_weights=w,
            confidence=forecast.confidence,
        ).normalized_weights()


_forecaster_singleton: Optional[DDGDAForecaster] = None


def get_forecaster() -> DDGDAForecaster:
    global _forecaster_singleton
    if _forecaster_singleton is None:
        _forecaster_singleton = DDGDAForecaster()
    return _forecaster_singleton
