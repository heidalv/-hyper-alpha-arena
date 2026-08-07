"""纸面交易风控辅助查询 — 从 monolith 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
from datetime import datetime, timezone as _tz
from typing import Callable, Optional

from sqlalchemy import func

logger = logging.getLogger(__name__)


def get_today_realized_pnl(db, account_id: int) -> float:
    """查询当日已实现盈亏（P0-1: 用于日亏损熔断）。"""
    from backend.database.models import PaperOrder

    today_start = datetime.now(_tz.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    result = db.query(func.coalesce(func.sum(PaperOrder.pnl), 0)).filter(
        PaperOrder.account_id == account_id,
        PaperOrder.created_at >= today_start,
        PaperOrder.pnl.isnot(None),
    ).scalar()
    return float(result or 0)


def get_account_risk_score(account_id: int) -> float:
    """获取账户健康度评分（P0-2: 用于 MasterCloseGuard risk_score 参数）。"""
    try:
        from backend.services.atas_v2_executor import ATASV2Executor

        _exec = ATASV2Executor()
        _hs = _exec.get_account_health_score(account_id)
        return float(_hs.get("overall", 50)) if isinstance(_hs, dict) else 50.0
    except Exception:
        return 50.0


def tiny_close_allowed_by_hardfact(
    account_id: int,
    pos: dict,
    reasoning: str = "",
    *,
    risk_score_fn: Optional[Callable[[int], float]] = None,
) -> tuple[bool, str]:
    """微仓「等效全平」前的硬事实复核（master_*_close_tiny 专用）。"""
    _risk_fn = risk_score_fn or get_account_risk_score
    try:
        from backend.services.master_close_guard import (
            check_master_close_hardfact,
            check_master_min_hold_block,
        )
        from backend.config.settings import MASTER_CLOSE_TINY_DISABLED_TIERS

        _tier = (pos.get("timeframe_tier") or pos.get("tier") or "mid").strip().lower()
        if _tier in MASTER_CLOSE_TINY_DISABLED_TIERS:
            return False, f"close_tiny disabled for tier={_tier}"
        _mh = check_master_min_hold_block(
            tier=_tier,
            opened_at=pos.get("opened_at"),
            margin=float(pos.get("margin", 0) or 0),
            unrealized_pnl=float(pos.get("unrealized_pnl", 0) or 0),
            action="close",
        )
        if not _mh.allow:
            return False, _mh.detail
        _entry = float(pos.get("entry_price", 0) or 0)
        _mark = float(pos.get("mark_price", 0) or _entry)
        _sl = pos.get("sl_price") or pos.get("stop_loss")
        hf = check_master_close_hardfact(
            tier=_tier,
            action="close",
            entry_price=_entry,
            mark_price=_mark,
            sl_price=(float(_sl) if _sl else None),
            unrealized_pnl=float(pos.get("unrealized_pnl", 0) or 0),
            margin=float(pos.get("margin", 0) or 0),
            risk_score=_risk_fn(account_id),
            reason_hint=reasoning or "",
        )
        return bool(hf.allow), hf.detail
    except Exception as _e:
        return True, f"tiny-close guard error: {_e}"
