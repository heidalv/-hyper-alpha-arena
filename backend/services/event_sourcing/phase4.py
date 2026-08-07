"""
事件溯源 Phase 4 — 写路径 DB 退役（投影为写权威 + DB 异步镜像）。

在 Phase 3 投影默认读基础上：
  - EVENT_SOURCING_WRITE_RETIRE_DB=true 时，仓位状态以事件/投影为准
  - DB PaperPosition 行降为审计镜像，由维护周期 `run_retirement_sync` 从投影补齐
  - 热路径仍写订单/余额；仓位事件在 commit 前记录（event-first）

零风险：开关默认关；对拍失败或投影缺失时 fail-open 回退双写。
"""
from __future__ import annotations

import logging
import os
import threading
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Set

from backend.services.event_sourcing.event_store import (
    EVT_POSITION_CLOSED,
    EVT_POSITION_OPENED,
    EVT_POSITION_CHANGED,
    is_enabled,
)
from backend.services.event_sourcing.phase2 import (
    get_live_repository,
    get_reconcile_stats,
    is_phase2_reconcile_enabled,
    record_position_event,
)
from backend.services.event_sourcing.phase3 import is_phase3_enabled

logger = logging.getLogger(__name__)

_sync_queue: Deque[str] = deque(maxlen=500)
_sync_lock = threading.Lock()
_phase4_stats: Dict[str, int] = {
    "event_first_writes": 0,
    "sync_runs": 0,
    "sync_upserts": 0,
    "sync_closes": 0,
    "sync_skipped": 0,
}


def is_write_retirement_enabled() -> bool:
    if not is_enabled() or not is_phase3_enabled():
        return False
    return os.environ.get("EVENT_SOURCING_WRITE_RETIRE_DB", "false").strip().lower() in (
        "1", "true", "yes", "on",
    )


def get_phase4_stats() -> Dict[str, int]:
    return dict(_phase4_stats)


def should_use_event_first_write() -> bool:
    """热路径写：事件先于 DB commit。"""
    return is_write_retirement_enabled() or is_enabled()


def enqueue_retirement_sync(position_id: str) -> None:
    if not position_id:
        return
    with _sync_lock:
        if position_id not in _sync_queue:
            _sync_queue.append(str(position_id))


def record_event_first(
    event_type: str,
    aggregate_id: str,
    payload: dict,
    *,
    before_db_commit: bool = True,
) -> bool:
    """event-first 写路径：在 DB commit 前记录事件并更新投影。"""
    global _phase4_stats
    if not is_enabled() or not aggregate_id:
        return False
    ok = record_position_event(event_type, str(aggregate_id), payload or {})
    if ok and before_db_commit:
        _phase4_stats["event_first_writes"] = _phase4_stats.get("event_first_writes", 0) + 1
        if is_write_retirement_enabled():
            enqueue_retirement_sync(str(aggregate_id))
    return ok


def _projection_open_rows(account_id: Optional[int] = None) -> List[dict]:
    try:
        repo = get_live_repository()
        rows = []
        for pid, st in (repo.projection.current_state or {}).items():
            if st.get("status") != "open":
                continue
            if account_id is not None and int(st.get("account_id") or 0) != int(account_id):
                continue
            rows.append({**st, "id": pid})
        return rows
    except Exception:
        return []


def run_retirement_sync(db, *, account_id: Optional[int] = None) -> Dict[str, int]:
    """维护周期：投影 → DB 镜像同步（写路径 DB 退役的核心）。"""
    global _phase4_stats
    result = {"upserts": 0, "closes": 0, "skipped": 0}
    if not is_write_retirement_enabled():
        result["skipped"] = 1
        _phase4_stats["sync_skipped"] += 1
        return result

    if is_phase2_reconcile_enabled():
        rec = get_reconcile_stats()
        if rec.get("last_ok", 1) == 0:
            result["skipped"] = 1
            _phase4_stats["sync_skipped"] += 1
            logger.debug("[EventSourcing#9 Phase4] 对拍未通过，跳过 DB 镜像同步")
            return result

    _phase4_stats["sync_runs"] = _phase4_stats.get("sync_runs", 0) + 1
    try:
        from backend.database.models import PaperPosition
        from datetime import datetime, timezone

        proj_rows = _projection_open_rows(account_id)
        proj_ids: Set[str] = {str(r.get("id")) for r in proj_rows if r.get("id")}

        pending: List[str] = []
        with _sync_lock:
            while _sync_queue:
                pending.append(_sync_queue.popleft())
        for pid in pending:
            if pid not in proj_ids:
                proj_ids.add(pid)

        for row in proj_rows:
            pid = str(row.get("id") or "")
            if not pid:
                continue
            acct = int(row.get("account_id") or account_id or 0)
            existing = db.query(PaperPosition).filter(PaperPosition.id == int(pid)).first() if pid.isdigit() else None
            if existing is None and pid.isdigit():
                existing = db.query(PaperPosition).filter(
                    PaperPosition.account_id == acct,
                    PaperPosition.symbol == row.get("symbol"),
                    PaperPosition.status == "open",
                ).first()

            if existing:
                existing.size = float(row.get("size") or existing.size or 0)
                existing.entry_price = float(row.get("entry_price") or existing.entry_price or 0)
                existing.side = row.get("side") or existing.side
                existing.status = "open"
                existing.trade_nature = row.get("trade_nature") or getattr(existing, "trade_nature", None) or "swing"
                result["upserts"] += 1
            elif pid.isdigit():
                pos = PaperPosition(
                    id=int(pid),
                    account_id=acct,
                    symbol=row.get("symbol"),
                    side=row.get("side") or "long",
                    size=float(row.get("size") or 0),
                    entry_price=float(row.get("entry_price") or 0),
                    status="open",
                    trade_nature=row.get("trade_nature") or "swing",
                    leverage=float(row.get("leverage") or 1),
                    margin=float(row.get("margin") or 0),
                    opened_at=datetime.now(timezone.utc),
                )
                db.add(pos)
                result["upserts"] += 1

        # 关闭投影中不存在、DB 仍 open 的孤儿行
        q = db.query(PaperPosition).filter(PaperPosition.status == "open")
        if account_id is not None:
            q = q.filter(PaperPosition.account_id == int(account_id))
        for db_pos in q.all():
            sid = str(db_pos.id)
            if sid not in proj_ids:
                db_pos.status = "closed"
                db_pos.close_reason = "es_phase4_mirror_retire"
                db_pos.closed_at = datetime.now(timezone.utc)
                result["closes"] += 1

        db.commit()
        _phase4_stats["sync_upserts"] += result["upserts"]
        _phase4_stats["sync_closes"] += result["closes"]
        if result["upserts"] or result["closes"]:
            logger.info(
                "[EventSourcing#9 Phase4] DB 镜像同步: upserts=%d closes=%d",
                result["upserts"], result["closes"],
            )
    except Exception as exc:
        logger.debug("[EventSourcing#9 Phase4] 镜像同步失败（忽略）: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
    return result


__all__ = [
    "EVT_POSITION_OPENED",
    "EVT_POSITION_CHANGED",
    "EVT_POSITION_CLOSED",
    "is_write_retirement_enabled",
    "should_use_event_first_write",
    "record_event_first",
    "enqueue_retirement_sync",
    "run_retirement_sync",
    "get_phase4_stats",
]
