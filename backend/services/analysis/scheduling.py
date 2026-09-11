# -*- coding: utf-8 -*-
"""非峰时调度助手 + 分析层基础定时任务注册（p0-model-gateway）。

GLM Coding Plan 峰时 = 工作日 14:00–18:00（北京时间，消耗 3×）。所有定时深度任务用 off_peak_cron()
取 cron 参数，保证落在非峰时；即便被手动触发落在峰时，QuotaGuard 也会把 GLM 传输判 degrade。

方案节奏：日度简报（自动，非峰时）、周度复盘（周一 04:00）、事件评估（事件触发，QuotaGuard 限 20 次/日/模型）。
本模块在 p0 只注册两项地基任务：
  analysis_ledger_scoring   每 15 分钟：signal_ledger / agent_predictions 到期评分
  analysis_quota_daily      每日 00:05：把 QuotaGuard 快照写日志（看板从 /api/analysis/quota 实时读）
Phase 1 的日度简报 / 周复盘 / 事件评估在 analysis/tasks.py 中实现并经 register_phase1_jobs() 注册。
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from backend.services.analysis.quota_guard import is_glm_peak, next_off_peak

logger = logging.getLogger(__name__)

# 方案拍板的默认时刻（北京时间）
DAILY_BRIEF_HOUR_LOCAL = 7      # 07:00 亚盘前，非峰时
WEEKLY_REVIEW_HOUR_LOCAL = 4    # 周一 04:00
TIMING_HOUR_LOCAL = 5           # 周一 05:00（择时紧随复盘）
# [2026-09-05] 图审是附加证据：默认 8h 扫一轮，新鲜窗 8h（见 ANALYSIS_TREND_CHART_*）。


def _tz_offset_h() -> int:
    import os

    try:
        return int(os.getenv("ANALYSIS_GLM_PEAK_TZ_OFFSET_H", "8"))
    except Exception:
        return 8


def local_hour_to_scheduler_hour(hour_local: int) -> int:
    """把北京时间小时换算成调度器进程本地时间的小时（调度器用系统本地时区）。"""
    now_utc = datetime.now(timezone.utc)
    local_now = now_utc.astimezone()  # 系统本地
    sys_off_h = int((local_now.utcoffset() or timedelta(0)).total_seconds() // 3600)
    return (hour_local - _tz_offset_h() + sys_off_h) % 24


def off_peak_cron(hour_local: int, minute: int = 0, day_of_week: Optional[str] = None) -> Dict[str, Any]:
    """返回 add_cron_task 的关键字参数；若给定时刻落在峰时，顺延到 18:05。"""
    probe = datetime.now(timezone(timedelta(hours=_tz_offset_h()))).replace(hour=hour_local, minute=minute, second=0, microsecond=0)
    if day_of_week is None or day_of_week in ("mon-fri", "mon,tue,wed,thu,fri"):
        # 工作日才有峰时；取一个工作日探测
        while probe.weekday() >= 5:
            probe += timedelta(days=1)
    if is_glm_peak(probe):
        shifted = next_off_peak(probe) + timedelta(minutes=5)
        hour_local, minute = shifted.hour, shifted.minute
        logger.info("[analysis.scheduling] %02d:00 落在 GLM 峰时，顺延到 %02d:%02d", probe.hour, hour_local, minute)
    out = {"hour": local_hour_to_scheduler_hour(hour_local), "minute": minute}
    if day_of_week:
        out["day_of_week"] = day_of_week
    return out


def score_due_with_agents() -> Dict[str, Any]:
    """到期评分入口：先确保 Agent 的 kind 评估器已挂载，再评分。

    `score_due` 对未注册 kind 的预测会一直挂 open、48h 后判 void；Agent 定时任务可能因
    `AGENTS_ENABLED=false` 而不注册，所以评分任务自己兜底挂一次评估器（幂等）。
    """
    from backend.services.analysis.ledgers import score_due

    try:
        from backend.services.agents.jobs import ensure_evaluators

        ensure_evaluators()
    except Exception as exc:
        logger.warning("[analysis.scheduling] Agent 评分器挂载失败（本轮只评信号）: %s", exc)
    return score_due()


def register_base_jobs(task_scheduler, wrap: Callable[..., Any], job: Callable[..., Any]) -> List[str]:
    registered: List[str] = []
    try:
        job("analysis_ledger_scoring", "interval 900s", "signal_ledger / agent_predictions 到期评分（命中/超额/Brier）",
            owner="analysis", runner=score_due_with_agents, expected_interval_sec=900)
        task_scheduler.add_interval_task(
            task_func=wrap("analysis_ledger_scoring", score_due_with_agents),
            interval_seconds=900, task_id="v3_analysis_ledger_scoring", max_instances=1,
        )
        registered.append("analysis_ledger_scoring")
    except Exception as exc:
        logger.warning("[analysis.scheduling] analysis_ledger_scoring 注册失败: %s", exc)

    try:
        from backend.services.analysis.quota_guard import get_quota_guard

        def _quota_daily() -> Dict[str, Any]:
            snap = get_quota_guard().snapshot()
            logger.info("[QuotaGuard] 日快照: %s", {k: v.get("calls_week") for k, v in snap.get("transports", {}).items()})
            return {"transports": {k: v.get("calls_week") for k, v in snap.get("transports", {}).items()}}

        cron = off_peak_cron(0, 5)
        job("analysis_quota_daily", f"cron {cron['hour']:02d}:{cron['minute']:02d}", "QuotaGuard 用量日快照（配额余量看板）",
            owner="analysis", runner=_quota_daily, expected_interval_sec=86400)
        task_scheduler.add_cron_task(task_func=wrap("analysis_quota_daily", _quota_daily), task_id="v3_analysis_quota_daily",
                                     hour=cron["hour"], minute=cron["minute"])
        registered.append("analysis_quota_daily")
    except Exception as exc:
        logger.warning("[analysis.scheduling] analysis_quota_daily 注册失败: %s", exc)
    return registered


def register_phase1_jobs(task_scheduler, wrap: Callable[..., Any], job: Callable[..., Any]) -> List[str]:
    """p1-deep-analysis：日度简报 / 周度复盘 / 择时 / 事件扫描。全部非峰时 cron。"""
    registered: List[str] = []
    try:
        from backend.services.analysis import tasks as analysis_tasks

        # 日度简报：北京时间 07:00
        cron = off_peak_cron(DAILY_BRIEF_HOUR_LOCAL, 0)
        job(
            "analysis_daily_brief",
            f"cron {cron['hour']:02d}:{cron['minute']:02d} (BJ {DAILY_BRIEF_HOUR_LOCAL}:00)",
            "双模型日度 regime/风险简报 → consensus≥0.7 入 signal_ledger + 桶权重建议",
            owner="analysis", runner=analysis_tasks.scheduled_daily_brief, expected_interval_sec=86400,
        )
        task_scheduler.add_cron_task(
            task_func=wrap("analysis_daily_brief", analysis_tasks.scheduled_daily_brief),
            task_id="v3_analysis_daily_brief", hour=cron["hour"], minute=cron["minute"],
        )
        registered.append("analysis_daily_brief")

        # 周度复盘 / 择时：默认不注册（研究报告不控仓，占配额）。要开设 ANALYSIS_WEEKLY_ENABLED / ANALYSIS_TIMING_ENABLED。
        _weekly_on = os.getenv("ANALYSIS_WEEKLY_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")
        _timing_on = os.getenv("ANALYSIS_TIMING_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")

        if _weekly_on:
            cron_w = off_peak_cron(WEEKLY_REVIEW_HOUR_LOCAL, 0, day_of_week="mon")
            job(
                "analysis_weekly_review",
                f"cron mon {cron_w['hour']:02d}:{cron_w['minute']:02d} (BJ {WEEKLY_REVIEW_HOUR_LOCAL}:00)",
                "双模型周度复盘 → 实验卡（proposed）写入 experiments 表",
                owner="analysis", runner=analysis_tasks.scheduled_weekly_review, expected_interval_sec=604800,
            )
            kw_w = {"task_func": wrap("analysis_weekly_review", analysis_tasks.scheduled_weekly_review),
                    "task_id": "v3_analysis_weekly_review", "hour": cron_w["hour"], "minute": cron_w["minute"]}
            if cron_w.get("day_of_week"):
                kw_w["day_of_week"] = cron_w["day_of_week"]
            task_scheduler.add_cron_task(**kw_w)
            registered.append("analysis_weekly_review")
        else:
            logger.info("[analysis.scheduling] analysis_weekly_review 跳过（ANALYSIS_WEEKLY_ENABLED=false）")

        if _timing_on:
            cron_t = off_peak_cron(TIMING_HOUR_LOCAL, 0, day_of_week="mon")
            job(
                "analysis_timing",
                f"cron mon {cron_t['hour']:02d}:{cron_t['minute']:02d} (BJ {TIMING_HOUR_LOCAL}:00)",
                "双模型策略择时：三桶权重 + E1/E2/E5/E3 资本建议 + 反事实",
                owner="analysis", runner=analysis_tasks.scheduled_timing, expected_interval_sec=604800,
            )
            kw_t = {"task_func": wrap("analysis_timing", analysis_tasks.scheduled_timing),
                    "task_id": "v3_analysis_timing", "hour": cron_t["hour"], "minute": cron_t["minute"]}
            if cron_t.get("day_of_week"):
                kw_t["day_of_week"] = cron_t["day_of_week"]
            task_scheduler.add_cron_task(**kw_t)
            registered.append("analysis_timing")
        else:
            logger.info("[analysis.scheduling] analysis_timing 跳过（ANALYSIS_TIMING_ENABLED=false）")

        # 多模态趋势图审：按间隔扫（默认 8h）。缺图不挡文字论题，也不挡开仓。
        chart_sec = analysis_tasks.trend_chart_interval_sec()
        job(
            "analysis_trend_chart",
            f"interval {chart_sec}s",
            "多模态趋势图审：缺图/过期优先，新鲜窗内跳过；双票+仲裁入 signal_ledger",
            owner="analysis", runner=analysis_tasks.scheduled_trend_chart_review, expected_interval_sec=chart_sec,
        )
        task_scheduler.add_interval_task(
            task_func=wrap("analysis_trend_chart", analysis_tasks.scheduled_trend_chart_review),
            interval_seconds=chart_sec, task_id="v3_analysis_trend_chart", max_instances=1,
        )
        registered.append("analysis_trend_chart")

        # 事件扫描：间隔可配（默认 15min；原硬编码 30min）
        # [2026-09-04] 云端配额放宽后默认收紧到 900s，日批上限另见 ANALYSIS_EVENT_BATCH_LIMIT
        scan_sec = max(300, int(os.getenv("ANALYSIS_EVENT_SCAN_SEC", "900")))
        job(
            "analysis_event_scan",
            f"interval {scan_sec}s",
            "高严重度 market_events 双模型冲击评估（未评估优先，日批上限 ANALYSIS_EVENT_BATCH_LIMIT）",
            owner="analysis", runner=analysis_tasks.scheduled_event_scan, expected_interval_sec=scan_sec,
        )
        task_scheduler.add_interval_task(
            task_func=wrap("analysis_event_scan", analysis_tasks.scheduled_event_scan),
            interval_seconds=scan_sec, task_id="v3_analysis_event_scan", max_instances=1,
        )
        registered.append("analysis_event_scan")
    except Exception as exc:
        logger.warning("[analysis.scheduling] register_phase1_jobs 失败: %s", exc)

    # 事件冲击回测：每天 BJ 06:00（非峰时，只读 K 线 + market_events，不调 LLM）
    try:
        from backend.research.event_study import scheduled_job as _es_job

        cron_es = off_peak_cron(6, 0)
        job(
            "event_study_daily",
            f"cron {cron_es['hour']:02d}:{cron_es['minute']:02d} (BJ 06:00)",
            "全类型 market_events 冲击回测：超额路径 / Wilson / 最优持有 / 显著性门，写 event_study/latest.json",
            owner="research", runner=_es_job, expected_interval_sec=86400,
        )
        task_scheduler.add_cron_task(
            task_func=wrap("event_study_daily", _es_job),
            task_id="v3_event_study_daily", hour=cron_es["hour"], minute=cron_es["minute"],
        )
        registered.append("event_study_daily")
    except Exception as exc:
        logger.warning("[analysis.scheduling] event_study_daily 注册失败: %s", exc)
    return registered
