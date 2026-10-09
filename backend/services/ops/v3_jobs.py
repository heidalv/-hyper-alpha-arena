# -*- coding: utf-8 -*-
"""v3 定时任务集中注册（Phase 0 起所有新任务都在这里登记，不再散落 main.py）。

每个任务：名称、节奏、入口函数、简述。注册经 task_scheduler（APScheduler），
并同步登记到 job_registry（p0-jobs-alerts）以便 /api/ops/jobs 展示心跳与失败。

时间口径：本地时间；深度 LLM 任务避开 GLM 峰时（工作日 14:00–18:00）。
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)


def _wrap(job_name: str, fn: Callable[..., Any]) -> Callable[..., Any]:
    """任务包装：心跳/失败登记到 job_registry（若不可用则只打日志）。"""

    def _runner(*args, **kwargs):
        try:
            from backend.services.ops.job_registry import job_run
        except Exception:
            job_run = None
        if job_run is None:
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                logger.exception("[v3_jobs] %s 失败: %s", job_name, exc)
                return None
        with job_run(job_name) as rec:
            if getattr(rec, "skipped", False):
                return None   # job_registry.enabled=false → 跳过本次执行
            out = fn(*args, **kwargs)
            rec.set_result(out)
            return out

    _runner.__name__ = f"v3_{job_name}"
    return _runner


JOBS: List[Dict[str, Any]] = []


def _job(name: str, cadence: str, description: str, owner: str = "v3",
         runner: Callable[..., Any] = None, expected_interval_sec: int = None) -> Dict[str, Any]:
    """登记任务元数据到进程内列表 + job_registry 表（含可手动触发的 runner）。"""
    spec = {"name": name, "cadence": cadence, "description": description, "owner": owner}
    JOBS.append(spec)
    try:
        from backend.services.ops.job_registry import register_job
        register_job(name, cadence, description, owner=owner,
                     expected_interval_sec=expected_interval_sec, runner=runner)
    except Exception as exc:
        logger.debug("[v3_jobs] register_job %s 失败: %s", name, exc)
    return spec


def register_v3_jobs() -> List[str]:
    """在 main.py 启动阶段调用一次；返回已注册任务名。幂等（replace_existing）。"""
    from backend.services.scheduler import task_scheduler

    registered: List[str] = []
    task_scheduler.start()

    # ── F2c：trade_facts 日对账回填（每日 03:50，回看 3 天）──
    try:
        from backend.services.ledger.trade_facts_reconcile import run_reconcile_job
        _job("trade_facts_reconcile", "daily 03:50", "回填缺失的 trade_facts 学习样本（回看 3 天）",
             owner="ledger", runner=lambda: run_reconcile_job(days=3), expected_interval_sec=24 * 3600)
        task_scheduler.add_cron_task(
            task_func=_wrap("trade_facts_reconcile", run_reconcile_job),
            hour=3, minute=50, task_id="v3_trade_facts_reconcile", max_instances=1, days=3,
        )
        registered.append("trade_facts_reconcile")
    except Exception as exc:
        logger.warning("[v3_jobs] trade_facts_reconcile 注册失败: %s", exc)

    # ── F2a：边际账本日快照（每日 04:10，14 天窗口）──
    try:
        from backend.services.ledger.edge_ledger import run_daily_snapshot_job
        _job("edge_ledger_snapshot", "daily 04:10", "各 paper 账户 14 天边际账本快照落库",
             owner="ledger", runner=lambda: run_daily_snapshot_job(days=14), expected_interval_sec=24 * 3600)
        task_scheduler.add_cron_task(
            task_func=_wrap("edge_ledger_snapshot", run_daily_snapshot_job),
            hour=4, minute=10, task_id="v3_edge_ledger_snapshot", max_instances=1, days=14,
        )
        registered.append("edge_ledger_snapshot")
    except Exception as exc:
        logger.warning("[v3_jobs] edge_ledger_snapshot 注册失败: %s", exc)

    # ── [P2 大轮回 2026-09-27] 出场后 60 分钟观察（§9.3，30 分钟节奏）──
    try:
        from backend.services.ops.exit_markout import run_exit_markout
        _job("exit_markout_30m", "interval 1800s",
             "出场后 1h/4h 价格路径观察（§9.3 卖飞/躲过标记）",
             owner="p2", runner=run_exit_markout, expected_interval_sec=1800)
        task_scheduler.add_interval_task(
            task_func=_wrap("exit_markout_30m", run_exit_markout),
            interval_seconds=1800, task_id="v3_exit_markout_30m", max_instances=1,
        )
        registered.append("exit_markout_30m")
    except Exception as exc:
        logger.warning("[v3_jobs] exit_markout_30m 注册失败: %s", exc)

    # ── [P0 大轮回 2026-09-27] 持仓逐 5 分钟快照（§10.1 断档门禁数据源）──
    try:
        from backend.services.ops.position_snapshotter import snapshot_open_positions
        _job("position_snapshot_5m", "interval 300s",
             "持仓逐 5 分钟快照（position_snapshots，幂等桶；§10.3 断档门禁数据源）",
             owner="p0", runner=snapshot_open_positions, expected_interval_sec=300)
        task_scheduler.add_interval_task(
            task_func=_wrap("position_snapshot_5m", snapshot_open_positions),
            interval_seconds=300, task_id="v3_position_snapshot_5m", max_instances=1,
        )
        registered.append("position_snapshot_5m")
    except Exception as exc:
        logger.warning("[v3_jobs] position_snapshot_5m 注册失败: %s", exc)

    # ── [P0 大轮回 2026-09-27] 数据契约每日质量门禁（每日 05:10）──
    try:
        from backend.services.ops.p0_data_contract_audit import run_p0_audit
        _job("p0_data_contract_audit", "daily 05:10",
             "P0 数据契约质量门禁（决策关联/快照/thesis/资金费/滑点/学习比率/K线新鲜度，§10.3）",
             owner="p0", runner=lambda: run_p0_audit(days=1), expected_interval_sec=24 * 3600)
        task_scheduler.add_cron_task(
            task_func=_wrap("p0_data_contract_audit", run_p0_audit),
            hour=5, minute=10, task_id="v3_p0_data_contract_audit", max_instances=1, days=1,
        )
        registered.append("p0_data_contract_audit")
    except Exception as exc:
        logger.warning("[v3_jobs] p0_data_contract_audit 注册失败: %s", exc)

    # ── [P0 大轮回 2026-09-27] 资金费逐仓回填每日任务（每日 05:00，近 7 天增量，幂等）──
    try:
        from scripts.backfill_position_funding import main as _funding_main
        def _run_funding_backfill() -> dict:
            out = _funding_main(["--days", "7", "--apply"])
            return {"exit": int(out or 0)}
        _job("position_funding_backfill", "daily 05:00",
             "position_funding_events 增量回填 + paper_positions.funding_paid/received 重算（幂等）",
             owner="data", runner=_run_funding_backfill, expected_interval_sec=24 * 3600)
        task_scheduler.add_cron_task(
            task_func=_wrap("position_funding_backfill", _run_funding_backfill),
            hour=5, minute=0, task_id="v3_position_funding_backfill", max_instances=1,
        )
        registered.append("position_funding_backfill")
    except Exception as exc:
        logger.warning("[v3_jobs] position_funding_backfill 注册失败: %s", exc)

    # ── 后续 Phase 的任务在此追加（RiskEngine 巡检、数据采集、双模型简报…）──
    # [M8 2026-09-14] 原裸 `except ImportError: pass` 可静默杀掉全部扩展定时任务
    # （审计实证：ops/v3_jobs.py:96-97）。改为 WARNING 可见 + 注册计数归零可见。
    try:
        from backend.services.ops.v3_jobs_ext import register_extended_jobs
        registered.extend(register_extended_jobs(task_scheduler, _wrap, _job))
    except ImportError as exc:
        logger.warning(
            "[v3_jobs] v3_jobs_ext 导入失败（扩展任务全部未注册）: %s", exc
        )
    except Exception as exc:
        logger.warning("[v3_jobs] 扩展任务注册失败: %s", exc)

    logger.info("[v3_jobs] 已注册 %d 个任务: %s", len(registered), ", ".join(registered))
    return registered
