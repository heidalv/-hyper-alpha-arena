"""持仓超时 → 排队交由大模型评估是否平仓（非粗暴强平）。"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

from backend.services.position_hold_time import (
    format_hold_timeout_reason,
    get_position_hold_status,
    is_position_hold_expired,
    resolve_tier_from_position,
)

logger = logging.getLogger(__name__)

_lock = threading.Lock()
# pos_id -> review record
_queue: Dict[int, Dict[str, Any]] = {}


def _pos_key(pos: Any) -> Optional[int]:
    pid = getattr(pos, "id", None)
    if pid is None and isinstance(pos, dict):
        pid = pos.get("id")
    return int(pid) if pid is not None else None


def register_position_for_review(
    pos: Any,
    *,
    account_id: int,
    force: bool = False,
) -> bool:
    """
    登记需 AI 复审的持仓。已超时或接近超时(≥85%)时返回 True。
    force=True 时强制刷新登记。
    短线 scalp/intraday 永不登记（硬超时强平，不走 Master 复审）。
    """
    from backend.services.position_hold_time import (
        is_short_no_ai_hold_nature,
        resolve_nature_from_position,
    )
    if is_short_no_ai_hold_nature(resolve_nature_from_position(pos)):
        return False

    status = get_position_hold_status(pos)
    if not status.get("hold_expired") and not status.get("hold_near_timeout"):
        return False

    pid = _pos_key(pos)
    if pid is None:
        return False

    is_expired = bool(status.get("hold_expired"))
    now = time.time()
    with _lock:
        existing = _queue.get(pid)
        if existing and not force:
            # 已超期仓位更频繁刷新，确保 AI 复审不被节流挡住
            min_refresh = 30 if is_expired else 120
            if now - existing.get("last_flag_ts", 0) < min_refresh:
                return True
        _queue[pid] = {
            "position_id": pid,
            "account_id": int(account_id),
            "symbol": (getattr(pos, "symbol", None) or (pos.get("symbol") if isinstance(pos, dict) else "")).upper(),
            "side": getattr(pos, "side", None) or (pos.get("side") if isinstance(pos, dict) else ""),
            "tier": status.get("tier") or resolve_tier_from_position(pos),
            "trade_nature": getattr(pos, "trade_nature", None) or (pos.get("trade_nature") if isinstance(pos, dict) else None),
            "strategy_id": getattr(pos, "strategy_id", None) or (pos.get("strategy_id") if isinstance(pos, dict) else None),
            "status": status,
            "expired": bool(status.get("hold_expired")),
            "near_timeout": bool(status.get("hold_near_timeout")),
            "first_flag_ts": (existing or {}).get("first_flag_ts", now),
            "last_flag_ts": now,
            "review_count": int((existing or {}).get("review_count", 0)),
            "reason": format_hold_timeout_reason(status),
        }
    return True


def get_pending_for_account(account_id: int) -> List[Dict[str, Any]]:
    with _lock:
        return [
            dict(v) for v in _queue.values()
            if int(v.get("account_id", 0)) == int(account_id)
        ]


def get_alerts_for_prompt(account_id: int) -> List[Dict[str, Any]]:
    """供 LLM prompt 注入的精简列表。"""
    pending = get_pending_for_account(account_id)
    alerts = []
    for p in pending:
        st = p.get("status") or {}
        alerts.append({
            "position_id": p.get("position_id"),
            "symbol": p.get("symbol"),
            "side": p.get("side"),
            "tier": p.get("tier"),
            "trade_nature": p.get("trade_nature"),
            "expired": p.get("expired"),
            "near_timeout": p.get("near_timeout"),
            "hold_age_hours": st.get("hold_age_hours"),
            "max_hold_hours": st.get("max_hold_hours"),
            "review_hold_hours": st.get("review_hold_hours"),
            "hold_remaining_hours": st.get("hold_remaining_hours"),
            "hold_progress_pct": st.get("hold_progress_pct"),
            "review_count": p.get("review_count", 0),
            "summary": p.get("reason"),
        })
    return alerts


def mark_review_cycle_done(account_id: int, symbols_processed: Optional[List[str]] = None) -> None:
    """一轮 AI 分析结束后：对已处理 symbol 增加 review_count。"""
    sym_set = {s.upper() for s in (symbols_processed or [])} if symbols_processed else None
    with _lock:
        for pid, rec in list(_queue.items()):
            if int(rec.get("account_id", 0)) != int(account_id):
                continue
            if sym_set is not None and rec.get("symbol") not in sym_set:
                continue
            rec["review_count"] = int(rec.get("review_count", 0)) + 1
            rec["last_review_ts"] = time.time()


def clear_position(pid: int) -> None:
    with _lock:
        _queue.pop(int(pid), None)


def sync_open_positions(account_id: int, positions: List[Any]) -> int:
    """扫描 open 持仓，登记超时/临期项。返回新登记数量。"""
    n = 0
    for pos in positions or []:
        if register_position_for_review(pos, account_id=account_id):
            n += 1
    return n


def should_fallback_force_close(pos: Any, review_count: int = 0) -> tuple[bool, str]:
    """
    仅极端情况规则兜底平仓（AI 多轮未处理或严重超期/深亏）。
    """
    expired, status = is_position_hold_expired(pos)
    if not expired:
        return False, ""

    max_sec = int(status.get("max_hold_sec") or 0)
    age_sec = int(status.get("hold_age_sec") or 0)
    if max_sec > 0 and age_sec > max_sec * 2.0:
        return True, f"超期2倍({age_sec/3600:.1f}h>{max_sec/3600*2:.1f}h)"

    if review_count >= 4:
        return True, f"AI已复审{review_count}轮仍未平仓"

    try:
        margin = float(getattr(pos, "margin", 0) or (pos.get("margin") if isinstance(pos, dict) else 0) or 0)
        upnl = float(getattr(pos, "unrealized_pnl", 0) or (pos.get("unrealized_pnl") if isinstance(pos, dict) else 0) or 0)
        if margin > 0 and max_sec > 0 and age_sec > max_sec * 1.5 and upnl / margin < -0.04:
            return True, (
                f"超期1.5倍({age_sec/3600:.1f}h)且浮亏{upnl/margin*100:.1f}%"
            )
        if margin > 0 and upnl / margin < -0.08:
            return True, f"超时且浮亏{upnl/margin*100:.1f}%超-8%"
    except Exception:
        pass

    return False, ""


def needs_priority_ai_review(account_id: int) -> bool:
    """100% 超期且尚未 AI 复审 → 提升调度优先级（不必等 180s）。"""
    pending = get_pending_for_account(account_id)
    for rec in pending:
        if rec.get("expired") and int(rec.get("review_count", 0) or 0) == 0:
            return True
    return False
