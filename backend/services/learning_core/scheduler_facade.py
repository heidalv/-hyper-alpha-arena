"""统一调度器门面 UnifiedScheduler

方案要求"合并 evolution_scheduler + opencode_scheduler"。这两个调度器在启动时各自
注册了大量定时任务（进化 / IC / Hermes L1-L4 / OpenCode 分析等），且系统正在跑实盘，
**物理合并 job 注册风险极高**。因此这里用"门面聚合"的方式统一对外：
  - 单一入口查询两套调度器的任务时间轴与运行状态；
  - 单一入口按任务名分发触发（路由到正确的调度器）；
  - 不改动底层 job 注册，保证实盘调度不中断（可回滚）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


class UnifiedScheduler:
    """跨 evolution_scheduler / opencode_scheduler 的统一门面。"""

    def status(self) -> Dict[str, Any]:
        return {
            "evolution": self._evolution_status(),
            "hermes_opencode": self._opencode_status(),
        }

    def _evolution_status(self) -> Dict[str, Any]:
        try:
            from backend.services.evolution_scheduler import evolution_scheduler
            fn = getattr(evolution_scheduler, "get_status", None)
            if callable(fn):
                return fn()
            return {"running": getattr(evolution_scheduler, "_running_evolution", False)}
        except Exception as exc:
            logger.debug("[UnifiedScheduler] evolution 状态不可用: %s", exc)
            return {}

    def _opencode_status(self) -> Dict[str, Any]:
        try:
            from backend.services.opencode_scheduler import get_hermes_schedule_status
            return get_hermes_schedule_status()
        except Exception as exc:
            logger.debug("[UnifiedScheduler] opencode/hermes 调度状态不可用: %s", exc)
            return {}

    def trigger(self, task: str) -> Dict[str, Any]:
        """按任务名统一分发触发。

        evolution.* -> evolution_scheduler；hermes.* -> hermes_orchestrator。
        """
        try:
            if task.startswith("hermes."):
                from backend.services.learning_core.adapters import HermesAdapter
                return HermesAdapter().run_task(task.split(".", 1)[1])
            if task == "evolution.weekly":
                from backend.services.evolution_scheduler import evolution_scheduler
                fn = getattr(evolution_scheduler, "weekly_evolution", None)
                if callable(fn):
                    fn()
                    return {"ok": True, "task": task}
            if task == "evolution.hypothesis_scan":
                from backend.services.evolution_scheduler import evolution_scheduler
                fn = getattr(evolution_scheduler, "hypothesis_scan", None)
                if callable(fn):
                    fn()
                    return {"ok": True, "task": task}
            return {"ok": False, "error": f"未知统一任务: {task}"}
        except Exception as exc:
            logger.error("[UnifiedScheduler] trigger(%s) 失败: %s", task, exc)
            return {"ok": False, "error": str(exc)}


# 单例
unified_scheduler = UnifiedScheduler()
