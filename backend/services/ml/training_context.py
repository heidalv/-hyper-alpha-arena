"""
统一训练上下文对象 —— 对标 FreqAI FreqaiDataKitchen。

逐品种/逐周期捆绑：特征矩阵 + 标签 + train/predict 时间切分 + 精确特征列清单。
核心价值：
  - 消除"特征漂移"bug —— train 与 predict 用同一份 feature_columns。
  - 内建 purge/embargo 前视防护 —— predict 窗严格在 purge 之后，永不重叠 train。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import timedelta
from typing import List, Optional, Tuple

import pandas as pd


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class TrainingContext:
    """逐品种/周期的训练上下文 —— 在 train() 与 predict() 间传递。"""
    # 标识
    symbol: str
    tier: str                              # 'short'|'mid'|'long'
    timeframe: str

    # 数据（features/labels 已切到 train 窗口，且行对齐）
    features: pd.DataFrame                  # (datetime, factor_id) 因子值矩阵
    labels: pd.Series                       # (datetime) 前瞻收益/分类标签
    feature_columns: List[str]              # 精确特征列清单（predict 复用，杜绝漂移）

    # 时间切分（前视防护）
    train_start: pd.Timestamp
    train_end: pd.Timestamp                 # train_period 边界
    purge_end: Optional[pd.Timestamp]       # purge gap 结束
    predict_start: pd.Timestamp             # predict 窗开始（永不重叠 train）
    predict_end: pd.Timestamp

    # 元数据
    model_identifier: str                   # 持久化路径标识
    retrain_count: int = 0
    sample_weights: Optional[pd.Series] = None  # DDGDA 重训预加权（#18）

    def aligned_train_xy(self) -> Tuple[pd.DataFrame, pd.Series]:
        """返回对齐、去 NaN 后的 (X_train, y_train)。"""
        cols = [c for c in self.feature_columns if c in self.features.columns]
        X = self.features[cols]
        df = X.join(self.labels.rename("__label__"), how="inner")
        df = df.replace([float("inf"), float("-inf")], pd.NA).dropna()
        return df[cols], df["__label__"]

    def aligned_train_weights(self) -> Optional[pd.Series]:
        """与 aligned_train_xy 行对齐的样本权重。"""
        if self.sample_weights is None or len(self.sample_weights) == 0:
            return None
        cols = [c for c in self.feature_columns if c in self.features.columns]
        X = self.features[cols]
        df = X.join(self.labels.rename("__label__"), how="inner")
        df = df.replace([float("inf"), float("-inf")], pd.NA).dropna()
        w = self.sample_weights.reindex(df.index).fillna(1.0)
        return w


@dataclass
class RollingWindowConfig:
    """严格滚动窗口配置 —— 对标 FreqAI train_period_days/backtest_period_days/live_retrain_hours。

    env 开关（doc §整改#10）：ML_TRAIN_PERIOD_DAYS / ML_PREDICT_PERIOD_DAYS /
    ML_PURGE_DAYS / ML_EMBARGO_DAYS / ML_LIVE_RETRAIN_HOURS。
    """
    train_period_days: int = field(default_factory=lambda: _env_int("ML_TRAIN_PERIOD_DAYS", 90))
    predict_period_days: int = field(default_factory=lambda: _env_int("ML_PREDICT_PERIOD_DAYS", 7))
    purge_days: int = field(default_factory=lambda: _env_int("ML_PURGE_DAYS", 5))
    embargo_days: int = field(default_factory=lambda: _env_int("ML_EMBARGO_DAYS", 3))
    live_retrain_hours: int = field(default_factory=lambda: _env_int("ML_LIVE_RETRAIN_HOURS", 12))
    continual_warm_start: bool = field(default_factory=lambda: _env_bool("ML_CONTINUAL_WARM_START", False))


@dataclass
class RollingWindow:
    """单个滚动窗口的时间边界。"""
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    purge_end: pd.Timestamp
    predict_start: pd.Timestamp
    predict_end: pd.Timestamp

    def as_tuple(self) -> Tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]:
        return (self.train_start, self.train_end, self.purge_end, self.predict_start, self.predict_end)


class RollingWindowGenerator:
    """生成严格滚动窗口序列 —— 预测窗永不重叠训练窗（purge 之后才开始）。"""

    def generate(self, data_start, data_end, config: RollingWindowConfig) -> List[RollingWindow]:
        """返回 [RollingWindow, ...]。

        每窗：train[train_start, train_end] → purge(purge_days) → predict[predict_start, predict_end]。
        下一窗 train_start 前进 predict_period_days（滚动重训节奏）；predict_end+embargo 超界则停。
        """
        data_start = pd.Timestamp(data_start)
        data_end = pd.Timestamp(data_end)
        windows: List[RollingWindow] = []

        step = max(1, int(config.predict_period_days))
        train_start = data_start
        while True:
            train_end = train_start + timedelta(days=config.train_period_days)
            purge_end = train_end + timedelta(days=max(0, config.purge_days))
            predict_start = purge_end + timedelta(days=1)
            predict_end = predict_start + timedelta(days=step)
            embargo_end = predict_end + timedelta(days=max(0, config.embargo_days))
            if embargo_end > data_end:
                break
            windows.append(RollingWindow(
                train_start=train_start,
                train_end=train_end,
                purge_end=purge_end,
                predict_start=predict_start,
                predict_end=predict_end,
            ))
            train_start = train_start + timedelta(days=step)
        return windows

    def latest(self, data_start, data_end, config: RollingWindowConfig) -> Optional[RollingWindow]:
        """返回可用的最后一个滚动窗口（用于实盘"最近一次重训"）。"""
        wins = self.generate(data_start, data_end, config)
        return wins[-1] if wins else None


def build_training_context(
    *,
    symbol: str,
    tier: str,
    timeframe: str,
    features: pd.DataFrame,
    labels: pd.Series,
    feature_columns: List[str],
    window: RollingWindow,
    model_identifier: str,
    retrain_count: int = 0,
    sample_weights: Optional[pd.Series] = None,
) -> TrainingContext:
    """把整段 features/labels 按 window 的 train 区间切片，构建 TrainingContext。"""
    mask = (features.index >= window.train_start) & (features.index <= window.train_end)
    train_features = features.loc[mask]
    train_labels = labels.loc[labels.index.isin(train_features.index)]
    train_weights = None
    if sample_weights is not None and len(sample_weights):
        train_weights = sample_weights.loc[sample_weights.index.isin(train_features.index)]
    return TrainingContext(
        symbol=symbol,
        tier=tier,
        timeframe=timeframe,
        features=train_features,
        labels=train_labels,
        feature_columns=list(feature_columns),
        train_start=window.train_start,
        train_end=window.train_end,
        purge_end=window.purge_end,
        predict_start=window.predict_start,
        predict_end=window.predict_end,
        model_identifier=model_identifier,
        retrain_count=retrain_count,
        sample_weights=train_weights,
    )
