"""统一进化学习内核 — LearningOrchestrator

三模块合并后的**唯一编排入口**。当前阶段（P0）以"合门面、不改内核"的方式，
通过适配器聚合假设 / Hermes / 在线学习状态，并统一血缘账本与实时广播。
后续阶段（P2+）会把假设→GA 断链、调度器合并、RL / codegen 逐步接入这里。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from . import flags
from .envelope import EvolutionEnvelope
from .ledger import ledger
from .adapters import HypothesisAdapter, HermesAdapter, LearningAdapter

logger = logging.getLogger(__name__)


class LearningOrchestrator:
    """统一进化学习内核编排器（单例）。"""

    def __init__(self) -> None:
        self.hypothesis = HypothesisAdapter()
        self.hermes = HermesAdapter()
        self.learning = LearningAdapter()

    # ── 血缘 API ──

    def emit(self, env: EvolutionEnvelope) -> EvolutionEnvelope:
        """由任意域调用，把一条 envelope 记入血缘账本并实时广播。"""
        return ledger.record(env)

    def get_lineage(self, lineage_id: str) -> List[Dict[str, Any]]:
        return ledger.get_lineage(lineage_id)

    def recent_events(self, limit: int = 100, stage: Optional[str] = None) -> List[Dict[str, Any]]:
        return ledger.recent(limit=limit, stage=stage)

    def recent_lineages(self, limit: int = 30) -> List[Dict[str, Any]]:
        return ledger.recent_lineages(limit=limit)

    # ── 统一概览（合并两套 overview 的内核实现）──

    def overview(self) -> Dict[str, Any]:
        """统一进化中枢概览：假设 / Hermes / 在线学习 / 进化 / 血缘账本。

        取代 /api/intelligent-learning/overview 与 /api/learning/dashboard/overview
        两套重叠接口的后端聚合逻辑。
        """
        from datetime import datetime, timezone

        return {
            "core": {
                "flags": flags.all_flags(),
                "ledger": ledger.stats(),
            },
            "hypothesis": self._safe(self.hypothesis.stats),
            "hermes": self._safe(self.hermes.dashboard),
            "learning_loop": self._safe(self.learning.loop_status),
            "evolution": self._safe(self.learning.evolution_status),
            "backends": self._safe(self.learning.backends_status),
            "scheduler": self._safe(self._scheduler_status),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def _scheduler_status(self) -> Dict[str, Any]:
        from .scheduler_facade import unified_scheduler
        return unified_scheduler.status()

    def trigger_scheduled(self, task: str) -> Dict[str, Any]:
        """统一调度器触发入口。"""
        from .scheduler_facade import unified_scheduler
        return unified_scheduler.trigger(task)

    # ── 触发型 API ──

    def run_hypothesis_cycle(
        self,
        *,
        symbols: Optional[List[str]] = None,
        market_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """触发假设全周期（生成→验证→晋升），写入血缘。"""
        return self.hypothesis.run_cycle(symbols=symbols, market_context=market_context)

    def run_hermes_task(self, task_name: str) -> Dict[str, Any]:
        return self.hermes.run_task(task_name)

    # ── 内部工具 ──

    @staticmethod
    def _safe(fn) -> Dict[str, Any]:
        try:
            return fn() or {}
        except Exception as exc:  # 单个子系统不可用不影响整体概览
            logger.debug("[LearningOrchestrator] 子系统聚合失败: %s", exc)
            return {"error": str(exc)}


# 单例
orchestrator = LearningOrchestrator()
