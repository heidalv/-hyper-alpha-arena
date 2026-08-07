"""统一 ML 训练管线（整改#10，对标 FreqAI FreqaiDataKitchen + IFreqaiModel）。

模块：
  - training_context : TrainingContext / RollingWindowConfig / RollingWindowGenerator
  - model_base       : SupervisedModel 接口 + LightGBM/CatBoost/GRU/Linear 实现 + 注册表
  - training_pipeline: ContinualTrainingPipeline 持续重训编排器

所有重后端（lightgbm/catboost/torch）均惰性导入，缺失时 get_model 自动降级到 linear，
保证在最小依赖环境下管线依然可跑（零风险）。
"""
from backend.services.ml.training_context import (
    TrainingContext,
    RollingWindowConfig,
    RollingWindowGenerator,
    RollingWindow,
    build_training_context,
)
from backend.services.ml.model_base import (
    SupervisedModel,
    LinearTrendModel,
    LightGBMTrendModel,
    CatBoostModel,
    PyTorchGRUModel,
    MODEL_REGISTRY,
    get_model,
)
from backend.services.ml.training_pipeline import ContinualTrainingPipeline
from backend.services.ml.activation_service import (
    run_ml_activation_tick,
    get_activation_stats,
    is_ml_activation_enabled,
)

__all__ = [
    "TrainingContext",
    "RollingWindowConfig",
    "RollingWindowGenerator",
    "RollingWindow",
    "build_training_context",
    "SupervisedModel",
    "LinearTrendModel",
    "LightGBMTrendModel",
    "CatBoostModel",
    "PyTorchGRUModel",
    "MODEL_REGISTRY",
    "get_model",
    "ContinualTrainingPipeline",
    "run_ml_activation_tick",
    "get_activation_stats",
    "is_ml_activation_enabled",
]
