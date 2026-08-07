"""
持续重训管线 —— 对标 FreqAI 持续学习循环。

每 live_retrain_hours 滑窗重训，替换旧模型；训练/预测共享 TrainingContext 的
feature_columns 与时间切分，杜绝特征漂移与前视泄漏。
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional

import pandas as pd

from backend.services.ml.model_base import SupervisedModel, get_model
from backend.services.ml.training_context import (
    RollingWindow,
    RollingWindowConfig,
    RollingWindowGenerator,
    TrainingContext,
    build_training_context,
)

logger = logging.getLogger(__name__)

# 标签列命名约定（对标 FreqAI &- 前缀）
LABEL_PREFIX = "&-"


class ContinualTrainingPipeline:
    """持续重训编排器。"""

    def __init__(
        self,
        config: Optional[RollingWindowConfig] = None,
        model_type: str = None,
        model_dir: Optional[str] = None,
    ):
        self.config = config or RollingWindowConfig()
        self.model_type = (model_type or os.environ.get("ML_MODEL_TYPE", "lightgbm")).strip().lower()
        self.model_dir = model_dir or os.environ.get(
            "ML_MODEL_DIR", os.path.join(".", "data", "ml_models")
        )
        self._generator = RollingWindowGenerator()
        self.last_retrain: Dict[str, datetime] = {}
        self.models: Dict[str, SupervisedModel] = {}

    # ---------- 对外主入口 ----------
    def check_and_retrain(
        self,
        symbol: str,
        tier: str,
        data: pd.DataFrame,
        feature_columns: List[str],
        label_fn: Callable[[pd.DataFrame], pd.Series],
        *,
        timeframe: str = "",
        force: bool = False,
        now: Optional[datetime] = None,
    ) -> Optional[SupervisedModel]:
        """若距上次重训 > live_retrain_hours（或 force），触发一次滚动重训。

        Args:
            data: 含特征列 + 供 label_fn 使用的原始列（如 close），DatetimeIndex。
            feature_columns: 精确特征列清单（train/predict 共用）。
            label_fn: data → 前瞻标签 Series（&- 前缀，如 &-fwd_return_5）。
            force: 忽略时间节奏强制重训（首次/测试用）。

        Returns:
            新训练好的 SupervisedModel，或 None（未到重训点/数据不足）。
        """
        key = f"{symbol}_{tier}"
        if not force and not self._due_for_retrain(key, now=now):
            return None
        if data is None or len(data) == 0:
            return None

        window = self._generator.latest(data.index[0], data.index[-1], self.config)
        if window is None:
            logger.info("[ML] %s 数据跨度不足以生成滚动窗口，跳过重训", key)
            return None

        ctx = self._build_context(symbol, tier, timeframe, data, feature_columns, label_fn, window, key)
        if ctx is None or len(ctx.features) == 0:
            logger.info("[ML] %s 训练窗口无有效样本，跳过", key)
            return None

        model = get_model(self.model_type)
        try:
            model.fit(ctx)
        except Exception as e:  # noqa: BLE001 —— 训练失败不影响调用方
            logger.warning("[ML] %s 训练失败（忽略本次）: %s", key, e)
            return None

        try:
            model.save(self._model_path(key))
        except Exception as e:  # noqa: BLE001
            logger.warning("[ML] %s 模型持久化失败（模型仍在内存可用）: %s", key, e)

        self.models[key] = model
        self.last_retrain[key] = now or datetime.now()
        logger.info("[ML] %s 重训完成 model_type=%s train=[%s..%s] samples=%d",
                    key, model.model_type, window.train_start.date(), window.train_end.date(),
                    len(ctx.features))
        return model

    def predict(self, symbol: str, tier: str, features: pd.DataFrame,
                feature_columns: List[str]) -> Optional[pd.Series]:
        """用当前内存模型预测；无模型返回 None。"""
        key = f"{symbol}_{tier}"
        model = self.models.get(key)
        if model is None:
            return None
        return model.predict(features, feature_columns)

    # ---------- 内部 ----------
    def _due_for_retrain(self, key: str, now: Optional[datetime] = None) -> bool:
        last = self.last_retrain.get(key)
        if last is None:
            return True
        now = now or datetime.now()
        return (now - last) >= timedelta(hours=self.config.live_retrain_hours)

    def _build_context(
        self,
        symbol: str,
        tier: str,
        timeframe: str,
        data: pd.DataFrame,
        feature_columns: List[str],
        label_fn: Callable[[pd.DataFrame], pd.Series],
        window: RollingWindow,
        key: str,
    ) -> Optional[TrainingContext]:
        try:
            labels = label_fn(data)
        except Exception as e:  # noqa: BLE001
            logger.warning("[ML] %s label_fn 失败: %s", key, e)
            return None
        if labels is None or len(labels) == 0:
            return None
        # 只保留存在的特征列
        cols = [c for c in feature_columns if c in data.columns]
        if not cols:
            logger.warning("[ML] %s 无可用特征列", key)
            return None
        return build_training_context(
            symbol=symbol,
            tier=tier,
            timeframe=timeframe,
            features=data[cols],
            labels=labels,
            feature_columns=cols,
            window=window,
            model_identifier=key,
            retrain_count=self._retrain_count(key),
        )

    def _retrain_count(self, key: str) -> int:
        return 1 if key in self.last_retrain else 0

    def _model_path(self, key: str) -> str:
        safe = key.replace(":", "_").replace("/", "_")
        return os.path.join(self.model_dir, f"{safe}_{self.model_type}.pkl")


def make_forward_return_label(horizon: int = 5, price_col: str = "close") -> Callable[[pd.DataFrame], pd.Series]:
    """便捷标签工厂：horizon 根之后的前瞻收益（&-fwd_return_{h}）。

    label_t = close_{t+h}/close_t - 1；末尾 h 根无标签（NaN，训练时被剔除）。
    """
    def _fn(data: pd.DataFrame) -> pd.Series:
        close = data[price_col]
        fwd = close.shift(-horizon) / close - 1.0
        fwd.name = f"{LABEL_PREFIX}fwd_return_{horizon}"
        return fwd

    return _fn
