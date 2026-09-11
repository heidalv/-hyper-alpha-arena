# -*- coding: utf-8 -*-
"""v3 扩展定时任务（由 v3_jobs.register_v3_jobs 调用；每个 Phase 在此追加）。

register_extended_jobs(task_scheduler, wrap, job) → 返回已注册任务名列表。
  wrap(job_name, fn)  任务包装（心跳/失败登记到 job_registry）
  job(name, cadence, description, owner)  登记任务元数据（/api/ops/jobs 展示）
"""
from __future__ import annotations

import logging
from typing import Any, Callable, List

logger = logging.getLogger(__name__)


def register_extended_jobs(task_scheduler, wrap: Callable[..., Any], job: Callable[..., Any]) -> List[str]:
    registered: List[str] = []

    # ── p0-risk-engine：RiskEngine 巡检（60s：闪崩 / 组合回撤 / 急停文件 / 过期状态回收）──
    try:
        from backend.services.risk.risk_engine import run_tick_job
        job("risk_engine_tick", "interval 60s", "闪崩(BTC 1h/4h)、组合回撤分级、急停文件同步、TTL 状态回收",
            owner="risk", runner=run_tick_job, expected_interval_sec=60)
        task_scheduler.add_interval_task(
            task_func=wrap("risk_engine_tick", run_tick_job),
            interval_seconds=60, task_id="v3_risk_engine_tick", max_instances=1,
        )
        registered.append("risk_engine_tick")
    except Exception as exc:
        logger.warning("[v3_jobs_ext] risk_engine_tick 注册失败: %s", exc)

    # ── p0-jobs-alerts：任务看门狗（5 分钟：滞后/中断告警 + job_runs 清理）──
    try:
        from backend.services.ops.job_registry import watchdog_job
        job("job_watchdog", "interval 300s", "定时任务滞后(P2)/中断(P1)告警；清理 14 天前 job_runs",
            owner="ops", runner=watchdog_job, expected_interval_sec=300)
        task_scheduler.add_interval_task(
            task_func=wrap("job_watchdog", watchdog_job),
            interval_seconds=300, task_id="v3_job_watchdog", max_instances=1,
        )
        registered.append("job_watchdog")
    except Exception as exc:
        logger.warning("[v3_jobs_ext] job_watchdog 注册失败: %s", exc)

    # ── p0-event-data：事件数据采集（公告 / forceOrder 清算流 / 持仓结构 / 全币池 funding / 桥接）──
    # 默认由数据中心进程执行（EVENT_COLLECTORS_HOST=dc）；主进程只登记元数据与手动 runner，
    # 让 /api/ops/jobs 可见、可触发。设 EVENT_COLLECTORS_HOST=main|both 可改在主进程跑。
    try:
        from backend.services.events.collectors import register_event_jobs
        registered.extend(register_event_jobs(task_scheduler, wrap, job, host="main"))
    except Exception as exc:
        logger.warning("[v3_jobs_ext] 事件采集任务注册失败: %s", exc)

    # ── p0-model-gateway：分析层地基任务（账本到期评分 / 配额日快照）──
    try:
        from backend.services.analysis.scheduling import register_base_jobs
        registered.extend(register_base_jobs(task_scheduler, wrap, job))
    except Exception as exc:
        logger.warning("[v3_jobs_ext] 分析层任务注册失败: %s", exc)

    # ── p1-deep-analysis：日度简报 / 周复盘 / 择时 / 事件扫描（非峰时）──
    try:
        from backend.services.analysis.scheduling import register_phase1_jobs
        registered.extend(register_phase1_jobs(task_scheduler, wrap, job))
    except Exception as exc:
        logger.warning("[v3_jobs_ext] 深度分析 Phase1 任务注册失败: %s", exc)

    # ── p1-agents-a / p2-agents-b：六个 Agent 观察模式（评分器无论开关都会挂载）──
    try:
        from backend.services.agents.jobs import register_agent_jobs
        registered.extend(register_agent_jobs(task_scheduler, wrap, job))
    except Exception as exc:
        logger.warning("[v3_jobs_ext] Agent 群任务注册失败: %s", exc)

    # ── p2-agents-b：实验卡生命周期（独立于 AGENTS_ENABLED，已落库的卡片必须走完）──
    try:
        from backend.services.agents.jobs import register_experiment_jobs
        registered.extend(register_experiment_jobs(task_scheduler, wrap, job))
    except Exception as exc:
        logger.warning("[v3_jobs_ext] 实验卡生命周期任务注册失败: %s", exc)

    # ── p2-event-strategies：E5-2/E5-3/E5-5 影子车道扫描 + 每日 KPI（只入 signal_ledger，不下单）──
    try:
        from backend.services.strategies.event.jobs import register_e5_jobs
        registered.extend(register_e5_jobs(task_scheduler, wrap, job))
    except Exception as exc:
        logger.warning("[v3_jobs_ext] E5 事件影子策略任务注册失败: %s", exc)

    # ── p2-oms-exec：悬挂单扫描 + 订单级日对账（独立于 EXEC_ALGO_ENABLED）──
    try:
        from backend.services.oms.jobs import register_oms_jobs
        registered.extend(register_oms_jobs(task_scheduler, wrap, job))
    except Exception as exc:
        logger.warning("[v3_jobs_ext] OMS 任务注册失败: %s", exc)

    # ── p2-arb-infra：套利 scorecard + carry 小资金（研究桶）──
    try:
        from backend.services.arbitrage.jobs import register_arb_infra_jobs
        registered.extend(register_arb_infra_jobs(task_scheduler, wrap, job))
    except Exception as exc:
        logger.warning("[v3_jobs_ext] arb-infra 任务注册失败: %s", exc)

    # ── p3-promotion：资本分配 / 尾部 funding / 付费决策 / F4 / E5 研究桶 ──
    try:
        from backend.services.allocation.jobs import register_promotion_jobs
        registered.extend(register_promotion_jobs(task_scheduler, wrap, job))
    except Exception as exc:
        logger.warning("[v3_jobs_ext] promotion 任务注册失败: %s", exc)

    # ── p1-trend-engine：E1 趋势 sleeve 日任务。关着就不注册，避免空转占 cron。──
    try:
        from backend.services.trend_e1_engine import e1_enabled as _e1_on
        from backend.services.trend_e1_engine import scheduled_job as _e1_job
        if not _e1_on():
            logger.info("[v3_jobs_ext] TREND_E1_ENABLED=false，跳过 trend_e1_daily 注册")
        else:
            from backend.services.analysis.scheduling import _tz_offset_h
            _h = (0 + int(_tz_offset_h())) % 24
            job("trend_e1_daily", f"cron {_h:02d}:20 (00:20 UTC)",
                "E1 主流 8 币趋势引擎：日线收盘触发，ema_stack 入场 / Chandelier 3×ATR20 出场 / vol-target 35%，写 trend_drift",
                owner="trend", runner=_e1_job, expected_interval_sec=86400)
            task_scheduler.add_cron_task(task_func=wrap("trend_e1_daily", _e1_job), task_id="v3_trend_e1_daily",
                                         hour=_h, minute=20)
            registered.append("trend_e1_daily")
    except Exception as exc:
        logger.warning("[v3_jobs_ext] trend_e1_daily 注册失败: %s", exc)

    # ── p1-cashflow：E2a T&E / E2b carry 模拟 / 闲置理财 / 返佣配置快照 ──
    try:
        from backend.services.cashflow.jobs import register_cashflow_jobs
        registered.extend(register_cashflow_jobs(task_scheduler, wrap, job))
    except Exception as exc:
        logger.warning("[v3_jobs_ext] cashflow 任务注册失败: %s", exc)

    return registered
