"""
Qlib 式学习型因子加权层（整改#4）。

与手工 regime 权重表（factor_weighting.py）并列，通过开关切换/并行影子运行。
数据流：因子值 → learn/infer 处理器（防前视）→ 标签(前瞻收益) → Model.fit
        → predict(分数) → 复合信号。

模型后端复用整改#10 的统一接口（get_model / SupervisedModel / TrainingContext），
不重复造 LightGBM/GRU 轮子；额外提供 ICWeightedFusion 非参数兜底（无需 ML）。

渐进迁移（doc §整改#4）：
  Phase 1  mode='regime'（默认）：learned 仅 shadow 计算，不影响实盘。
  Phase 2  mode='hybrid'        ：A/B，learned IC 显著优于 regime 时按置信度融合。
  Phase 3  mode='learned'       ：learned 主路径，regime 降级 fallback。
"""
from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ============================ 配置 ============================
def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_str(name: str, default: str) -> str:
    v = os.environ.get(name)
    return v if v else default


@dataclass
class FactorProcessorConfig:
    """对标 Qlib DataHandlerLP 的 learn/infer 处理器分离 —— 防前视偏差。"""
    learn_normalization: str = "cs_zscore"   # 'cs_zscore'|'cs_rank'|'robust_zscore'|'none'
    infer_fillna: bool = True
    infer_dropna_label: bool = True


@dataclass
class LearnedWeightingConfig:
    enabled: bool = field(default_factory=lambda: _env_bool("LEARNED_WEIGHTING_ENABLED", False))
    model_type: str = field(default_factory=lambda: _env_str("LEARNED_MODEL_TYPE", "lightgbm"))
    label_horizon_bars: int = field(default_factory=lambda: _env_int("LEARNED_LABEL_HORIZON", 5))
    retrain_frequency_hours: int = field(default_factory=lambda: _env_int("LEARNED_RETRAIN_HOURS", 24))
    train_lookback_days: int = field(default_factory=lambda: _env_int("LEARNED_TRAIN_LOOKBACK_DAYS", 90))
    min_ic_to_include: float = field(default_factory=lambda: _env_float("LEARNED_MIN_IC", 0.015))
    purge_bars: int = field(default_factory=lambda: _env_int("LEARNED_PURGE_BARS", 5))
    model_dir: str = field(default_factory=lambda: _env_str("LEARNED_MODEL_DIR", os.path.join(".", "data", "ml_models", "factor")))


# ============================ 处理器（learn/infer 分离）============================
class FactorProcessor:
    """归一化处理器：统计量仅在训练集 fit（防前视），推理时套用。"""

    def __init__(self, config: FactorProcessorConfig):
        self.config = config
        self._mu: Optional[pd.Series] = None
        self._sd: Optional[pd.Series] = None
        self._median: Optional[pd.Series] = None
        self._mad: Optional[pd.Series] = None
        self._fitted = False

    def fit(self, train_features: pd.DataFrame) -> None:
        X = train_features.replace([np.inf, -np.inf], np.nan)
        self._mu = X.mean()
        self._sd = X.std(ddof=0).replace(0, 1e-9)
        self._median = X.median()
        self._mad = (X - self._median).abs().median().replace(0, 1e-9)
        self._fitted = True

    def transform(self, features: pd.DataFrame) -> pd.DataFrame:
        X = features.replace([np.inf, -np.inf], np.nan)
        mode = self.config.learn_normalization
        if not self._fitted or mode == "none":
            out = X
        elif mode == "cs_rank":
            # 用训练分布不便直接排名；此处按列在当前批做百分位秩（[0,1]），稳健且无前视风险
            out = X.rank(pct=True)
        elif mode == "robust_zscore":
            out = (X - self._median) / (1.4826 * self._mad)
        else:  # cs_zscore（默认）
            out = (X - self._mu) / self._sd
        if self.config.infer_fillna:
            out = out.fillna(0.0)
        return out


# ============================ 非参数兜底：IC 加权融合 ============================
class ICWeightedFusion:
    """按滚动 IC 加权融合（无需 ML）：score_t = Σ ic_i·f_i / Σ|ic_i|。"""

    def fuse(self, factor_values: pd.DataFrame, ic_history: pd.DataFrame) -> pd.Series:
        common = [c for c in factor_values.columns if c in ic_history.columns]
        if not common:
            return pd.Series(0.0, index=factor_values.index)
        fv = factor_values[common].replace([np.inf, -np.inf], np.nan).fillna(0.0)
        ic = ic_history[common].reindex(fv.index).ffill().fillna(0.0)
        num = (fv * ic).sum(axis=1)
        den = ic.abs().sum(axis=1).replace(0, np.nan)
        return (num / den).fillna(0.0)


# ============================ 因子模型抽象（复用#10 后端）============================
class FactorModel(ABC):
    """对标 Qlib Model：统一 fit/predict。内部委托整改#10 的 SupervisedModel。"""

    @abstractmethod
    def fit(self, features: pd.DataFrame, labels: pd.Series, feature_columns: List[str]) -> None: ...

    @abstractmethod
    def predict(self, features: pd.DataFrame, feature_columns: List[str]) -> pd.Series: ...


