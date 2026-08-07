"""假设域适配器 — 薄封装 StrategyHypothesisEngine。

统一内核通过本适配器触发"假设生成→回测验证→晋升"，并把每一步落成 EvolutionEnvelope
写入血缘账本，实现可追溯。P2 会在此基础上补"晋升→GA 进化"断链。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..envelope import (
    EvolutionEnvelope,
    STAGE_HYPOTHESIS,
    STAGE_VALIDATE,
    STAGE_EVOLVE,
    STAGE_DEPLOY,
    STATUS_PASSED,
    STATUS_REJECTED,
    STATUS_DEPLOYED,
    STATUS_PENDING,
)
from ..ledger import ledger
from .. import flags

logger = logging.getLogger(__name__)


class HypothesisAdapter:
    """封装策略假设引擎。"""

    source = "hypothesis_engine"

    def stats(self) -> Dict[str, Any]:
        try:
            from backend.services.strategy_hypothesis_engine import get_hypothesis_engine
            return get_hypothesis_engine().get_stats()
        except Exception as exc:
            logger.debug("[HypothesisAdapter] stats 不可用: %s", exc)
            return {}

    def run_cycle(
        self,
        *,
        symbols: Optional[List[str]] = None,
        market_context: Optional[Dict[str, Any]] = None,
        record_lineage: bool = True,
    ) -> Dict[str, Any]:
        """运行一次完整假设周期，返回 summary，并写入血缘账本。

        每个通过验证并晋升的假设，都会产生一条 lineage：
        hypothesis -> validate -> deploy(晋升为模板)
        """
        from backend.database.connection import SessionLocal
        from backend.services.strategy_hypothesis_engine import get_hypothesis_engine

        engine = get_hypothesis_engine()
        context = market_context or {"regime": "unknown", "source": "learning_core"}
        db = SessionLocal()
        try:
            summary = engine.run_full_cycle(context, symbols, db=db)
        finally:
            db.close()

        if record_lineage:
            self._record_summary(summary, context)
        return summary

    def _record_summary(self, summary: Dict[str, Any], context: Dict[str, Any]) -> None:
        """把 run_full_cycle 的 details 转成血缘 envelope。"""
        for item in summary.get("details", []) or []:
            try:
                root = EvolutionEnvelope.root(
                    stage=STAGE_HYPOTHESIS,
                    source=self.source,
                    payload={"hypothesis_id": item.get("id"), "regime": context.get("regime")},
                    status=STATUS_PASSED,
                )
                ledger.record(root)

                passed = bool(item.get("passed"))
                validate = root.child(
                    stage=STAGE_VALIDATE,
                    source=self.source,
                    payload={"error": item.get("error", "")},
                    metrics={
                        "sharpe": item.get("sharpe"),
                        "win_rate": item.get("win_rate"),
                        "max_dd": item.get("max_dd"),
                        "trades": item.get("trades"),
                    },
                    status=STATUS_PASSED if passed else STATUS_REJECTED,
                )
                ledger.record(validate)

                tpl_id = item.get("promoted_template_id")
                if passed and tpl_id:
                    deploy = validate.child(
                        stage=STAGE_DEPLOY,
                        source=self.source,
                        payload={"template_id": tpl_id},
                        status=STATUS_DEPLOYED,
                    )
                    ledger.record(deploy)
                    # 补断链：假设晋升后自动进入 GA 进化（flag 门控，默认关闭）
                    self._auto_evolve(deploy, tpl_id)
            except Exception as exc:
                logger.debug("[HypothesisAdapter] 记录血缘失败: %s", exc)

    def _auto_evolve(self, deploy_env: EvolutionEnvelope, template_id: str) -> None:
        """把晋升模板自动送入 StrategyEvolver GA（修复原"只建模板不触发进化"断链）。

        默认由 HYPOTHESIS_AUTO_EVOLVE=False 门控，避免影响正在跑的实盘进化节奏；
        开启后调用 evolution_scheduler.trigger_emergency_evolution 对该模板做单独进化，
        并把结果记为 evolve 阶段血缘节点，实现"假设→进化"可追溯闭环。
        """
        if not flags.get_flag("HYPOTHESIS_AUTO_EVOLVE"):
            return
        try:
            from backend.services.evolution_scheduler import evolution_scheduler
            result = evolution_scheduler.trigger_emergency_evolution(
                template_id, reason="hypothesis_promoted"
            )
            evolve = deploy_env.child(
                stage=STAGE_EVOLVE,
                source=self.source,
                payload={"template_id": template_id, "trigger": result},
                status=STATUS_PENDING if result.get("started") else STATUS_REJECTED,
            )
            ledger.record(evolve)
            logger.info(
                "[HypothesisAdapter] 假设晋升模板 %s 自动进化触发: %s",
                template_id, result,
            )
        except Exception as exc:
            logger.warning("[HypothesisAdapter] 自动进化失败 %s: %s", template_id, exc)
