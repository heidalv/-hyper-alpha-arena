"""
事件溯源 Phase 3 — 投影为默认读路径 + 启动时 DB→事件引导 + 崩溃恢复。

在 Phase 2 双写/对拍基础上：
  - EVENT_SOURCING_PHASE3=true 时 get_positions 默认走投影（等同 PHASE2_READ 常开）
  - 进程启动时：先 replay 事件日志，再将 DB 中「投影缺失」的 open 仓位 bootstrap 进事件流
  - 对拍仍 fail-open 回退 DB，不阻断交易

写路径仍双写 DB + 事件（Phase 3 不写路径革命，避免一次性破坏过大）。
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from backend.services.event_sourcing.event_store import EVT_POSITION_OPENED, is_enabled
from backend.services.event_sourcing.phase2 import (
    get_live_repository,
    is_phase2_read_enabled,
    is_phase2_reconcile_enabled,
    merge_projection_with_db_prices,
    projection_positions_for_account,
    reconcile_db_vs_projection,
    record_position_event,
)

logger = logging.getLogger(__name__)

_phase3_stats: Dict[str, int] = {
    "bootstrap_writes": 0,
    "startup_warm_calls": 0,
}


def is_phase3_enabled() -> bool:
    if not is_enabled():
        return False
    return os.environ.get("EVENT_SOURCING_PHASE3", "false").strip().lower() in (
        "1", "true", "yes", "on",
    )


def get_phase3_stats() -> Dict[str, int]:
    return dict(_phase3_stats)


def is_projection_read_active() -> bool:
    """Phase 3 开启即默认投影读；否则沿用 Phase 2 开关。"""
    return is_phase3_enabled() or is_phase2_read_enabled()


def _normalize_side(side: str) -> str:
    s = (side or "").lower()
    if s in ("buy", "long"):
        return "long"
    if s in ("sell", "short"):
        return "short"
    return s


def bootstrap_db_position_row(row: dict, *, account_id: int) -> bool:
    """将 DB open 仓位补写入事件流（仅当投影中缺失时）。"""
    if not is_phase3_enabled():
        return False
    global _phase3_stats
    pid = str(row.get("id") or row.get("position_id") or "")
    if not pid:
        return False
    try:
        repo = get_live_repository()
        existing = repo.projection.current_state.get(pid)
        if existing and existing.get("status") == "open":
            return False
    except Exception:
        pass

    payload = {
        "account_id": int(row.get("account_id") or account_id or 0),
        "symbol": row.get("symbol"),
        "side": _normalize_side(str(row.get("side") or "")),
        "size": float(row.get("size") or 0),
        "entry_price": float(row.get("entry_price") or row.get("avg_entry_price") or 0),
        "trade_nature": row.get("trade_nature") or "swing",
        "strategy_id": row.get("strategy_id"),
        "leverage": float(row.get("leverage") or 1),
        "_bootstrap": "phase3_db_seed",
    }
    ok = record_position_event(EVT_POSITION_OPENED, pid, payload)
    if ok:
        _phase3_stats["bootstrap_writes"] = _phase3_stats.get("bootstrap_writes", 0) + 1
        logger.info(
            "[EventSourcing#9 Phase3] bootstrap position %s %s from DB",
            pid, row.get("symbol"),
        )
    return ok


def warm_startup_projection(db) -> int:
    """启动预热：replay 已由 get_live_repository 触发；补齐 DB 有、投影无的 open 仓。"""
    global _phase3_stats
    if not is_phase3_enabled():
        return 0
    _phase3_stats["startup_warm_calls"] = _phase3_stats.get("startup_warm_calls", 0) + 1
    n = 0
    try:
        from backend.database.models import PaperPosition

        rows = (
            db.query(PaperPosition)
            .filter(PaperPosition.status == "open")
            .all()
        )
        for row in rows:
            d = {
                "id": row.id,
                "account_id": row.account_id,
                "symbol": row.symbol,
                "side": row.side,
                "size": row.size,
                "entry_price": getattr(row, "entry_price", None) or getattr(row, "avg_entry_price", None),
                "trade_nature": getattr(row, "trade_nature", None),
                "strategy_id": getattr(row, "strategy_id", None),
                "leverage": getattr(row, "leverage", None),
                "status": "open",
            }
            if bootstrap_db_position_row(d, account_id=int(row.account_id or 0)):
                n += 1
        if n:
            logger.info("[EventSourcing#9 Phase3] 启动引导完成: %d 个 open 仓位写入事件流", n)
    except Exception as exc:
        logger.debug("[EventSourcing#9 Phase3] 启动预热跳过: %s", exc)
    return n


def resolve_position_list_for_read(
    db_rows: List[dict],
    *,
    account_id: int,
    status: str = "open",
) -> List[dict]:
    """统一读路径：对拍 →（Phase3/Phase2_READ）投影读 + DB 动态字段合并。"""
    result = list(db_rows or [])
    if status != "open" or not is_enabled():
        return result

    rec = None
    if is_phase2_reconcile_enabled():
        rec = reconcile_db_vs_projection(result, account_id=account_id, status=status)

    use_proj = is_projection_read_active() and (rec is None or rec.ok)
    if not use_proj:
        return result

    proj_rows = projection_positions_for_account(account_id, status=status)
    if not proj_rows:
        return result
    return merge_projection_with_db_prices(proj_rows, result)
