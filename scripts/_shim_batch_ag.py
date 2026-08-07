"""Replace A–G methods in monolith with thin shims."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "backend/services/full_auto_trading_service.py"
lines = path.read_text(encoding="utf-8").splitlines(True)


def find_range(method: str) -> tuple[int, int]:
    def_line = next(
        i for i, l in enumerate(lines) if re.match(rf"    def {re.escape(method)}\(", l)
    )
    # include leading @staticmethod / @classmethod decorators
    start = def_line
    while start > 0 and lines[start - 1].startswith("    @"):
        start -= 1
    # IMPORTANT: search for next method AFTER this method's own def line
    end = len(lines)
    for i in range(def_line + 1, len(lines)):
        if lines[i].startswith("    @"):
            j = i + 1
            while j < len(lines) and (lines[j].strip() == "" or lines[j].startswith("    @")):
                j += 1
            if j < len(lines) and lines[j].startswith("    def "):
                end = i
                break
        elif lines[i].startswith("    def "):
            end = i
            break
    return start, end


def replace_method(method: str, shim: str) -> None:
    global lines
    start, end = find_range(method)
    # verify next method boundary not swallowed
    if end < len(lines) and not (
        lines[end].startswith("    def ") or lines[end].startswith("    @")
    ):
        raise SystemExit(f"bad end for {method}: {lines[end][:80]!r}")
    shim_lines = [s + "\n" if not s.endswith("\n") else s for s in shim.splitlines(True)]
    if shim_lines and not shim_lines[-1].endswith("\n"):
        shim_lines[-1] += "\n"
    if not shim.endswith("\n\n") and not shim.endswith("\n"):
        pass
    # ensure trailing blank line between methods
    if shim_lines and shim_lines[-1].strip() != "":
        shim_lines.append("\n")
    removed = end - start
    lines[:] = lines[:start] + shim_lines + lines[end:]
    print(f"shim {method}: removed {removed} lines")


# ── A ────────────────────────────────────────────────────────────────
replace_method(
    "_paper_auto_unlock_session",
    '''    def _paper_auto_unlock_session(self, db: Session, session) -> bool:
        from backend.services.full_auto.paper_session_helpers import (
            build_paper_session_host,
            paper_auto_unlock_session,
        )
        host = build_paper_session_host(self)
        changed = paper_auto_unlock_session(db, session, host)
        self._defensive_entered_at = host.defensive_entered_at
        self._recovery_until = host.recovery_until
        self._symbol_frozen_set = host.symbol_frozen_set
        self._strat_pause_meta = host.strat_pause_meta
        return changed
''',
)

replace_method(
    "_cap_paper_active_strategies",
    '''    def _cap_paper_active_strategies(
        self, db: Session, session, active_ids: list, *, max_per_symbol: Optional[int] = None
    ) -> bool:
        from backend.services.full_auto.paper_session_helpers import (
            build_paper_session_host,
            cap_paper_active_strategies,
        )
        return cap_paper_active_strategies(
            db, session, active_ids, build_paper_session_host(self),
            max_per_symbol=max_per_symbol,
        )
''',
)

replace_method(
    "_get_trade_history",
    '''    def _get_trade_history(self, db, session) -> list:
        from backend.services.full_auto.paper_session_helpers import (
            build_paper_session_host,
            get_trade_history,
        )
        return get_trade_history(db, session, build_paper_session_host(self))
''',
)

replace_method(
    "_cleanup_duplicate_strategies",
    '''    def _cleanup_duplicate_strategies(self, db):
        from backend.services.full_auto.paper_session_helpers import (
            build_paper_session_host,
            cleanup_duplicate_strategies,
        )
        return cleanup_duplicate_strategies(db, build_paper_session_host(self))
''',
)

# ── B ────────────────────────────────────────────────────────────────
replace_method(
    "_build_fast_stability_result",
    '''    @staticmethod
    def _build_fast_stability_result(
        symbols,
        *,
        trigger: str = "timeout",
        timeout_s: Optional[float] = None,
    ) -> dict:
        from backend.services.full_auto.orch_background import build_fast_stability_result
        return build_fast_stability_result(symbols, trigger=trigger, timeout_s=timeout_s)
''',
)

replace_method(
    "_purge_stale_caches",
    '''    def _purge_stale_caches(self):
        from backend.services.full_auto.orch_background import (
            build_orch_background_host,
            purge_stale_caches,
        )
        host = build_orch_background_host(self)
        purge_stale_caches(host)
        self._last_cache_purge = host.last_cache_purge
        self._last_close_time = host.last_close_time
        self._last_reduce_time = host.last_reduce_time
        self._master_strat_cache = host.master_strat_cache
        self._partial_close_tracker = host.partial_close_tracker
        self._market_scan_cache = host.market_scan_cache
        self._market_scan_cache_ts = host.market_scan_cache_ts
        self._strategy_creation_ts = host.strategy_creation_ts
        self._health_status = host.health_status
''',
)

replace_method(
    "_inject_orch_scheduled_stubs",
    '''    def _inject_orch_scheduled_stubs(
        self,
        decisions: list,
        market_summary: dict,
        session=None,
    ) -> list:
        from backend.services.full_auto.orch_background import (
            build_orch_background_host,
            inject_orch_scheduled_stubs,
        )
        host = build_orch_background_host(self)
        result = inject_orch_scheduled_stubs(decisions, market_summary, host, session=session)
        self._last_orch_decisions = host.last_orch_decisions
        self._last_orch_decisions_ts = host.last_orch_decisions_ts
        return result
''',
)

replace_method(
    "_ensure_orchestrator_bg_running",
    '''    def _ensure_orchestrator_bg_running(self, session_id: str, symbols: list):
        from backend.services.full_auto.orch_background import (
            build_orch_background_host,
            ensure_orchestrator_bg_running,
        )
        host = build_orch_background_host(self)
        ensure_orchestrator_bg_running(session_id, symbols, host)
        self._orch_bg_thread = host.orch_bg_thread
        self._orch_bg_session_id = host.orch_bg_session_id
        self._orch_bg_symbols = host.orch_bg_symbols
        self._orch_bg_running = host.orch_bg_running
        self._last_unified_snapshot = host.last_unified_snapshot
        self._market_scan_cache = host.market_scan_cache
        self._market_scan_cache_ts = host.market_scan_cache_ts
        self._last_orch_decisions = host.last_orch_decisions
        self._last_orch_decisions_ts = host.last_orch_decisions_ts
''',
)

# ── C ────────────────────────────────────────────────────────────────
replace_method(
    "_ai_dynamic_position_pct",
    '''    def _ai_dynamic_position_pct(self, confidence: int, volatility: float,
                                  open_position_count: int,
                                  tier: str = "mid",
                                  tier_budget_pct: float = 0.0) -> float:
        from backend.services.full_auto.decision_sizing import ai_dynamic_position_pct
        return ai_dynamic_position_pct(
            confidence, volatility, open_position_count,
            tier=tier, tier_budget_pct=tier_budget_pct,
        )
''',
)

replace_method(
    "_apply_tdi_position_advice",
    '''    def _apply_tdi_position_advice(
        self,
        symbol: str,
        base_pct: float,
        confidence: int,
        volatility: float,
        open_position_count: int,
        tier: str = "mid",
        tier_budget_pct: float = 0.0,
        equity: float = 0.0,
        regime: str = "ranging",
        base_direction: str = "hold",
    ):
        from backend.services.full_auto.decision_sizing import apply_tdi_position_advice
        return apply_tdi_position_advice(
            symbol, base_pct, confidence, volatility, open_position_count,
            tier=tier, tier_budget_pct=tier_budget_pct, equity=equity,
            regime=regime, base_direction=base_direction,
        )
''',
)

replace_method(
    "_resolve_alignment_scale",
    '''    def _resolve_alignment_scale(self, sym: str) -> float:
        from backend.services.full_auto.decision_sizing import (
            build_decision_sizing_host,
            resolve_alignment_scale,
        )
        return resolve_alignment_scale(sym, build_decision_sizing_host(self))
''',
)

replace_method(
    "_resolve_decision_leverage",
    '''    def _resolve_decision_leverage(
        self,
        dec: dict,
        sym: str,
        tier: str,
        mkt: dict,
        db: Session,
        account_id: int,
        trade_nature: str = "",
        market_summary: dict = None,
    ) -> tuple:
        from backend.services.full_auto.decision_sizing import resolve_decision_leverage
        return resolve_decision_leverage(
            dec, sym, tier, mkt, db, account_id,
            trade_nature=trade_nature, market_summary=market_summary,
        )
''',
)

replace_method(
    "_resolve_decision_position_pct",
    '''    def _resolve_decision_position_pct(
        self,
        dec: dict,
        confidence: int,
        vol_value: float,
        open_position_count: int,
        tier: str,
        tier_budget_pct: float,
        total_equity: float,
        market_regime: str,
        sym: str,
        action: str,
    ) -> tuple:
        from backend.services.full_auto.decision_sizing import (
            build_decision_sizing_host,
            resolve_decision_position_pct,
        )
        return resolve_decision_position_pct(
            dec, confidence, vol_value, open_position_count, tier,
            tier_budget_pct, total_equity, market_regime, sym, action,
            build_decision_sizing_host(self),
        )
''',
)

replace_method(
    "_calibrate_confidence",
    '''    def _calibrate_confidence(self, raw_conf: int, action: str, symbol: str,
                               analyst_reports: dict, market_summary: dict) -> int:
        from backend.services.full_auto.decision_sizing import (
            build_decision_sizing_host,
            calibrate_confidence,
        )
        return calibrate_confidence(
            raw_conf, action, symbol, analyst_reports, market_summary,
            build_decision_sizing_host(self),
        )
''',
)

# ── D ────────────────────────────────────────────────────────────────
replace_method(
    "_factor_veto_check",
    '''    def _factor_veto_check(self, db: Session, sym: str, action: str, mode: str = "paper") -> Optional[str]:
        from backend.services.full_auto.tp_sl_gates import (
            build_tp_sl_gates_host,
            factor_veto_check,
        )
        return factor_veto_check(db, sym, action, build_tp_sl_gates_host(self), mode=mode)
''',
)

replace_method(
    "_validate_tp_sl_by_nature",
    '''    def _validate_tp_sl_by_nature(
        self, trade_nature: str, side: str, entry_price: float,
        tp_price, sl_price, symbol: str = "",
    ) -> tuple:
        from backend.services.full_auto.tp_sl_gates import validate_tp_sl_by_nature
        return validate_tp_sl_by_nature(
            trade_nature, side, entry_price, tp_price, sl_price, symbol=symbol,
        )
''',
)

replace_method(
    "_compute_dynamic_min_sl",
    '''    def _compute_dynamic_min_sl(
        self, symbol: str, trade_nature: str, entry_price: float,
        fallback_pct: float = 0.025,
    ) -> float:
        from backend.services.full_auto.tp_sl_gates import compute_dynamic_min_sl
        return compute_dynamic_min_sl(symbol, trade_nature, entry_price, fallback_pct)
''',
)

# ── E ────────────────────────────────────────────────────────────────
replace_method(
    "_live_constitutional_enabled",
    '''    def _live_constitutional_enabled(self, session) -> bool:
        from backend.services.full_auto.live_trading import (
            build_live_trading_host,
            live_constitutional_enabled,
        )
        return live_constitutional_enabled(session, build_live_trading_host(self))
''',
)

replace_method(
    "_fetch_live_account_snapshot",
    '''    def _fetch_live_account_snapshot(self, db: Session, account_id: int) -> dict:
        from backend.services.full_auto.live_trading import fetch_live_account_snapshot
        return fetch_live_account_snapshot(db, account_id)
''',
)

replace_method(
    "_live_constitutional_pre_trade_check",
    '''    def _live_constitutional_pre_trade_check(
        self,
        db: Session,
        session,
        strat,
        decision: dict,
    ) -> tuple:
        from backend.services.full_auto.live_trading import (
            build_live_trading_host,
            live_constitutional_pre_trade_check,
        )
        return live_constitutional_pre_trade_check(
            db, session, strat, decision, build_live_trading_host(self),
        )
''',
)

replace_method(
    "_check_live_constitutional_session_risk",
    '''    def _check_live_constitutional_session_risk(self, db: Session, session) -> None:
        from backend.services.full_auto.live_trading import (
            build_live_trading_host,
            check_live_constitutional_session_risk,
        )
        host = build_live_trading_host(self)
        check_live_constitutional_session_risk(db, session, host)
        self._defensive_entered_at = host.defensive_entered_at
''',
)

replace_method(
    "_execute_live_trade",
    '''    def _execute_live_trade(self, db: Session, session, strat, decision: dict):
        from backend.services.full_auto.live_trading import (
            build_live_trading_host,
            execute_live_trade,
        )
        execute_live_trade(db, session, strat, decision, build_live_trading_host(self))
''',
)

# ── F ────────────────────────────────────────────────────────────────
replace_method(
    "_resolve_independent_strategy",
    '''    def _resolve_independent_strategy(self, db: Session, session, sym_u: str, tier: str):
        from backend.services.full_auto.midlong_helpers import (
            build_midlong_helpers_host,
            resolve_independent_strategy,
        )
        return resolve_independent_strategy(
            db, session, sym_u, tier, build_midlong_helpers_host(self),
        )
''',
)

replace_method(
    "_try_execute_independent_agent_open",
    '''    def _try_execute_independent_agent_open(
        self,
        *,
        db: Session,
        session,
        sym: str,
        tier: str,
        action: str,
        confidence: int,
        sl_pct: float = 0.0,
        tp_pct: float = 0.0,
        trade_nature: str,
        market_summary: dict,
        session_mode: str = "running",
    ) -> bool:
        from backend.services.full_auto.midlong_helpers import (
            build_midlong_helpers_host,
            try_execute_independent_agent_open,
        )
        return try_execute_independent_agent_open(
            db=db, session=session, sym=sym, tier=tier, action=action,
            confidence=confidence, sl_pct=sl_pct, tp_pct=tp_pct,
            trade_nature=trade_nature, market_summary=market_summary,
            session_mode=session_mode, host=build_midlong_helpers_host(self),
        )
''',
)

replace_method(
    "_record_midlong_factor_snapshots",
    '''    def _record_midlong_factor_snapshots(
        self,
        *,
        db,
        account_id: int,
        trade_id: int,
        symbol: str,
        side: str,
        market_data: dict,
    ) -> None:
        from backend.services.full_auto.midlong_helpers import record_midlong_factor_snapshots
        return record_midlong_factor_snapshots(
            db=db, account_id=account_id, trade_id=trade_id,
            symbol=symbol, side=side, market_data=market_data,
        )
''',
)

replace_method(
    "_persist_independent_scan_log",
    '''    def _persist_independent_scan_log(
        self,
        *,
        account_id: Optional[int],
        symbol: str,
        tier: str,
        trade_nature: str,
        action: str,
        confidence: float,
        reasoning: str,
        agent_source: str,
        cited_fact_ids: Optional[List[str]] = None,
        evidence_audit: Optional[dict] = None,
        market_summary: Optional[dict] = None,
    ) -> None:
        from backend.services.full_auto.midlong_helpers import persist_independent_scan_log
        return persist_independent_scan_log(
            account_id=account_id, symbol=symbol, tier=tier,
            trade_nature=trade_nature, action=action, confidence=confidence,
            reasoning=reasoning, agent_source=agent_source,
            cited_fact_ids=cited_fact_ids, evidence_audit=evidence_audit,
            market_summary=market_summary,
        )
''',
)

replace_method(
    "_inject_midlong_indicators",
    '''    def _inject_midlong_indicators(
        self, market_summary: dict, symbol: str, include_weekly: bool = False
    ) -> None:
        from backend.services.full_auto.midlong_helpers import inject_midlong_indicators
        return inject_midlong_indicators(market_summary, symbol, include_weekly=include_weekly)
''',
)

# ── G ────────────────────────────────────────────────────────────────
replace_method(
    "_check_data_health",
    '''    def _check_data_health(self, session, market_summary: Dict[str, Any],
                           symbols: List[str], db=None):
        from backend.services.full_auto.data_health import (
            build_data_health_host,
            check_data_health,
        )
        host = build_data_health_host(self)
        check_data_health(session, market_summary, symbols, host, db=db)
        self._symbol_frozen_set = host.symbol_frozen_set
        self._health_status = host.health_status
''',
)

path.write_text("".join(lines), encoding="utf-8")
print("monolith lines", len(lines))
print("shim done")
