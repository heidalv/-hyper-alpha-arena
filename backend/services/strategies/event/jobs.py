# -*- coding: utf-8 -*-
"""E5 事件影子策略的定时任务（v3 方向 4，p2-event-strategies）。

  e5_shadow_scan   每 15 分钟   三个策略各扫一次近窗 → 新事件写 signal_ledger（幂等）
  e5_shadow_kpi    每日 09:10   汇总三个策略的影子 KPI 与晋升门状态，写 latest_e5_kpi.json

信号到期评分由既有的 `analysis_ledger_scoring`（每 15 分钟）负责：影子信号以
`source=<strategy_id>` 入 `signal_ledger`，`score_due()` 会按 horizon 自动结算命中/超额/Brier，
E5 这边不需要自己写评分器。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)

SCAN_INTERVAL_SEC = 900
KPI_HOUR_LOCAL = 9
KPI_MINUTE_LOCAL = 10

STRATEGY_IDS = ("e5_2_funding_shock", "e5_3_liq_cascade", "e5_5_news_hedge")


def _enabled() -> bool:
    from backend.services.strategies.event.base import env_true

    return env_true("E5_SHADOW_ENABLED", True)


def run_shadow_scan() -> Dict[str, Any]:
    """三个策略各扫一次近窗，新事件入账。"""
    from backend.core.tenant import set_system_identity
    from backend.services.strategies.event import get_strategy

    if not _enabled():
        return {"skipped": True, "reason": "E5_SHADOW_ENABLED=false"}
    set_system_identity()
    t0 = time.time()
    out: Dict[str, Any] = {"strategies": {}, "total_detected": 0, "total_recorded": 0}
    for sid in STRATEGY_IDS:
        try:
            strat = get_strategy(sid)
            if strat is None:
                out["strategies"][sid] = {"error": "未注册"}
                continue
            res = strat.shadow()
            out["strategies"][sid] = {
                "detected": res.get("detected"), "recorded": res.get("recorded"),
                "enabled": res.get("enabled"), "notes": res.get("notes"),
            }
            out["total_detected"] += int(res.get("detected") or 0)
            out["total_recorded"] += int(res.get("recorded") or 0)
        except Exception as exc:
            logger.exception("[e5.jobs] %s 影子扫描失败", sid)
            out["strategies"][sid] = {"error": str(exc)[:200]}
    out["elapsed_sec"] = round(time.time() - t0, 2)
    logger.info("[e5.jobs] 影子扫描 检出=%d 入账=%d 耗时=%.1fs",
                out["total_detected"], out["total_recorded"], out["elapsed_sec"])
    return out


def backfill_shadow(days: int = 30) -> Dict[str, Any]:
    """冷启动回填：把最近 `days` 天的历史事件补进 signal_ledger，让影子 KPI 尽快有样本。

    这些事件的评分完全是事后的（`score_due` 用 `created_ms`/`expires_ms` 对应时刻的 K 线），
    不存在前视；只是把"本可以在当时产生"的信号补记上。日常扫描仍走 24h 窗口即可。
    幂等：id 由 (strategy, symbol, event_ts) 决定，重复回填不会产生新行。
    """
    from backend.core.tenant import set_system_identity
    from backend.services.strategies.event import get_strategy

    set_system_identity()
    out: Dict[str, Any] = {"days": days, "strategies": {}, "total_recorded": 0}
    for sid in STRATEGY_IDS:
        try:
            strat = get_strategy(sid)
            if strat is None:
                continue
            res = strat.shadow(lookback_h=days * 24.0, limit=2000)
            out["strategies"][sid] = {
                "detected": res.get("detected"), "recorded": res.get("recorded"),
                "already": res.get("already_recorded"), "notes": res.get("notes"),
            }
            out["total_recorded"] += int(res.get("recorded") or 0)
        except Exception as exc:
            logger.exception("[e5.jobs] %s 回填失败", sid)
            out["strategies"][sid] = {"error": str(exc)[:200]}
    logger.info("[e5.jobs] 冷启动回填 %d 天，入账 %d 条", days, out["total_recorded"])
    return out


def run_kpi_rollup() -> Dict[str, Any]:
    """汇总影子 KPI + 晋升门，写 latest_e5_kpi.json 供看板/日报读取。"""
    from backend.core.tenant import set_system_identity
    from backend.services.strategies.event import get_strategy

    set_system_identity()
    out: Dict[str, Any] = {"generated_ms": int(time.time() * 1000), "strategies": {}}
    ready: List[str] = []
    for sid in STRATEGY_IDS:
        try:
            strat = get_strategy(sid)
            if strat is None:
                continue
            k = strat.kpi()
            out["strategies"][sid] = k
            if k.get("promotion_ready"):
                ready.append(sid)
        except Exception as exc:
            out["strategies"][sid] = {"error": str(exc)[:200]}
    out["promotion_ready"] = ready
    try:
        from backend.services.analysis.tasks import write_latest

        write_latest("e5_kpi", out)
    except Exception as exc:
        logger.debug("[e5.jobs] KPI 落盘失败: %s", exc)
    logger.info("[e5.jobs] KPI 汇总完成，过门策略: %s", ready or "无")
    return out


def register_e5_jobs(task_scheduler, wrap: Callable[..., Any], job: Callable[..., Any]) -> List[str]:
    """由 ops/v3_jobs_ext.py 的扩展注册链调用。"""
    import backend.services.strategies.event  # noqa: F401  触发策略注册

    registered: List[str] = []
    if not _enabled():
        logger.info("[e5.jobs] E5_SHADOW_ENABLED=false，跳过影子任务注册")
        return registered

    from backend.services.analysis.scheduling import off_peak_cron

    try:
        job("e5_shadow_scan", f"interval {SCAN_INTERVAL_SEC}s",
            "E5 影子车道：资金费突变 / 清算级联 / 新闻避险 三策略近窗检测 → signal_ledger（幂等，不下单）",
            owner="strategy", runner=run_shadow_scan, expected_interval_sec=SCAN_INTERVAL_SEC)
        task_scheduler.add_interval_task(
            task_func=wrap("e5_shadow_scan", run_shadow_scan),
            interval_seconds=SCAN_INTERVAL_SEC, task_id="v3_e5_shadow_scan", max_instances=1,
        )
        registered.append("e5_shadow_scan")
    except Exception as exc:
        logger.warning("[e5.jobs] e5_shadow_scan 注册失败: %s", exc)

    try:
        cron = off_peak_cron(KPI_HOUR_LOCAL, KPI_MINUTE_LOCAL)
        job("e5_shadow_kpi", f"cron {cron['hour']:02d}:{cron['minute']:02d} (BJ {KPI_HOUR_LOCAL}:{KPI_MINUTE_LOCAL:02d})",
            "E5 影子 KPI 汇总：N / 命中率 CI / 净期望下界 / 晋升门 → latest_e5_kpi.json",
            owner="strategy", runner=run_kpi_rollup, expected_interval_sec=86400)
        task_scheduler.add_cron_task(
            task_func=wrap("e5_shadow_kpi", run_kpi_rollup),
            task_id="v3_e5_shadow_kpi", hour=cron["hour"], minute=cron["minute"],
        )
        registered.append("e5_shadow_kpi")
    except Exception as exc:
        logger.warning("[e5.jobs] e5_shadow_kpi 注册失败: %s", exc)

    logger.info("[e5.jobs] 定时任务已注册: %s", registered)
    return registered
