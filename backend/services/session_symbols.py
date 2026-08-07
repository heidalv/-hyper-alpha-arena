"""会话交易 universe 单一入口（禁止直读 session.symbols）。"""
from __future__ import annotations

from typing import List, Optional


def resolve_session_trade_symbols(session, db=None, full_auto_service=None) -> List[str]:
    """统一 universe：session + auto_coin + 持仓 + active 策略。"""
    if full_auto_service is not None and hasattr(full_auto_service, "_resolve_session_trade_symbols"):
        return list(full_auto_service._resolve_session_trade_symbols(session, db=db))
    merged: List[str] = []
    seen = set()

    def _add(s) -> None:
        u = str(s or "").strip().upper()
        if u and u not in seen:
            seen.add(u)
            merged.append(u)

    for s in (getattr(session, "symbols", None) or []):
        _add(s)
    for s in (getattr(session, "auto_coin_symbols", None) or []):
        _add(s)
    return merged
