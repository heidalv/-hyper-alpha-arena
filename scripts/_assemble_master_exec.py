"""Assemble master_execution.py from extracted body."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
body = (ROOT / "backend/services/full_auto/_master_exec_body.tmp").read_text(encoding="utf-8")

header = '''"""Master 总控决策执行 — 从 monolith _execute_master_decisions 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class MasterExecutionHost:
    """monolith 状态与回调切片。"""

    market_scan_cache: Dict[str, Any]
    partial_close_tracker: Dict[str, Any]
    deferred_signals: Dict[str, Any]
    last_reduce_time: Dict[str, Any]
    position_last_decision_ts: Dict[str, Any]
    master_strat_cache: Dict[str, Any]
    nature_to_tier_map: Dict[str, str]
    position_min_decision_interval: Dict[str, int]
    deferred_max_retries: int
    sub_mgr: Any
    current_decision_tier: str = ""

    clear_master_strat_cache: Callable = field(repr=False, default=lambda: None)
    get_lock_profile: Callable = field(repr=False, default=lambda s: None)
    refresh_positions_local: Callable = field(repr=False, default=lambda *a, **k: (0, 0))
    expand_multi_tier_decisions: Callable = field(repr=False, default=lambda *a, **k: [])
    orchestrator_blocks_open: Callable = field(repr=False, default=lambda *a, **k: (False, ""))
    ensure_bound_strategy: Callable = field(repr=False, default=lambda *a, **k: None)
    load_strategy_by_id: Callable = field(repr=False, default=lambda *a, **k: None)
    execute_paper_trade: Callable = field(repr=False, default=lambda *a, **k: False)
    execute_mlto_lane: Callable = field(repr=False, default=lambda *a, **k: None)
    try_execute_independent_agent_open: Callable = field(repr=False, default=lambda *a, **k: False)
    mark_master_decision_executed: Callable = field(repr=False, default=lambda *a, **k: None)
    backfill_dec_confidence_from_orch: Callable = field(repr=False, default=lambda *a, **k: 0)
    build_midlong_agent_envelope: Callable = field(repr=False, default=lambda *a, **k: {})
    midlong_persistence_allow: Callable = field(repr=False, default=lambda *a, **k: True)
    factor_veto_check: Callable = field(repr=False, default=lambda *a, **k: None)
    get_today_realized_pnl: Callable = field(repr=False, default=lambda *a, **k: 0.0)
    get_account_risk_score: Callable = field(repr=False, default=lambda *a, **k: 50.0)
    tiny_close_allowed_by_hardfact: Callable = field(repr=False, default=lambda *a, **k: (True, ""))
    paper_loss_locks_disabled: Callable = field(repr=False, default=lambda *a, **k: False)
    safe_commit: Callable = field(repr=False, default=lambda *a, **k: None)
    session_trading_mode: Callable = field(repr=False, default=lambda s: "paper")
    extract_ai_position_pct: Callable = field(repr=False, default=lambda *a, **k: None)
    resolve_alignment_scale: Callable = field(repr=False, default=lambda *a, **k: 1.0)
    resolve_decision_leverage: Callable = field(repr=False, default=lambda *a, **k: 10)
    calibrate_confidence: Callable = field(repr=False, default=lambda *a, **k: 50)
    ai_dynamic_position_pct: Callable = field(repr=False, default=lambda *a, **k: 0.0)
    apply_tdi_position_advice: Callable = field(repr=False, default=lambda *a, **k: None)
    get_direction_win_rate: Callable = field(repr=False, default=lambda *a, **k: None)
    get_symbol_direction_wr: Callable = field(repr=False, default=lambda *a, **k: (None, 0))
    log_pipeline_audit: Callable = field(repr=False, default=lambda *a, **k: None)
    validate_tp_sl_by_nature: Callable = field(repr=False, default=lambda *a, **k: (True, ""))
    is_reduce_cooldown_exempt: Callable = field(repr=False, default=lambda *a, **k: False)
    should_evaluate_position: Callable = field(repr=False, default=lambda *a, **k: True)
    record_position_decision: Callable = field(repr=False, default=lambda *a, **k: None)
    clear_deferred_signal: Callable = field(repr=False, default=lambda *a, **k: None)
    deferred_signal_key: Callable = field(repr=False, default=lambda *a, **k: "")
    clear_hold_timeout_queue_entry: Callable = field(repr=False, default=lambda *a, **k: None)
    event_scope_label: Callable = field(repr=False, default=lambda *a, **k: "")
    append_event: Callable = field(repr=False, default=lambda *a, **k: None)
    defensive_reduce_cap: float = 0.25