class _ML10FactorModel(FactorModel):
    """把整改#10 的 SupervisedModel 适配成因子模型（lightgbm/gru/linear/catboost）。"""

    def __init__(self, model_type: str):
        from backend.services.ml.model_base import get_model

        self._model = get_model(model_type)

    def fit(self, features: pd.DataFrame, labels: pd.Series, feature_columns: List[str]) -> None:
        # 构造一个最小的 TrainingContext 复用 #10 的 fit
        from backend.services.ml.training_context import TrainingContext

        ts0 = features.index[0] if len(features.index) else pd.Timestamp("2020-01-01")
        ts1 = features.index[-1] if len(features.index) else pd.Timestamp("2020-01-02")
        ctx = TrainingContext(
            symbol="_factor", tier="_", timeframe="_",
            features=features, labels=labels, feature_columns=list(feature_columns),
            train_start=ts0, train_end=ts1, purge_end=ts1, predict_start=ts1, predict_end=ts1,
            model_identifier="factor",
        )
        self._model.fit(ctx)

    def predict(self, features: pd.DataFrame, feature_columns: List[str]) -> pd.Series:
        return self._model.predict(features, feature_columns)

    @property
    def backend_type(self) -> str:
        return self._model.model_type


# ============================ 学习型加权主类 ============================
class LearnedFactorWeighting:
    """学习型因子加权主类 —— 与 DynamicFactorWeighting 并列，可切换/影子。"""

    def __init__(self, config: Optional[LearnedWeightingConfig] = None):
        self.config = config or LearnedWeightingConfig()
        self.processor = FactorProcessor(FactorProcessorConfig())
        self.ic_fusion = ICWeightedFusion()
        self.model: Optional[_ML10FactorModel] = None
        self.feature_columns: List[str] = []
        self.last_train_time: Optional[datetime] = None

    # ---- 训练 ----
    def train(self, features_history: pd.DataFrame, labels: pd.Series,
              *, now: Optional[datetime] = None) -> bool:
        """在历史因子矩阵上训练。返回是否训练成功。

        features_history: index=datetime, columns=factor_id 的因子值历史。
        labels: 对齐的前瞻收益（末尾若干根为 NaN，自动剔除）。
        """
        df = features_history.replace([np.inf, -np.inf], np.nan)
        aligned = df.join(labels.rename("__y__"), how="inner").dropna(subset=["__y__"])
        if len(aligned) < 20:
            logger.info("[LearnedWeighting] 训练样本不足(%d)，跳过", len(aligned))
            return False
        feat_cols = [c for c in df.columns]
        self.feature_columns = feat_cols
        # learn 处理器仅在训练集 fit（防前视）
        self.processor.fit(aligned[feat_cols])
        X = self.processor.transform(aligned[feat_cols])
        y = aligned["__y__"]
        try:
            model = _ML10FactorModel(self.config.model_type)
            model.fit(X, y, feat_cols)
            self.model = model
            self.last_train_time = now or datetime.now()
            logger.info("[LearnedWeighting] 训练完成 backend=%s samples=%d features=%d",
                        model.backend_type, len(X), len(feat_cols))
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("[LearnedWeighting] 训练失败: %s", e)
            return False

    # ---- 预测 ----
    def predict_score(self, features: pd.DataFrame) -> pd.Series:
        """对给定因子值矩阵输出学习分数（未训练则返回 0）。"""
        if self.model is None or not self.feature_columns:
            return pd.Series(0.0, index=features.index)
        X = self.processor.transform(features.reindex(columns=self.feature_columns))
        return self.model.predict(X, self.feature_columns)

    # ---- doc 兼容主入口 ----
    def compute_weighted_signal(
        self,
        factor_values: pd.DataFrame,
        factor_metadata: Optional[Dict] = None,
        historical_data: Optional[pd.DataFrame] = None,
        labels: Optional[pd.Series] = None,
        ic_history: Optional[pd.DataFrame] = None,
        now: Optional[datetime] = None,
    ) -> pd.Series:
        """返回加权后的复合信号（方向+强度）。

        1. 到重训时间且提供了 historical_data+labels → 重训。
        2. 有模型 → model.predict；否则回退 ICWeightedFusion（若给了 ic_history）或 0。
        """
        if self._due_for_retrain(now) and historical_data is not None and labels is not None:
            self.train(historical_data, labels, now=now)

        if self.model is not None and self.feature_columns:
            return self.predict_score(factor_values)

        if ic_history is not None:
            return self.ic_fusion.fuse(factor_values, ic_history)
        return pd.Series(0.0, index=factor_values.index)

    def _due_for_retrain(self, now: Optional[datetime] = None) -> bool:
        if self.last_train_time is None:
            return True
        now = now or datetime.now()
        return (now - self.last_train_time) >= timedelta(hours=self.config.retrain_frequency_hours)

    def compute_ic_decay_coupled_retrain_interval(self, ic_halflife_bars: float) -> int:
        """重训频率 = f(IC 半衰期)：半衰期短→更频繁重训。返回小时数。

        以 halflife_bars 与基准（默认 lookback 折算）比例缩放 retrain_frequency_hours，
        夹在 [2h, 7*24h]。
        """
        base = max(1, self.config.retrain_frequency_hours)
        if ic_halflife_bars is None or ic_halflife_bars <= 0:
            return base
        # 半衰期越短，间隔越短；用 min(base, halflife 折半) 的直觉
        scaled = int(max(2, min(base, ic_halflife_bars)))
        return int(np.clip(scaled, 2, 7 * 24))
