# -*- coding: utf-8 -*-
"""E2 现金流定时任务注册（p1-cashflow）。"""
from __future__ import annotations

import logging
from typing import Any, Callable, List

logger = logging.getLogger(__name__)


def register_cashflow_jobs(task_scheduler, wrap: Callable[..., Any], job: Callable[..., Any]) -> List[str]:
    registered: List[str] = []
    try:
        from backend.services.cashflow.e2a_aster_te import scheduled_aster_te
        from backend.services.cashflow.e2b_carry_sim import scheduled_carry_sim
        from backend.services.cashflow.idle_earn import scheduled_idle_earn
        from backend.services.cashflow.rebate_snapshot import snapshot_rebate_config
        from backend.services.analysis.scheduling import off_peak_cron

        job("cashflow_aster_te", "interval 3600s", "Aster Trade&Earn 真实收入账本刷新 → data/cashflow/aster_te",
            owner="cashflow", runner=scheduled_aster_te, expected_interval_sec=3600)
        task_scheduler.add_interval_task(
            task_func=wrap("cashflow_aster_te", scheduled_aster_te),
            interval_seconds=3600, task_id="v3_cashflow_aster_te", max_instances=1,
        )
        registered.append("cashflow_aster_te")

        job("cashflow_carry_sim", "interval 900s", "多场所资金费 carry Paper 模拟（perp_funding 矩阵 + 双腿）",
            owner="cashflow", runner=scheduled_carry_sim, expected_interval_sec=900)
        task_scheduler.add_interval_task(
            task_func=wrap("cashflow_carry_sim", scheduled_carry_sim),
            interval_seconds=900, task_id="v3_cashflow_carry_sim", max_instances=1,
        )
        registered.append("cashflow_carry_sim")

        cron = off_peak_cron(6, 30)
        job("cashflow_idle_earn", f"cron {cron['hour']:02d}:{cron['minute']:02d} (BJ 06:30)",
            "Binance Simple Earn 闲置 USDT 申购（默认 dry；CASHFLOW_IDLE_EARN_LIVE=true 才真申购）",
            owner="cashflow", runner=scheduled_idle_earn, expected_interval_sec=86400)
        task_scheduler.add_cron_task(
            task_func=wrap("cashflow_idle_earn", scheduled_idle_earn),
            task_id="v3_cashflow_idle_earn", hour=cron["hour"], minute=cron["minute"],
        )
        registered.append("cashflow_idle_earn")

        job("cashflow_rebate_snapshot", "interval 3600s", "rebate/arb 双链路开关与引擎配置快照",
            owner="cashflow", runner=snapshot_rebate_config, expected_interval_sec=3600)
        task_scheduler.add_interval_task(
            task_func=wrap("cashflow_rebate_snapshot", snapshot_rebate_config),
            interval_seconds=3600, task_id="v3_cashflow_rebate_snapshot", max_instances=1,
        )
        registered.append("cashflow_rebate_snapshot")
    except Exception as exc:
        logger.warning("[cashflow.jobs] 注册失败: %s", exc)
    return registered
