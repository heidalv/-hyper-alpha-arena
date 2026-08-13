"""
健康总览聚合服务（R6-2）

设计原则（对齐 learning_health_routes 的既有约定）：
    - 只读、只聚合真实数据源，绝不伪造；子源失败如实返回 {ok: false, error}。
    - 全部子源懒加载 + try/except 隔离：任一子源故障不影响整体返回。
    - 该端点被 ops 页 60s 轮询，内部只做轻量查询/内存快照读取。

对应接口：GET /api/system/health-overview（system_control_routes.py）
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from datetime import datetime, timezone
from typing import Any, Dict

logger = logging.getLogger(__name__)

# 进程启动时刻（uptime 基准）
_START_TS = time.time()


def _db_ping() -> Dict[str, Any]:
    try:
        from sqlalchemy import text
        from backend.database.connection import SessionLocal
        t0 = time.time()
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        return {"ok": True, "latency_ms": round((time.time() - t0) * 1000, 1)}
    except Exception as exc:  # pragma: no cover
        logger.warning("[health-overview] db ping failed: %s", exc)
        return {"ok": False, "latency_ms": None, "error": str(exc)[:200]}


def _errors_counts() -> Dict[str, Any]:
    try:
        from backend.api.ops_routes import ops_errors
        counts = ops_errors(limit=50).get("counts", {})
        return {"ok": True, "counts": counts}
    except Exception as exc:  # pragma: no cover
        logger.warning("[health-overview] errors aggregation failed: %s", exc)
        return {"ok": False, "error": str(exc)[:200]}


def _learning_health() -> Dict[str, Any]:
    try:
        from backend.services.learning_health_service import build_learning_health
        return build_learning_health()
    except Exception as exc:  # pragma: no cover
        logger.warning("[health-overview] learning health failed: %s", exc)
        return {"overall": "unknown", "error": str(exc)[:200]}


def _funding_collector() -> Dict[str, Any]:
    try:
        from backend.services import multi_venue_funding_collector as mvc
        report = mvc.get_last_report()
        if not report:
            return {"has_report": False, "venue_report": {}}
        return {
            "has_report": True,
            "as_of_iso": report.get("as_of_iso"),
            "venue_report": report.get("venue_report", {}),
            "consecutive_failures": dict(getattr(mvc, "_CONSEC_FAIL", {})),
        }
    except Exception as exc:  # pragma: no cover
        logger.warning("[health-overview] funding collector failed: %s", exc)
        return {"has_report": False, "error": str(exc)[:200]}


def _resources() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    try:
        total, _used, free = shutil.disk_usage(os.getcwd())
        out["disk_free_mb"] = round(free / 1e6, 1)
        out["disk_total_mb"] = round(total / 1e6, 1)
    except Exception as exc:  # pragma: no cover
        out["disk_error"] = str(exc)[:200]
    try:
        import psutil  # 可选依赖：不可用时跳过
        out["mem_rss_mb"] = round(psutil.Process().memory_info().rss / 1e6, 1)
        out["cpu_pct"] = psutil.cpu_percent(interval=None)
    except Exception:
        pass
    return out


def build_health_overview() -> Dict[str, Any]:
    """聚合健康总览。所有子源均为只读快照。"""
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "uptime_sec": round(time.time() - _START_TS, 1),
        "db": _db_ping(),
        "errors": _errors_counts(),
        "learning_loops": _learning_health(),
        "funding_collector": _funding_collector(),
        "resources": _resources(),
    }
