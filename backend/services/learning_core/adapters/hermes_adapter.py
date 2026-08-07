"""Hermes 域适配器 — 薄封装 HermesOrchestrator（L1-L4 自进化）。

只做只读状态聚合与任务触发的门面，不改 Hermes 内部逻辑。
"""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


class HermesAdapter:
    """封装 Hermes 四层自进化。"""

    source = "hermes"

    def dashboard(self) -> Dict[str, Any]:
        """返回 Hermes 成熟度 / 健康 / L1-L4 概览（只读）。"""
        try:
            from backend.services.hermes_orchestrator import hermes
            hermes.ensure_initialized()
            out: Dict[str, Any] = {}
            try:
                out["maturity"] = hermes.compute_maturity_score()
            except Exception:
                out["maturity"] = None
            try:
                out["health"] = hermes.full_health_check()
            except Exception:
                out["health"] = None
            return out
        except Exception as exc:
            logger.debug("[HermesAdapter] dashboard 不可用: %s", exc)
            return {}

    def run_task(self, task_name: str) -> Dict[str, Any]:
        """触发一次 Hermes 任务（wisdom / prompt / architecture / genesis 等）。"""
        try:
            from backend.services.hermes_orchestrator import hermes
            hermes.ensure_initialized()
            handler_map = {
                "wisdom": getattr(hermes, "accumulate_wisdom", None),
                "meta": getattr(hermes, "run_meta_analysis", None),
                "prompt": getattr(hermes, "run_prompt_optimization", None),
                "architecture": getattr(hermes, "run_architecture_evolution", None),
                "genesis": getattr(hermes, "run_strategy_genesis", None),
            }
            fn = handler_map.get(task_name)
            if not fn:
                return {"ok": False, "error": f"未知 Hermes 任务: {task_name}"}
            result = fn()
            return {"ok": True, "result": result}
        except Exception as exc:
            logger.error("[HermesAdapter] run_task(%s) 失败: %s", task_name, exc)
            return {"ok": False, "error": str(exc)}
