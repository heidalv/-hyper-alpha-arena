"""
BackendLoader — 学习后端加载器

对标因子系统的 FactorLoader，但因学习后端数量固定且多有状态（计数器/缓存），
采用显式注册而非目录扫描：更可控、启动更快、无意外注册。

调用方式：
    from backend.services.learning import BackendLoader
    BackendLoader().load_all()   # 启动时调用一次（通常在 startup.py）

幂等：重复调用不会重复注册（registry.register 内部去重）。
"""
from __future__ import annotations

import logging

from .backend_registry import registry

logger = logging.getLogger(__name__)


class BackendLoader:
    """学习后端加载器（显式注册）。"""

    def __init__(self) -> None:
        self.registry = registry

    def load_all(self) -> int:
        """注册全部内置学习后端。

        Returns:
            成功注册的后端数量
        """
        count = 0
        for backend in self._iter_default_backends():
            try:
                # 每次实例化新的（计数器需独立状态）
                self.registry.register(backend, override=False)
                count += 1
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "[BackendLoader] 注册后端 %s 失败: %s",
                    getattr(backend, "name", backend.__class__.__name__), e,
                )
        logger.info(
            "[BackendLoader] 学习后端加载完成: %d/%d 已注册",
            count, self._total,
        )
        return count

    @property
    def _total(self) -> int:
        return len(self._backend_classes())

    def _iter_default_backends(self):
        """实例化并返回所有默认后端（按 priority 由小到大）。"""
        instances = []
        for cls in self._backend_classes():
            try:
                instances.append(cls())
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "[BackendLoader] 实例化后端 %s 失败: %s", cls.__name__, e
                )
        # 按 priority 排序，注册顺序即调度顺序（registry.handle_all 也按 priority 排）
        instances.sort(key=lambda b: getattr(b, "priority", 100))
        return instances

    @staticmethod
    def _backend_classes():
        """返回所有内置后端类（延迟 import 避免循环依赖）。"""
        from .backends.causal_diagnosis_backend import CausalDiagnosisBackend
        from .backends.reflexion_backend import ReflexionBackend
        from .backends.promotion_backend import PromotionBackend
        from .backends.template_stats_backend import TemplateStatsBackend
        from .backends.qaa_backend import QaaBackend
        from .backends.qaa_semantic_memory_backend import QaaSemanticMemoryBackend
        from .backends.factor_joint_backend import FactorJointBackend
        from .backends.drift_detection_backend import DriftDetectionBackend
        from .backends.review_backend import ReviewBackend
        from .backends.miner_backend import MinerBackend
        from .backends.pattern_extraction_backend import PatternExtractionBackend
        from .backends.causal_discovery_backend import CausalDiscoveryBackend
        from .backends.hermes_agent_wisdom_backend import HermesAgentWisdomBackend
        from .backends.block_pattern_learning_backend import BlockPatternLearningBackend

        return [
            CausalDiagnosisBackend,
            ReflexionBackend,
            PromotionBackend,
            TemplateStatsBackend,
            QaaBackend,
            QaaSemanticMemoryBackend,
            FactorJointBackend,
            DriftDetectionBackend,
            ReviewBackend,
            MinerBackend,
            PatternExtractionBackend,
            CausalDiscoveryBackend,
            HermesAgentWisdomBackend,
            BlockPatternLearningBackend,
        ]


# 全局单例
backend_loader = BackendLoader()
