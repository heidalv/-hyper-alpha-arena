# -*- coding: utf-8 -*-
"""p3-promotion 定时任务：资本分配、尾部 funding 扫描、付费数据决策、F4 门禁、E5 研究桶扫描。"""
from __future__ import annotations

import logging
from typing import Any, Callable, List

logger = logging.getLogger(__name__)


def register_promotion_jobs(task_scheduler, wrap: Callable[..., Any],
                            job: Callable[..., Any]) -> List[str]:
    registered: List[str] = []
    try:
        from backend.services.allocation.capital_allocator import apply_enabled, scheduled_allocate
        from backend.services.analysis.scheduling import off_peak_cron

        if apply_enabled():
            cron_a = off_peak_cron(5, 20)
            job("capital_allocate", f"cron {cron_a['hour']:02d}:{cron_a['minute']:02d} (BJ 05:20)",
                "E4 三桶分配：Timing 建议 × 可信度缩放 → data/allocation/latest.json",
                owner="allocation", runner=scheduled_allocate, expected_interval_sec=86400)
            task_scheduler.add_cron_task(
                task_func=wrap("capital_allocate", scheduled_allocate),
                task_id="v3_capital_allocate", hour=cron_a["hour"], minute=cron_a["minute"],
            )
            registered.append("capital_allocate")
        else:
            logger.info("[promotion.jobs] ALLOCATOR_APPLY=false，跳过 capital_allocate 空转")

        from backend.services.cashflow.funding_tail_harvest import scheduled_funding_tail
        job("funding_tail_scan", "interval 1800s",
            "尾部 funding 收割扫描（默认影子入 signal_ledger）",
            owner="cashflow", runner=scheduled_funding_tail, expected_interval_sec=1800)
        task_scheduler.add_interval_task(
            task_func=wrap("funding_tail_scan", scheduled_funding_tail),
            interval_seconds=1800, task_id="v3_funding_tail_scan", max_instances=1,
        )
        registered.append("funding_tail_scan")

        def _paid_job():
            from backend.research.paid_data_decision import run_paid_data_decision
            return run_paid_data_decision(persist=True)

        cron_p = off_peak_cron(4, 50)
        job("paid_data_decision", f"cron {cron_p['hour']:02d}:{cron_p['minute']:02d} (BJ 04:50)",
            "付费数据源决策卡（基于 event_study）",
            owner="research", runner=_paid_job, expected_interval_sec=86400)
        task_scheduler.add_cron_task(
            task_func=wrap("paid_data_decision", _paid_job),
            task_id="v3_paid_data_decision", hour=cron_p["hour"], minute=cron_p["minute"],
        )
        registered.append("paid_data_decision")

        def _f4_job():
            from backend.services.trend_e1_f4_gate import evaluate_f4_gate
            return evaluate_f4_gate(persist=True)

        cron_f = off_peak_cron(0, 35)
        job("trend_e1_f4_gate", f"cron {cron_f['hour']:02d}:{cron_f['minute']:02d} (BJ 00:35)",
            "E1 Aster 小额实盘 F4 门禁检查单",
            owner="trend", runner=_f4_job, expected_interval_sec=86400)
        task_scheduler.add_cron_task(
            task_func=wrap("trend_e1_f4_gate", _f4_job),
            task_id="v3_trend_e1_f4_gate", hour=cron_f["hour"], minute=cron_f["minute"],
        )
        registered.append("trend_e1_f4_gate")

        def _e5_job():
            from backend.services.strategies.event.research_bucket import scan_and_promote
            return scan_and_promote(auto_promote=False)

        cron_e = off_peak_cron(6, 10)
        job("e5_research_bucket_scan", f"cron {cron_e['hour']:02d}:{cron_e['minute']:02d} (BJ 06:10)",
            "E5 过门策略扫描（默认不自动 promote；E5_AUTO_PROMOTE=true 才写入阶梯）",
            owner="event", runner=_e5_job, expected_interval_sec=86400)
        task_scheduler.add_cron_task(
            task_func=wrap("e5_research_bucket_scan", _e5_job),
            task_id="v3_e5_research_bucket_scan", hour=cron_e["hour"], minute=cron_e["minute"],
        )
        registered.append("e5_research_bucket_scan")
    except Exception as exc:
        logger.warning("[promotion.jobs] 注册失败: %s", exc)
    logger.info("[promotion.jobs] 已注册: %s", registered)
    return registered
