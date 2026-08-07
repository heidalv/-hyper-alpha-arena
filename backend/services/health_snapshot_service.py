"""聚合内部健康 API，供 OpenCode 系统健康 Tab 与 Alpha 助手 L1 使用。"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

Fetcher = Callable[[], Dict[str, Any]]


def _safe_call(name: str, fn: Fetcher) -> Dict[str, Any]:
    try:
        data = fn()
        return {"ok": True, "data": data}
    except Exception as exc:
        logger.debug("[HealthSnapshot] %s failed: %s", name, exc)
        return {"ok": False, "error": str(exc)}


def _fetch_api_health() -> Dict[str, Any]:
    return {"status": "ok"}


def _fetch_opencode_status() -> Dict[str, Any]:
    from backend.services.opencode_bridge import get_bridge_status, health_check
    from backend.services.paper_pace_controller import paper_pace_controller

    sidecar: Dict[str, Any] = {}
    try:
        from backend.services.opencode_sidecar import sidecar_status
        sidecar = sidecar_status()
    except Exception:
        pass
    return {
        "bridge": get_bridge_status(),
        "serve_healthy": health_check(),
        "pace": paper_pace_controller.to_dict(),
        "sidecar": sidecar,
    }


def _fetch_governor_ownership() -> Dict[str, Any]:
    from backend.services.runtime_governor import runtime_governor as gov
    return {"ownership": gov.get_ownership_map()}


def _fetch_proposal_funnel() -> Dict[str, Any]:
    import json
    from backend.database.connection import SessionLocal
    from backend.database.models import OpenCodeEvolutionProposalDB

    db = SessionLocal()
    try:
        rows = db.query(OpenCodeEvolutionProposalDB).all()
        by_status: Dict[str, int] = {}
        verdicts: Dict[str, int] = {"improved": 0, "neutral": 0, "degraded": 0, "unevaluated": 0}
        for row in rows:
            st = row.status or "unknown"
            by_status[st] = by_status.get(st, 0) + 1
            verdict = None
            try:
                verdict = json.loads(row.after_json or "{}").get("verdict")
            except Exception:
                verdict = None
            verdicts[verdict if verdict in verdicts else "unevaluated"] += 1
        total = len(rows)
        applied_like = sum(
            by_status.get(s, 0)
            for s in ("applied", "paper_applying", "paper_validated", "validating")
        )
        evaluated = verdicts["improved"] + verdicts["neutral"] + verdicts["degraded"]
        evaluated = max(
            evaluated,
            sum(by_status.get(s, 0) for s in ("paper_validated", "rolled_back", "inconclusive")),
        )
        from backend.services.proposal_validation_policy import validation_policy_for_gear

        return {
            "total": total,
            "by_status": by_status,
            "verdicts": verdicts,
            "validation_policy": validation_policy_for_gear(),
            "funnel": {
                "created": total,
                "applied": applied_like,
                "evaluated": evaluated,
                "improved": verdicts["improved"],
            },
            "improve_rate": round(verdicts["improved"] / evaluated, 3) if evaluated else None,
        }
    finally:
        db.close()


def _fetch_learning_health() -> Dict[str, Any]:
    from backend.services.learning_health_service import build_learning_health
    return build_learning_health()


def _fetch_system_log_stats() -> Dict[str, Any]:
    from backend.services.system_logger import system_logger

    all_logs = system_logger.get_logs(limit=500, min_level="WARNING")
    stats = {
        "total_logs": len(all_logs),
        "by_level": {"INFO": 0, "WARNING": 0, "ERROR": 0},
        "by_category": {"price_update": 0, "ai_decision": 0, "system_error": 0},
    }
    for entry in all_logs:
        level = entry.get("level", "INFO")
        category = entry.get("category", "system_error")
        if level in stats["by_level"]:
            stats["by_level"][level] += 1
        if category in stats["by_category"]:
            stats["by_category"][category] += 1
    return stats


def build_health_snapshot(*, timeout_sec: float = 5.0) -> Dict[str, Any]:
    """并行拉取各健康子系统；单点失败不阻断整体。"""
    fetchers: Dict[str, Fetcher] = {
        "api_health": _fetch_api_health,
        "opencode_status": _fetch_opencode_status,
        "governor_ownership": _fetch_governor_ownership,
        "proposal_funnel": _fetch_proposal_funnel,
        "learning_health": _fetch_learning_health,
        "system_log_stats": _fetch_system_log_stats,
    }

    snapshot: Dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=len(fetchers)) as pool:
        futures = {pool.submit(_safe_call, name, fn): name for name, fn in fetchers.items()}
        for fut in as_completed(futures, timeout=max(15.0, timeout_sec * len(fetchers))):
            name = futures[fut]
            try:
                snapshot[name] = fut.result(timeout=timeout_sec)
            except Exception as exc:
                snapshot[name] = {"ok": False, "error": str(exc)}

    ok_count = sum(1 for v in snapshot.values() if v.get("ok"))
    return {
        "overall_ok": ok_count == len(fetchers),
        "ok_count": ok_count,
        "total": len(fetchers),
        "apis": snapshot,
    }


def build_combined_digest(
    *,
    window_hours: int = 24,
    log_path: Optional[str] = None,
) -> Dict[str, Any]:
    from backend.services.log_digest_service import build_digest

    log_kw: Dict[str, Any] = {"window_hours": window_hours}
    if log_path:
        log_kw["log_path"] = log_path
    return {
        "log_digest": build_digest(**log_kw),
        "health_snapshot": build_health_snapshot(),
    }
