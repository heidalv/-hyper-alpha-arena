"""轻量交易循环 — 从 monolith _run_light_trading_cycle 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict

logger = logging.getLogger(__name__)


@dataclass
class LightTradingHost:
    active_db_sessions: Dict[str, Any]
    last_unified_snapshot: Any = None

    get_trading_account_id: Callable = field(repr=False, default=lambda *a, **k: 0)
    active_exchange: Callable = field(repr=False, default=lambda: "binance")
    orch_payload_from_decision: Callable = field(repr=False, default=lambda *a, **k: {})
    run_analyst_system: Callable = field(repr=False, default=lambda *a, **k: None)
    safe_commit: Callable = field(repr=False, default=lambda *a, **k: True)


def build_light_trading_host(svc) -> LightTradingHost:
    return LightTradingHost(
        active_db_sessions=svc._active_db_sessions,
        last_unified_snapshot=getattr(svc, "_last_unified_snapshot", None),
        get_trading_account_id=svc._get_trading_account_id,
        active_exchange=svc._active_exchange,
        orch_payload_from_decision=svc._orch_payload_from_decision,
        run_analyst_system=svc._run_analyst_system,
        safe_commit=svc._safe_commit,
    )


def run_light_trading_cycle(session_id: str, host: LightTradingHost) -> None:
    from backend.database.connection import SessionLocal
    from backend.database.models import FullAutoSession

    db = SessionLocal()
    _db_track_key = f"{session_id}:light_trading"
    host.active_db_sessions[_db_track_key] = db
    try:
        session = db.query(FullAutoSession).filter(
            FullAutoSession.session_id == session_id
        ).first()
        if not session or session.status not in ("running", "defensive"):
            return
        active_ids = list(session.active_strategy_ids or [])
        if not active_ids:
            return

        market_summary = {}
        try:
            raw = session.last_market_summary
            if isinstance(raw, dict):
                market_summary = dict(raw)
        except Exception:
            pass

        try:
            from backend.services.unified_data_pool import unified_data_pool
            snap = unified_data_pool.capture_snapshot(
                symbols=session.symbols,
                account_id=host.get_trading_account_id(db, session),
                environment=host.active_exchange(),
                include_klines=True,
                include_strategy=True,
            )
            host.last_unified_snapshot = snap
            unified_data_pool.merge_snapshot_into_market_summary(
                market_summary, snap, session.symbols,
            )
            from backend.services.multi_timeframe_orchestrator import mt_orchestrator
            for sym, dec in mt_orchestrator.evaluate_portfolio(
                session.symbols, snap
            ).items():
                market_summary.setdefault(sym, {})
                market_summary[sym]["orchestrator"] = host.orch_payload_from_decision(dec)
                market_summary[sym]["recommended_nature"] = getattr(
                    dec, "recommended_nature", None
                )
        except Exception as e:
            logger.warning(f"[FullAuto] 轻量交易循环快照失败: {e}")

        logger.info(
            f"[FullAuto] 轻量交易循环 AI 决策 session={session_id} "
            f"active={len(active_ids)}"
        )
        host.run_analyst_system(db, session, active_ids, market_summary)
        session.last_market_summary = market_summary
        host.safe_commit(db, "light_trading_cycle", session=session)
    except Exception as e:
        logger.error(f"[FullAuto] 轻量交易循环异常: {e}", exc_info=True)
    finally:
        host.active_db_sessions.pop(_db_track_key, None)
        db.close()
