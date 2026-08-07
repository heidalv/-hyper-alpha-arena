"""
AI 主交易循环（整改#8 trading_cycle_loop 拆分）。

从 full_auto_trading_service._run_trading_cycle 迁出；
monolith 保留 thin shim 转发。
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

if TYPE_CHECKING:
    from backend.services.full_auto_trading_service import FullAutoTradingService

logger = logging.getLogger(__name__)


def _apply_training_phase_tick_constraints(svc: "FullAutoTradingService", session) -> None:
    """训练期：收窄分析币种池，记录允许新开仓的 symbol 集合。"""
    self = svc
    self._training_allowed_symbols = set()
    try:
        from backend.services.training_phase_service import is_active, target_symbols
        if not is_active():
            return
        allowed = {str(s).upper() for s in target_symbols()}
        self._training_allowed_symbols = allowed
        orig = list(session.symbols or [])
        session.symbols = (
            [s for s in orig if str(s).upper() in allowed]
            + [s for s in orig if str(s).upper() not in allowed]
        )
        if getattr(session, "auto_coin_symbols", None):
            from backend.services.auto_coin_policy import filter_strict_auto_symbols
            filtered = filter_strict_auto_symbols(session.auto_coin_symbols or [])
            session.auto_coin_symbols = [
                s for s in filtered if str(s).upper() in allowed
            ]
        logger.debug(
            f"[FullAuto] 训练期币种过滤: allowed={sorted(allowed)} "
            f"symbols={session.symbols}"
        )
    except Exception as err:
        logger.debug(f"[FullAuto] training phase filter skip: {err}")

def run_trading_cycle(
    svc: "FullAutoTradingService",
    session_id: str,
    ai_tiers: Optional[List[str]] = None,
) -> None:
    """交易本质路径：数据 → 编排器(辅) → 多 Agent AI → 执行。"""
    self = svc
    # [C1] 后台交易循环(由 coordinator 或独立调度器驱动),设 system_identity 穿透 RLS。
    from backend.core.tenant import set_system_identity
    set_system_identity()
    self._current_ai_tiers = list(ai_tiers or ["mid", "long"])
    from backend.database.connection import SessionLocal
    from backend.database.models import FullAutoSession, AIStrategy as _AIStrategy

    db = SessionLocal()
    _db_track_key = f"{session_id}:trading_cycle"
    self._active_db_sessions[_db_track_key] = db
    # 每个 tick 清空策略缓存，避免跨 session 的 detached instance 错误
    if hasattr(self, '_master_strat_cache'):
        self._master_strat_cache.clear()
    try:
        session = db.query(FullAutoSession).filter(
            FullAutoSession.session_id == session_id
        ).first()
        if not session or session.status not in ("running", "defensive"):
            return

        _apply_training_phase_tick_constraints(self, session)

        active_ids = list(session.active_strategy_ids or [])
        if not active_ids:
            logger.info(f"[FullAuto] 交易循环跳过: 无活跃策略 {session_id}")
            return

        tick = self._unified_tick_count.get(session_id, 1)
        from backend.config.settings import FULLAUTO_AI_DOMINANT, MIDLONG_AI_MANDATORY
        _full_symbol_coverage = FULLAUTO_AI_DOMINANT or MIDLONG_AI_MANDATORY
        if _full_symbol_coverage:
            max_strategies = max(1, len(active_ids) or 1)
            max_symbols = max(1, len(session.symbols or []) or 1)
            logger.debug(
                f"[FullAuto] 全币种覆盖 tick#{tick} "
                f"(midlong_ai={MIDLONG_AI_MANDATORY}) symbols={max_symbols}"
            )
        else:
            try:
                from backend.services.paper_pace_controller import paper_pace_controller
                _knobs = paper_pace_controller.get_knobs()
                max_strategies = max(1, _knobs.max_strategies_per_tick)
                max_symbols = max(1, _knobs.max_symbols_per_tick)
                try:
                    from backend.services.training_phase_service import is_active
                    if is_active():
                        max_symbols = min(max_symbols, 6)
                except Exception:
                    pass
            except Exception:
                try:
                    max_strategies = max(1, int(os.getenv("FULLAUTO_MAX_STRATEGIES_PER_TICK", "8")))
                except Exception:
                    max_strategies = 8
                try:
                    max_symbols = max(1, int(os.getenv("FULLAUTO_MAX_SYMBOLS_PER_TICK", "8")))
                except Exception:
                    max_symbols = 8

        selected_symbols_for_tick: Set[str] = set()
        if not _full_symbol_coverage and len(active_ids) > max_strategies:
            rows = (
                db.query(
                    _AIStrategy.strategy_id,
                    _AIStrategy.primary_symbol,
                    _AIStrategy.timeframe_tier,
                )
                .filter(_AIStrategy.strategy_id.in_(active_ids))
                .order_by(_AIStrategy.primary_symbol.asc(), _AIStrategy.timeframe_tier.asc())
                .all()
            )
            ordered = [
                (sid, (sym or "").upper())
                for sid, sym, _tier in rows
                if sid and (sym or "").strip()
            ]
            if ordered:
                start = ((max(tick, 1) - 1) * max_strategies) % len(ordered)
                selected = (ordered + ordered)[start:start + max_strategies]
                active_ids = [sid for sid, _sym in selected]
                selected_symbols_for_tick = {_sym for _sid, _sym in selected if _sym}
                logger.info(
                    f"[FullAuto] tick#{tick} 批量限流: "
                    f"策略 {len(session.active_strategy_ids or [])}->{len(active_ids)}, "
                    f"symbols={sorted(selected_symbols_for_tick)}"
                )
        elif MIDLONG_AI_MANDATORY and len(active_ids) > max_strategies:
            rows = (
                db.query(
                    _AIStrategy.strategy_id,
                    _AIStrategy.primary_symbol,
                    _AIStrategy.timeframe_tier,
                )
                .filter(_AIStrategy.strategy_id.in_(active_ids))
                .order_by(_AIStrategy.primary_symbol.asc(), _AIStrategy.timeframe_tier.asc())
                .all()
            )
            midlong_rows = [
                (sid, (sym or "").upper(), (tier or "mid").lower())
                for sid, sym, tier in rows
                if sid and (sym or "").strip()
                and (tier or "mid").lower()
                in (getattr(self, "_current_ai_tiers", None) or ["mid", "long"])
            ]
            short_rows = [
                (sid, (sym or "").upper(), (tier or "short").lower())
                for sid, sym, tier in rows
                if sid and (sym or "").strip() and (tier or "short").lower() == "short"
            ]
            midlong_ids = [sid for sid, _sym, _t in midlong_rows]
            budget = max(0, max_strategies - len(midlong_ids))
            short_ids: list = []
            if budget > 0 and short_rows:
                start = ((max(tick, 1) - 1) * budget) % len(short_rows)
                picked = (short_rows + short_rows)[start:start + budget]
                short_ids = [sid for sid, _sym, _t in picked]
            active_ids = list(dict.fromkeys(midlong_ids + short_ids)) or active_ids[:max_strategies]
            selected_symbols_for_tick = {
                _sym for _sid, _sym, _t in (midlong_rows + short_rows)
                if _sid in active_ids and _sym
            }
            logger.info(
                f"[FullAuto] tick#{tick} 中长线优先限流: "
                f"mid/long={len(midlong_ids)} short={len(short_ids)} "
                f"symbols={sorted(selected_symbols_for_tick)}"
            )
        
        _t0 = time.time()
        symbols = list(session.symbols or [])
        if _full_symbol_coverage:
            symbols = list(dict.fromkeys(symbols))
            self._tick_symbol_subset[session_id] = {str(s).upper() for s in symbols}
            logger.info(
                f"[FullAuto] tick#{tick} 全 session 币种: "
                f"{[str(s).upper() for s in symbols]}"
            )
        elif not FULLAUTO_AI_DOMINANT:
            if selected_symbols_for_tick:
                symbols = [s for s in symbols if str(s).upper() in selected_symbols_for_tick]
            if len(symbols) > max_symbols:
                start = ((max(tick, 1) - 1) * max_symbols) % len(symbols)
                symbols = (symbols + symbols)[start:start + max_symbols]
                logger.info(
                    f"[FullAuto] tick#{tick} symbol限流: 本轮处理 {len(symbols)} 个 "
                    f"{[str(s).upper() for s in symbols]}"
                )
        if FULLAUTO_AI_DOMINANT:
            self._tick_symbol_subset.pop(session_id, None)
        else:
            self._tick_symbol_subset[session_id] = {str(s).upper() for s in symbols}

        # D7: 缓存 + 已有 DB 概览合并，避免空缓存把价格/编排器写没
        _prev_ms = session.last_market_summary if isinstance(session.last_market_summary, dict) else {}
        market_summary: Dict[str, Any] = {}
        for s in symbols:
            prev = dict(_prev_ms.get(s) or {}) if isinstance(_prev_ms.get(s), dict) else {}
            cached = self._market_scan_cache.get(s)
            if isinstance(cached, dict) and cached:
                for k, v in cached.items():
                    if v is not None and v != "":
                        prev[k] = v
            market_summary[s] = prev
        self._ensure_market_prices(market_summary, symbols)

        try:
            from backend.services.market_flow_collector import market_flow_collector
            if symbols and market_flow_collector.running:
                market_flow_collector.refresh_subscriptions(symbols)
        except Exception:
            pass

        snap = {}
        if os.getenv("FULLAUTO_CAPTURE_UNIFIED_SNAPSHOT", "false").lower() in ("1", "true", "yes", "on"):
            from backend.services.unified_data_pool import unified_data_pool
            def _capture_unified_snapshot():
                return unified_data_pool.capture_snapshot(
                    symbols=symbols,
                    account_id=self._get_trading_account_id(db, session),
                    environment=self._active_exchange(),
                    include_klines=True,
                    include_strategy=False,
                    light_mode=True,
                )
            snap = self._run_with_timeout(
                _capture_unified_snapshot,
                timeout_s=float(os.getenv("FULLAUTO_SNAPSHOT_TIMEOUT_S", "12")),
                fallback={},
                label="trading_unified_snapshot",
            ) or {}
        else:
            logger.info("[FullAuto] 跳过统一数据快照捕获，使用缓存行情运行交易循环")
        self._last_unified_snapshot = snap
        if snap:
            from backend.services.unified_data_pool import unified_data_pool
            unified_data_pool.merge_snapshot_into_market_summary(
                market_summary, snap, symbols,
            )

        from backend.config.settings import FULLAUTO_AI_DOMINANT
        _run_trading_orch = (
            FULLAUTO_AI_DOMINANT
            or os.getenv("FULLAUTO_RUN_TRADING_ORCHESTRATOR", "false").lower()
            in ("1", "true", "yes", "on")
        )
        if _run_trading_orch and self._orch_bg_cache_covers_symbols(symbols):
            self._merge_orch_from_scan_cache(market_summary, symbols)
            logger.info(
                "[FullAuto] OrchBG 缓存新鲜，跳过同步编排器 "
                f"({len(symbols)} symbols, age={time.time() - float(getattr(self, '_market_scan_cache_ts', 0) or 0):.0f}s)"
            )
            _run_trading_orch = False
        if _run_trading_orch:
            try:
                from backend.services.multi_timeframe_orchestrator import mt_orchestrator
                def _orch_eval():
                    return mt_orchestrator.evaluate_portfolio(symbols, snap)
                orch_result = self._run_with_timeout(
                    _orch_eval,
                    timeout_s=float(os.getenv("FULLAUTO_TRADING_ORCHESTRATOR_TIMEOUT_S", "12")),
                    fallback={},
                    label="trading_orchestrator"
                )
                for sym, dec in (orch_result or {}).items():
                    market_summary.setdefault(sym, {})
                    market_summary[sym]["orchestrator"] = self._orch_payload_from_decision(dec)
                    market_summary[sym]["recommended_nature"] = dec.recommended_nature
            except Exception as e:
                logger.warning(f"[FullAuto] 交易循环编排器: {e}")
        else:
            logger.info("[FullAuto] 跳过交易循环同步编排器，使用后台/缓存信号")

        # 超期仓优先触发专用 AI 复审（不受 5min 节流）
        self._run_hold_timeout_ai_review_if_needed(
            session_id, priority_expired=True,
        )
        self._run_analyst_system(db, session, active_ids, market_summary)

        self._ensure_market_prices(market_summary, symbols)
        for _sym, _info in market_summary.items():
            if isinstance(_info, dict):
                self._normalize_orchestrator_for_ui(_info)
                self._attach_scalp_advisory_for_ui(_sym, _info)

        session.last_market_summary = market_summary
        session.last_health_check_at = datetime.now(timezone.utc)
        self._safe_commit(db, "trading_cycle", session=session)
        logger.info(
            f"[FullAuto] 交易循环完成 {session_id} "
            f"耗时={time.time()-_t0:.1f}s active={len(active_ids)}"
        )
        # 喂给自适应 hang 阈值统计器：近 N 轮真实耗时驱动动态阈值，避免固定值误杀长轮
        self._record_tick_duration(session_id, time.time() - _t0)
    except Exception as e:
        logger.error(f"[FullAuto] 交易循环异常: {e}", exc_info=True)
    finally:
        self._tick_symbol_subset.pop(session_id, None)
        self._active_db_sessions.pop(_db_track_key, None)
        db.close()

