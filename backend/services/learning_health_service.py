"""学习闭环健康检查 — 同步版，供 health_snapshot 与 API 共用。"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text


def _health_item(
    name: str,
    label: str,
    last_activity: Optional[datetime],
    *,
    threshold_hours: float,
    detail: str = "",
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    if last_activity is None:
        return {
            "name": name,
            "label": label,
            "status": "dead",
            "last_activity": None,
            "age_hours": None,
            "threshold_hours": threshold_hours,
            "detail": detail or "无活动记录",
        }
    if last_activity.tzinfo is None:
        last_activity = last_activity.replace(tzinfo=timezone.utc)
    age_h = (now - last_activity).total_seconds() / 3600.0
    if age_h <= threshold_hours:
        status = "ok"
    elif age_h <= threshold_hours * 2:
        status = "warn"
    else:
        status = "dead"
    return {
        "name": name,
        "label": label,
        "status": status,
        "last_activity": last_activity.isoformat(),
        "age_hours": round(age_h, 1),
        "threshold_hours": threshold_hours,
        "detail": detail,
    }


def build_learning_health() -> Dict[str, Any]:
    """同步构建学习进化各闭环健康状态。"""
    items: List[Dict[str, Any]] = []

    try:
        from backend.database.connection import AnalyticsSessionLocal

        ana = AnalyticsSessionLocal()
        try:
            row = ana.execute(
                text("SELECT count(*), max(created_at) FROM decision_retrospectives")
            ).fetchone()
            cnt, last = (row[0], row[1]) if row else (0, None)
        finally:
            ana.close()
        items.append(
            _health_item(
                "retrospective", "决策复盘", last, threshold_hours=48,
                detail=f"累计 {cnt} 条复盘记录",
            )
        )
    except Exception as exc:
        items.append({"name": "retrospective", "label": "决策复盘", "status": "dead", "detail": str(exc)})

    try:
        from backend.database.connection import SessionLocal

        db = SessionLocal()
        try:
            row = db.execute(
                text("SELECT count(*), max(created_at) FROM backtest_runs")
            ).fetchone()
            cnt, last = (row[0], row[1]) if row else (0, None)
        finally:
            db.close()
        items.append(
            _health_item(
                "evolution", "参数进化(NSGA-II)", last, threshold_hours=96,
                detail=f"累计 {cnt} 次进化回测",
            )
        )
    except Exception as exc:
        items.append({"name": "evolution", "label": "参数进化(NSGA-II)", "status": "dead", "detail": str(exc)})

    try:
        # v5_runtime_gates.json 已废弃；门槛现由 RuntimeGovernor → runtime_tuning.json 统一下发
        gate_files = (
            os.path.join("data", "runtime_tuning.json"),
            os.path.join("data", "runtime_tuning_intents.json"),
            os.path.join("data", "runtime_governor_decisions.jsonl"),
        )
        last = None
        for gates_file in gate_files:
            if os.path.exists(gates_file):
                mtime = datetime.fromtimestamp(os.path.getmtime(gates_file), tz=timezone.utc)
                if last is None or mtime > last:
                    last = mtime
        items.append(
            _health_item(
                "runtime_gates", "运行时门槛(runtime_tuning)", last, threshold_hours=48,
                detail="RuntimeGovernor/runtime_tuning 统一下发，决策核心 60s 内生效",
            )
        )
    except Exception as exc:
        items.append({"name": "runtime_gates", "label": "运行时门槛(runtime_tuning)", "status": "dead", "detail": str(exc)})

    try:
        from backend.database.connection import SessionLocal

        db = SessionLocal()
        try:
            row = db.execute(text("SELECT max(timestamp) FROM multi_symbol_kelly")).fetchone()
            last = row[0] if row else None
        finally:
            db.close()
        items.append(
            _health_item("kelly", "Kelly 仓位聚合", last, threshold_hours=2, detail="LearningLoop 每 30min 聚合")
        )
    except Exception as exc:
        items.append({"name": "kelly", "label": "Kelly 仓位聚合", "status": "dead", "detail": str(exc)})

    try:
        from backend.database.connection import SessionLocal

        db = SessionLocal()
        try:
            row = db.execute(text("SELECT max(ts) FROM coordinator_actions")).fetchone()
            last = row[0] if row else None
        finally:
            db.close()
        items.append(
            _health_item("coordinator", "系统协调器", last, threshold_hours=2, detail="LearningLoop 每 1h 协调检查")
        )
    except Exception as exc:
        items.append({"name": "coordinator", "label": "系统协调器", "status": "dead", "detail": str(exc)})

    try:
        from backend.database.connection import SessionLocal

        db = SessionLocal()
        try:
            row = db.execute(
                text("SELECT count(*), max(updated_at) FROM strategy_memories")
            ).fetchone()
            cnt, last = (row[0], row[1]) if row else (0, None)
        finally:
            db.close()
        items.append(
            _health_item(
                "strategy_memory", "策略记忆/教训", last, threshold_hours=72,
                detail=f"累计 {cnt} 条策略记忆",
            )
        )
    except Exception as exc:
        items.append({"name": "strategy_memory", "label": "策略记忆/教训", "status": "dead", "detail": str(exc)})

    # ── [2026-08-05 v6 8.3 阶段1] LearningLoop 5 条闭环：最后活动时间超时标红 ──
    # 数据源：LearningLoopService.last_tick_map()（每 tick 成功后 _record_tick 更新）。
    # 阈值 = 间隔的约 3 倍：ok ≤ 阈值；warn ≤ 2×阈值；超过即 dead（瘫痪可视化）。
    try:
        from backend.services.learning_loop_service import (
            JOB_COORDINATOR,
            JOB_HEARTBEAT,
            JOB_KELLY_PORTFOLIO,
            JOB_OUTCOME_BATCH,
            JOB_PAPER_OUTCOME_BACKFILL,
            learning_loop,
        )
        loop_last = learning_loop.last_tick_map()
        loop_spec = [
            (JOB_OUTCOME_BATCH, "loop_outcome_batch", "闭环-结果批处理", 0.5,
             "间隔 5min：结算→绩效矩阵反哺"),
            (JOB_PAPER_OUTCOME_BACKFILL, "loop_paper_backfill", "闭环-paper补偿", 1.0,
             "间隔 10min：paper 平仓→补写学习结果"),
            (JOB_KELLY_PORTFOLIO, "loop_kelly", "闭环-Kelly聚合", 2.0,
             "间隔 30min：组合 Kelly 快照"),
            (JOB_COORDINATOR, "loop_coordinator", "闭环-系统协调器", 4.0,
             "间隔 1h：进化/DRL/Kelly 路由"),
            (JOB_HEARTBEAT, "loop_heartbeat", "闭环-WS心跳", 0.1,
             "间隔 30s：coordinator_status 广播"),
        ]
        for job_id, name, label, thr, detail in loop_spec:
            last_iso = loop_last.get(job_id)
            last_dt = None
            if last_iso:
                try:
                    last_dt = datetime.fromisoformat(last_iso)
                except (ValueError, TypeError):
                    last_dt = None
            items.append(
                _health_item(name, label, last_dt, threshold_hours=thr, detail=detail)
            )
    except Exception as exc:
        items.append({
            "name": "learning_loop", "label": "闭环-LearningLoop",
            "status": "dead", "detail": str(exc),
        })

    statuses = [it.get("status") for it in items]
    overall = "dead" if "dead" in statuses else ("warn" if "warn" in statuses else "ok")
    return {
        "items": items,
        "overall": overall,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
