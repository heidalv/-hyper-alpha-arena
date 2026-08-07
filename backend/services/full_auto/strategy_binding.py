"""策略 ORM 绑定辅助 — 避免 detached 对象（从 monolith 迁出）。"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session


def active_exchange() -> str:
    """统一快照跟随当前交易环境。"""
    try:
        from backend.services.exchange_config import get_active_exchange

        return get_active_exchange() or "mainnet"
    except Exception:
        return "mainnet"


def load_strategy_by_id(
    db: Session,
    strategy_id: str,
    *,
    active_ids: Optional[list] = None,
    symbol: Optional[str] = None,
    status: Optional[tuple] = None,
):
    """从当前 db session 加载 AIStrategy（避免使用 detached 对象）。"""
    if not strategy_id:
        return None
    from backend.database.models import AIStrategy as _AIS

    q = db.query(_AIS).filter(_AIS.strategy_id == strategy_id)
    if active_ids is not None:
        q = q.filter(_AIS.strategy_id.in_(list(active_ids)))
    if symbol:
        q = q.filter(_AIS.primary_symbol == symbol)
    if status:
        q = q.filter(_AIS.status.in_(list(status)))
    return q.first()


def ensure_bound_strategy(
    db: Session,
    strat,
    *,
    active_ids: Optional[list] = None,
    symbol: Optional[str] = None,
    status: Optional[tuple] = None,
):
    """若 AIStrategy 已脱离 session，则按 strategy_id 重新查询。"""
    if strat is None:
        return None
    sid = getattr(strat, "strategy_id", None)
    if not sid:
        return strat
    try:
        from sqlalchemy.orm import object_session

        if object_session(strat) is db:
            return strat
    except Exception:
        pass
    return load_strategy_by_id(
        db, sid, active_ids=active_ids, symbol=symbol, status=status,
    )
