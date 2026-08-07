"""持仓时限 AI 复审 + TrendAgent 复查 — 从 monolith 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class HoldTrendReviewHost:
    active_db_sessions: Dict[str, Any]
    last_hold_timeout_ai_review: Dict[str, float]
    last_analyst_reports: Dict[str, Any] = field(default_factory=dict)
    last_unified_snapshot: Any = None
    TIER_PROTECTION: Dict[str, Any] = field(default_factory=dict)

    get_trading_account_id: Callable = field(repr=False, default=lambda *a, **k: 0)
    append_event: Callable = field(repr=False, default=lambda *a, **k: None)
    active_exchange: Callable = field(repr=False, default=lambda: "binance")
    run_analyst_system: Callable = field(repr=False, default=lambda *a, **k: None)
    get_account_risk_score: Callable = field(repr=False, default=lambda *a, **k: 0.0)
    clear_hold_timeout_queue_entry: Callable = field(repr=False, default=lambda *a, **k: None)
    orch_payload_from_decision: Callable = field(repr=False, default=lambda *a, **k: {})
    safe_commit: Callable = field(repr=False, default=lambda *a, **k: True)


def build_hold_trend_review_host(svc) -> HoldTrendReviewHost:
    return HoldTrendReviewHost(
        active_db_sessions=svc._active_db_sessions,
        last_hold_timeout_ai_review=svc._last_hold_timeout_ai_review,
        last_analyst_reports=getattr(svc, "_last_analyst_reports", None) or {},
        last_unified_snapshot=getattr(svc, "_last_unified_snapshot", None),
        TIER_PROTECTION=getattr(svc, "TIER_PROTECTION", {}) or {},
        get_trading_account_id=svc._get_trading_account_id,
        append_event=svc._append_event,
        active_exchange=svc._active_exchange,
        run_analyst_system=svc._run_analyst_system,
        get_account_risk_score=svc._get_account_risk_score,
        clear_hold_timeout_queue_entry=svc._clear_hold_timeout_queue_entry,
        orch_payload_from_decision=svc._orch_payload_from_decision,
        safe_commit=svc._safe_commit,
    )


def run_hold_timeout_ai_review_if_needed(
    session_id: str,
    host: HoldTrendReviewHost,
    *,
    priority_expired: bool = False,
) -> None:
    from backend.database.connection import SessionLocal
    from backend.database.models import FullAutoSession

    db = SessionLocal()
    _db_track_key = f"{session_id}:hold_timeout_review"
    host.active_db_sessions[_db_track_key] = db
    try:
        session = db.query(FullAutoSession).filter(
            FullAutoSession.session_id == session_id
        ).first()
        if not session or session.status not in ("running", "defensive"):
            return
        acct = host.get_trading_account_id(db, session)
        from backend.services.hold_timeout_review_queue import (
            get_pending_for_account,
            sync_open_positions,
        )
        from backend.services.paper_trading_engine import paper_engine

        positions = paper_engine.get_positions(db, acct) or []
        sync_open_positions(acct, positions)
        pending = get_pending_for_account(acct)
        if not pending:
            return

        has_expired = any(p.get("expired") for p in pending)
        oldest = min(p.get("first_flag_ts", time.time()) for p in pending)
        from backend.services.hold_timeout_review_queue import needs_priority_ai_review
        _priority = priority_expired or needs_priority_ai_review(acct)
        if not has_expired and not _priority and (time.time() - oldest) < 180:
            return

        try:
            expired_iv = max(30, int(os.getenv("HOLD_TIMEOUT_EXPIRED_REVIEW_SEC", "60")))
        except Exception:
            expired_iv = 60
        try:
            near_iv = max(60, int(os.getenv("HOLD_TIMEOUT_NEAR_REVIEW_SEC", "300")))
        except Exception:
            near_iv = 300

        last = host.last_hold_timeout_ai_review.get(session_id, 0)
        interval = expired_iv if has_expired else near_iv
        if not (_priority and has_expired) and not (priority_expired and has_expired) and (time.time() - last) < interval:
            return

        host.last_hold_timeout_ai_review[session_id] = time.time()
        run_hold_timeout_ai_review(db, session, pending, host)
    except Exception as e:
        logger.warning(f"[FullAuto] 持仓时限AI复审调度失败: {e}")
    finally:
        host.active_db_sessions.pop(_db_track_key, None)
        db.close()

def run_hold_timeout_ai_review(
    db: Session,
    session,
    pending: list,
    host: HoldTrendReviewHost,
) -> None:
    import json as _json

    syms = sorted({p.get("symbol") for p in pending if p.get("symbol")})
    _exp_n = sum(1 for p in pending if p.get("expired"))
    host.append_event(
        session,
        "hold_timeout_ai_review",
        f"⏰ 持仓时限AI复审: {len(pending)} 仓(超期{_exp_n}) → {','.join(syms)}",
    )
    logger.info(
        f"[FullAuto] 持仓时限AI复审开始 session={session.session_id} "
        f"pending={len(pending)} expired={_exp_n} symbols={syms}"
    )

    market_summary = {}
    try:
        raw = session.last_market_summary
        if isinstance(raw, str) and raw.strip():
            market_summary = _json.loads(raw)
        elif isinstance(raw, dict):
            market_summary = raw
    except Exception:
        market_summary = {}

    if not market_summary:
        try:
            from backend.services.unified_data_pool import unified_data_pool
            snap = unified_data_pool.capture_snapshot(
                symbols=session.symbols,
                account_id=host.get_trading_account_id(db, session),
                environment=host.active_exchange(),
            )
            from backend.services.multi_timeframe_orchestrator import mt_orchestrator
            for sym in syms:
                if sym in (session.symbols or []):
                    dec = mt_orchestrator.evaluate(sym, snap)
                    market_summary.setdefault(sym, {})
                    market_summary[sym]["orchestrator"] = {
                        "final_action": dec.final_action,
                        "long_confidence": dec.long_view.confidence,
                        "mid_confidence": dec.mid_view.confidence,
                        "short_confidence": dec.short_view.confidence,
                    }
        except Exception as e:
            logger.warning(f"[FullAuto] 复审市场快照失败: {e}")

    active_ids = list(session.active_strategy_ids or [])
    acct = host.get_trading_account_id(db, session)
    try:
        session._hold_timeout_review_symbols = set(s.upper() for s in syms)
        host.run_analyst_system(db, session, active_ids, market_summary)
        from backend.services.hold_timeout_review_queue import mark_review_cycle_done
        mark_review_cycle_done(acct, syms)
    except Exception as e:
        logger.error(f"[FullAuto] 持仓时限AI复审异常: {e}", exc_info=True)
    finally:
        if hasattr(session, "_hold_timeout_review_symbols"):
            delattr(session, "_hold_timeout_review_symbols")

    # ── TrendAgent 持仓复查（2026-06-18）──
    # 交易执行后，对 trend_follow/position 持仓做定期深度复查（90min 节流）。
    # 判断趋势是否仍成立，给出 hold/reduce/close/tighten 建议。
    try:
        run_trend_review(db, session, acct, market_summary, host)
    except Exception as _trend_rev_err:
        logger.debug(f"[TrendAgent] 持仓复查异常: {_trend_rev_err}")

def run_trend_review(
    db,
    session,
    account_id,
    market_summary,
    host: HoldTrendReviewHost,
) -> None:
    from backend.services.trend_agent import (
        trend_agent, TREND_REVIEW_INTERVAL_SEC, TREND_REVIEW_MAX_PER_TICK,
    )
    from backend.services.paper_trading_engine import paper_engine
    from backend.database.models import PaperPosition
    import json as _json

    positions = paper_engine.get_positions(db, account_id) or []
    _now = time.time()
    reviewed = 0

    for p in positions:
        if reviewed >= TREND_REVIEW_MAX_PER_TICK:
            break
        nature = (p.get("trade_nature") or "").lower()
        if not trend_agent.is_trend_nature(nature):
            continue
        if p.get("status") != "open":
            continue

        sym = p.get("symbol", "")
        pid = p.get("id")
        if not sym or not pid:
            continue

        # 节流：读 exit_state_json 里的 last_trend_review_ts
        db_pos = db.query(PaperPosition).filter(PaperPosition.id == int(pid)).first()
        if not db_pos:
            continue
        _state = {}
        try:
            _state = _json.loads(getattr(db_pos, "exit_state_json", None) or "{}")
        except Exception:
            _state = {}
        _last_review = float(_state.get("last_trend_review_ts", 0) or 0)
        if (_now - _last_review) < TREND_REVIEW_INTERVAL_SEC:
            continue  # 还没到复查时间

        # 计算持仓信息
        entry = float(p.get("entry_price", 0) or 0)
        mark = float(p.get("mark_price", 0) or entry)
        side = p.get("side", "long")
        lev = int(p.get("leverage", 1) or 1)
        margin = float(p.get("margin", 0) or 0)
        qty = float(p.get("quantity", 0) or p.get("size", 0) or 0)
        pnl = float(p.get("unrealized_pnl", 0) or 0)
        pnl_pct = (pnl / margin * 100) if margin > 0 else 0
        # 持仓时长
        opened_at = getattr(db_pos, "opened_at", None) or getattr(db_pos, "created_at", None)
        hold_hours = ((_now - opened_at.timestamp()) / 3600) if opened_at else 0

        _pos_ctx = {
            "entry_price": entry, "mark_price": mark, "pnl_pct": pnl_pct,
            "hold_hours": hold_hours, "leverage": lev,
        }

        # 调 TrendAgent 复查
        _review = trend_agent.review_position(
            symbol=sym, side=side, position=_pos_ctx,
            reports=host.last_analyst_reports or {},
            market_envs=market_summary or {},
            account_id=account_id,
            db=db,
        )
        _action = _review.get("action", "hold")
        _reasoning = _review.get("reasoning", "")[:120]

        try:
            from backend.services.trend_prediction_service import trend_prediction_service
            trend_prediction_service.append_review_snapshot(
                paper_position_id=int(pid),
                mark_price=mark,
                note=_reasoning,
            )
        except Exception as _tps_err:
            logger.debug(f"[TrendPrediction] 复查快照跳过: {_tps_err}")

        logger.info(
            f"[TrendAgent] 复查 {sym} {nature} {side} pnl={pnl_pct:+.1f}% "
            f"hold={hold_hours:.1f}h -> {_action} ({_reasoning})"
        )

        # 执行建议 — UnifiedExitExecutor Tier 1
        if _action in ("close", "reduce"):
            try:
                from backend.services.unified_exit_executor import (
                    unified_exit_executor, ExitExecuteRequest,
                )
                _pos_side = side if side in ("long", "short") else (
                    "long" if str(side).lower() in ("buy", "long") else "short"
                )
                _pos_dict = dict(p)
                _pos_dict["side"] = _pos_side
                if not _pos_dict.get("quantity"):
                    _pos_dict["quantity"] = float(
                        p.get("size", 0) or p.get("quantity", 0) or 0
                    )
                _exit_req = ExitExecuteRequest(
                    db=db,
                    account_id=int(account_id),
                    symbol=sym,
                    action=_action,
                    pos=_pos_dict,
                    exit_channel=(
                        "trend_review_close" if _action == "close"
                        else f"trend_review_reduce_{int(_review.get('reduce_ratio', 0.3)*100)}%"
                    ),
                    reason="trend_review_close" if _action == "close" else "trend_review_reduce",
                    reasoning=_reasoning,
                    reduce_ratio=float(_review.get("reduce_ratio", 0.3) or 0.3),
                    reduce_qty=(
                        qty * float(_review.get("reduce_ratio", 0.3) or 0.3)
                        if _action == "reduce" else None
                    ),
                    tier_level=1,
                    session=session,
                    append_event=host.append_event,
                    get_risk_score=host.get_account_risk_score,
                    tier_protection=host.TIER_PROTECTION,
                )
                _result = unified_exit_executor.execute(_exit_req)
                if _result:
                    host.append_event(
                        session, "trend_review_executed",
                        f"📊 Trend复查{_action} {sym} PnL=${_result.get('pnl', 0):+.2f} | {_reasoning}",
                    )
                    host.clear_hold_timeout_queue_entry(_pos_dict)
                    logger.info(f"[TrendAgent] {sym} 趋势复查{_action}已执行: {_reasoning}")
                elif _action != "hold":
                    logger.info(f"[TrendAgent] {sym} 趋势复查{_action}被门控拦截: {_reasoning}")
            except Exception as _ce:
                logger.warning(f"[TrendAgent] {sym} 复查执行失败: {_ce}")

        # 写入 trend_adjustment + 更新复查时间戳
        _trend_adj = _review.get("trend_adjustment", {})
        _state["last_trend_review_ts"] = _now
        if _trend_adj:
            _state["trend_adjustment"] = _trend_adj
        try:
            from backend.services.position_exit_state import merge_exit_state, dump_exit_state
            db_pos.exit_state_json = dump_exit_state(_state)
            db.commit()
        except Exception:
            db.rollback()

        reviewed += 1