def build_master_execution_host(svc) -> MasterExecutionHost:
    host = MasterExecutionHost(
        market_scan_cache=svc._market_scan_cache,
        partial_close_tracker=svc._partial_close_tracker,
        deferred_signals=svc._deferred_signals,
        last_reduce_time=svc._last_reduce_time,
        position_last_decision_ts=svc._position_last_decision_ts,
        master_strat_cache=getattr(svc, "_master_strat_cache", {}),
        nature_to_tier_map=svc._NATURE_TO_TIER_MAP,
        position_min_decision_interval=svc._POSITION_MIN_DECISION_INTERVAL,
        deferred_max_retries=getattr(svc, "_DEFERRED_MAX_RETRIES", 3),
        sub_mgr=svc._sub_mgr,
        defensive_reduce_cap=getattr(svc, "_defensive_reduce_cap", 0.25),
        clear_master_strat_cache=svc._clear_master_strat_cache,
        get_lock_profile=svc._get_lock_profile,
        refresh_positions_local=svc._refresh_positions_local,
        expand_multi_tier_decisions=svc._expand_multi_tier_decisions,
        orchestrator_blocks_open=svc._orchestrator_blocks_open,
        ensure_bound_strategy=svc._ensure_bound_strategy,
        load_strategy_by_id=svc._load_strategy_by_id,
        execute_paper_trade=svc._execute_paper_trade,
        execute_mlto_lane=svc._execute_mlto_lane,
        try_execute_independent_agent_open=svc._try_execute_independent_agent_open,
        mark_master_decision_executed=svc._mark_master_decision_executed,
        backfill_dec_confidence_from_orch=svc._backfill_dec_confidence_from_orch,
        build_midlong_agent_envelope=svc._build_midlong_agent_envelope,
        midlong_persistence_allow=svc._midlong_persistence_allow,
        factor_veto_check=svc._factor_veto_check,
        get_today_realized_pnl=svc._get_today_realized_pnl,
        get_account_risk_score=svc._get_account_risk_score,
        tiny_close_allowed_by_hardfact=svc._tiny_close_allowed_by_hardfact,
        paper_loss_locks_disabled=svc._paper_loss_locks_disabled,
        safe_commit=svc._safe_commit,
        session_trading_mode=svc._session_trading_mode,
        extract_ai_position_pct=svc._extract_ai_position_pct,
        resolve_alignment_scale=svc._resolve_alignment_scale,
        resolve_decision_leverage=svc._resolve_decision_leverage,
        calibrate_confidence=svc._calibrate_confidence,
        ai_dynamic_position_pct=svc._ai_dynamic_position_pct,
        apply_tdi_position_advice=svc._apply_tdi_position_advice,
        get_direction_win_rate=svc._get_direction_win_rate,
        get_symbol_direction_wr=svc._get_symbol_direction_wr,
        log_pipeline_audit=svc._log_pipeline_audit,
        validate_tp_sl_by_nature=svc._validate_tp_sl_by_nature,
        is_reduce_cooldown_exempt=svc._is_reduce_cooldown_exempt,
        should_evaluate_position=svc._should_evaluate_position,
        record_position_decision=svc._record_position_decision,
        clear_deferred_signal=svc._clear_deferred_signal,
        deferred_signal_key=svc._deferred_signal_key,
        clear_hold_timeout_queue_entry=svc._clear_hold_timeout_queue_entry,
        event_scope_label=svc._event_scope_label,
        append_event=svc._append_event,
    )
    if not hasattr(svc, "_master_strat_cache"):
        svc._master_strat_cache = host.master_strat_cache
    return host


def execute_master_decisions(
    db: Session,
    session,
    account_id: int,
    decisions: List[Dict],
    positions_list: List[Dict],
    active_ids: list,
    market_summary: dict,
    mode: str,
    host: MasterExecutionHost,
    analyst_reports: dict = None,
    balance_info: dict = None,
    orch_directions: dict = None,
    strat_tier_map: dict = None,
) -> None:
'''

# Dedent body: was 8 spaces inside method, needs 4 inside execute_master_decisions
dedented = []
for line in body.splitlines():
    if line.startswith("        "):
        dedented.append("    " + line[8:])
    else:
        dedented.append(line)
body = "\n".join(dedented)

out = ROOT / "backend/services/full_auto/master_execution.py"
out.write_text(header + body + "\n", encoding="utf-8")
print(f"wrote {out} ({out.read_text(encoding='utf-8').count(chr(10))} lines)")
