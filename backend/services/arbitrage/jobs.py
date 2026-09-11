# -*- coding: utf-8 -*-
"""套利基础设施定时任务（p2-arb-infra）：scorecard + carry 小资金。"""
from __future__ import annotations

import logging
from typing import Any, Callable, List

logger = logging.getLogger(__name__)


def register_arb_infra_jobs(task_scheduler, wrap: Callable[..., Any],
                            job: Callable[..., Any]) -> List[str]:
    registered: List[str] = []
    try:
        from backend.services.arbitrage.scorecard import scheduled_scorecard
        from backend.services.analysis.scheduling import off_peak_cron

        job("arb_scorecard", "interval 3600s",
            "套利 scorecard：收益/占用/年化/回撤/对账误差 → data/arb/scorecard_latest.json",
            owner="arb", runner=scheduled_scorecard, expected_interval_sec=3600)
        task_scheduler.add_interval_task(
            task_func=wrap("arb_scorecard", scheduled_scorecard),
            interval_seconds=3600, task_id="v3_arb_scorecard", max_instances=1,
        )
        registered.append("arb_scorecard")

        from backend.services.cashflow.carry_small import scheduled_carry_small
        cron = off_peak_cron(5, 45)
        job("carry_small_capital", f"cron {cron['hour']:02d}:{cron['minute']:02d} (BJ 05:45)",
            "Carry 小资金：研究桶~5% 名义 + scorecard 门；默认 Paper（CASHFLOW_CARRY_SMALL_LIVE）",
            owner="cashflow", runner=scheduled_carry_small, expected_interval_sec=86400)
        task_scheduler.add_cron_task(
            task_func=wrap("carry_small_capital", scheduled_carry_small),
            task_id="v3_carry_small_capital",
            hour=cron["hour"], minute=cron["minute"],
        )
        registered.append("carry_small_capital")
    except Exception as exc:
        logger.warning("[arb.jobs] 注册失败: %s", exc)
    logger.info("[arb.jobs] 已注册: %s", registered)
    return registered
