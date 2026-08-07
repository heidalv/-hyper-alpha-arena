"""
统一学习后端抽象层 (L1)

本包定义智能学习中心的统一后端接口与注册表，取代旧版
``unified_learning_service.process_outcome`` 内联拼装 + ``learning_bus.dispatch``
双入口的碎片化结构。

架构：
    TradeOutcome → UnifiedLearningService.process_outcome
                       │
                       ├── 9 步 EMA 核心更新（绩效矩阵/记忆/偏离检测）
                       │
                       └── BackendRegistry.handle_all(db, outcome)
                              │
                              ├── CausalDiagnosisBackend      （亏损根因）
                              ├── ReflexionBackend            （亏损反思）
                              ├── PromotionBackend            （达标晋升）
                              ├── TemplateStatsBackend        （模板 live stats 回灌）
                              ├── QaaBackend                  （QAA 进化）
                              ├── FactorJointBackend          （因子-策略贝叶斯）
                              ├── DriftDetectionBackend       （概念漂移）
                              ├── ReviewBackend               （定期复盘，计数触发）
                              ├── MinerBackend                （模式挖掘，计数触发）
                              ├── PatternExtractionBackend    （成功模板提取）
                              └── CausalDiscoveryBackend      （因果发现）

新增后端只需继承 LearningBackend 并在注册表注册，无需改动 process_outcome。
"""

from .backend_base import LearningBackend, AsyncBackend, ThresholdBackend
from .backend_registry import (
    BackendRegistry,
    registry,
    register_backend,
)
from .backend_loader import BackendLoader, backend_loader

__all__ = [
    "LearningBackend",
    "AsyncBackend",
    "ThresholdBackend",
    "BackendRegistry",
    "registry",
    "register_backend",
    "BackendLoader",
    "backend_loader",
]
