"""
Alpha 训练管道门面 — 指向真实训练路径。

[2026-08-15 阶段3 T3]
    本文件历史上是 0 行空文件，却出现在多处注释与文档引用中，属于死代码陷阱。
    现改为门面：真实训练路径有两条，均由本模块统一转发：

    1. 持续重训（轻量模型）：backend/services/ml/training_pipeline.py
       ContinualTrainingPipeline.check_and_retrain —— 滑窗重训 LightGBM 等，
       训练/预测共享 TrainingContext 特征列与时间切分。

    2. 因子进化（因子挖掘）：backend/services/evolution/factor_evolution_loop.py
       FactorEvolutionLoop —— 表达式因子 + 时序三段切分 + purge/embargo 防泄漏。

    多源训练数据装配（K线+资金费/OI/CVD/链上/清算/事件）见：
       backend/services/factor_engine/dataset_builder.py
       build_enriched_dataset() / data_availability_report() / build_weekly_dataset()
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)


def build_training_dataset(
    symbol: str,
    timeframe: str,
    count: int = 500,
    exchange: Optional[str] = None,
    **kw,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """门面：装配多源训练样本（见 dataset_builder.build_enriched_dataset）。"""
    from backend.services.factor_engine.dataset_builder import build_enriched_dataset

    return build_enriched_dataset(symbol, timeframe, count=count, exchange=exchange, **kw)


def check_data_availability(
    symbol: str,
    timeframe: str,
    count: int = 500,
    exchange: Optional[str] = None,
) -> Dict[str, Any]:
    """门面：训练数据可用性门（各源覆盖率，供因子启用判定）。"""
    from backend.services.factor_engine.dataset_builder import data_availability_report

    return data_availability_report(symbol, timeframe, count=count, exchange=exchange)
