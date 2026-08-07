"""Assemble health_check_cycle.py from extracted body."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
body = (ROOT / "backend/services/full_auto/_health_check_body.tmp").read_text(encoding="utf-8")

header = '''"""健康检查循环 — 从 monolith _run_health_check 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class HealthCheckHost:
    """monolith 状态与回调切片。"""

    active_db_sessions: Dict[str, Any]
    market_scan_cache: Dict[str, Any]
    market_scan_cache_ts: float
    last_orch_bias_by_symbol: Dict[str, str]
    last_orch_decisions: Any
    last_orch_decisions_ts: float
    last_unified_snapshot: Any
    defensive_entered_at: Dict[str, float]
    recovery_until: Dict[str, float]
    strategy_creation_ts: Dict[str, float]
    unified_tick_count: Dict[str, int]
    sub_mgr: Any
    nature_to_tier_map: Dict[str, str]
    peak_decay_grace_hours: float
    recovery_duration_hours: float
    recovery_position_scale: float
    strategy_creation_cooldown: float
    current_trace_id: str = ""
    midlong_evidence_metrics: Optional[Dict[str, Any]] = None

    purge_stale_caches: Callable = field(repr=False, default=lambda: None)
    active_exchange: Callable = field(repr=False, default=lambda: "paper")
    resolve_session_trade_symbols: Callable = field(repr=False, default=lambda *a, **k: [])
    bootstrap_market_summary: Callable = field(repr=False, default=lambda *a, **k: {})
    check_data_health: Callable = field(repr=False, default=lambda *a, **k: None)
    run_v3_factor_pipeline: Callable = field(repr=False, default=lambda *a, **k: None)
    run_with_timeout: Callable = field(repr=False, default=lambda *a, **k: None)
    orch_payload_from_decision: Callable = field(repr=False, default=lambda *a, **k: {})
    run_analyst_system: Callable = field(repr=False, default=lambda *a, **k: None)
    paper_loss_locks_disabled: Callable = field(repr=False, default=lambda *a, **k: False)
    should_terminate_strategy: Callable = field(repr=False, default=lambda *a, **k: (False, ""))
    is_champion_strategy: Callable = field(repr=False, default=lambda *a, **k: False)
    pause_champion_strategy: Callable = field(repr=False, default=lambda *a, **k: None)
    snapshot_strategy_genome: Callable = field(repr=False, default=lambda *a, **k: None)
    terminate_strategy: Callable = field(repr=False, default=lambda *a, **k: None)
    append_event: Callable = field(repr=False, default=lambda *a, **k: None)
    adapt_strategy_params: Callable = field(repr=False, default=lambda *a, **k: False)
    safe_commit: Callable = field(repr=False, default=lambda *a, **k: True)
    get_trading_account_id: Callable = field(repr=False, default=lambda *a, **k: 0)
    infer_timeframe_slots: Callable = field(repr=False, default=lambda *a, **k: [])
    auto_create_strategy: Callable = field(repr=False, default=lambda *a, **k: None)
    update_session_stats: Callable = field(repr=False, default=lambda *a, **k: None)
    evaluate_dynamic_risk: Callable = field(repr=False, default=lambda *a, **k: None)
    update_symbol_daily_pnl: Callable = field(repr=False, default=lambda *a, **k: None)
    live_constitutional_enabled: Callable = field(repr=False, default=lambda *a, **k: False)
    check_live_constitutional_session_risk: Callable = field(repr=False, default=lambda *a, **k: None)
    paper_auto_unlock_session: Callable = field(repr=False, default=lambda *a, **k: False)
    check_per_symbol_risk: Callable = field(repr=False, default=lambda *a, **k: None)
    should_switch_mode: Callable = field(repr=False, default=lambda *a, **k: True)
    freeze_symbol_strategies: Callable = field(repr=False, default=lambda *a, **k: None)
    unfreeze_recovered_symbols: Callable = field(repr=False, default=lambda *a, **k: None)
    evaluate_strategy_switches: Callable = field(repr=False, default=lambda *a, **k: None)
    cap_paper_active_strategies: Callable = field(repr=False, default=lambda *a, **k: False)
    ensure_market_prices: Callable = field(repr=False, default=lambda *a, **k: None)
    normalize_orchestrator_for_ui: Callable = field(repr=False, default=lambda *a, **k: None)
    attach_scalp_advisory_for_ui: Callable = field(repr=False, default=lambda *a, **k: None)
    record_strategy_pause: Callable = field(repr=False, default=lambda *a, **k: None)
    should_log_pause_event: Callable = field(repr=False, default=lambda *a, **k: True)


def build_health_check_host(svc) -> HealthCheckHost:
    return HealthCheckHost(
        active_db_sessions=svc._active_db_sessions,
        market_scan_cache=svc._market_scan_cache,
        market_scan_cache_ts=svc._market_scan_cache_ts,
        last_orch_bias_by_symbol=svc._last_orch_bias_by_symbol,
        last_orch_decisions=getattr(svc, "_last_orch_decisions", None),
        last_orch_decisions_ts=float(getattr(svc, "_last_orch_decisions_ts", 0) or 0),
        last_unified_snapshot=getattr(svc, "_last_unified_snapshot", None),
        defensive_entered_at=svc._defensive_entered_at,
        recovery_until=svc._recovery_until,
        strategy_creation_ts=svc._strategy_creation_ts,
        unified_tick_count=svc._unified_tick_count,
        sub_mgr=svc._sub_mgr,
        nature_to_tier_map=svc._NATURE_TO_TIER_MAP,
        peak_decay_grace_hours=svc._PEAK_DECAY_GRACE_HOURS,
        recovery_duration_hours=svc._RECOVERY_DURATION_HOURS,
        recovery_position_scale=svc._RECOVERY_POSITION_SCALE,
        strategy_creation_cooldown=svc._STRATEGY_CREATION_COOLDOWN,
        midlong_evidence_metrics=getattr(svc, "_midlong_evidence_metrics", None),
        purge_stale_caches=svc._purge_stale_caches,
        active_exchange=svc._active_exchange,
        resolve_session_trade_symbols=svc._resolve_session_trade_symbols,
        bootstrap_market_summary=svc._bootstrap_market_summary,
        check_data_health=svc._check_data_health,
        run_v3_factor_pipeline=svc._run_v3_factor_pipeline,
        run_with_timeout=svc._run_with_timeout,
        orch_payload_from_decision=svc._orch_payload_from_decision,
        run_analyst_system=svc._run_analyst_system,
        paper_loss_locks_disabled=svc._paper_loss_locks_disabled,
        should_terminate_strategy=svc._should_terminate_strategy,
        is_champion_strategy=svc._is_champion_strategy,
        pause_champion_strategy=svc._pause_champion_strategy,
        snapshot_strategy_genome=svc._snapshot_strategy_genome,
        terminate_strategy=svc._terminate_strategy,
        append_event=svc._append_event,
        adapt_strategy_params=svc._adapt_strategy_params,
        safe_commit=svc._safe_commit,
        get_trading_account_id=svc._get_trading_account_id,
        infer_timeframe_slots=svc._infer_timeframe_slots,
        auto_create_strategy=svc._auto_create_strategy,
        update_session_stats=svc._update_session_stats,
        evaluate_dynamic_risk=svc._evaluate_dynamic_risk,
        update_symbol_daily_pnl=svc._update_symbol_daily_pnl,
        live_constitutional_enabled=svc._live_constitutional_enabled,
        check_live_constitutional_session_risk=svc._check_live_constitutional_session_risk,
        paper_auto_unlock_session=svc._paper_auto_unlock_session,
        check_per_symbol_risk=svc._check_per_symbol_risk,
        should_switch_mode=svc._should_switch_mode,
        freeze_symbol_strategies=svc._freeze_symbol_strategies,
        unfreeze_recovered_symbols=svc._unfreeze_recovered_symbols,
        evaluate_strategy_switches=svc._evaluate_strategy_switches,
        cap_paper_active_strategies=svc._cap_paper_active_strategies,
        ensure_market_prices=svc._ensure_market_prices,
        normalize_orchestrator_for_ui=svc._normalize_orchestrator_for_ui,
        attach_scalp_advisory_for_ui=svc._attach_scalp_advisory_for_ui,
        record_strategy_pause=svc._record_strategy_pause,
        should_log_pause_event=svc._should_log_pause_event,
    )


def run_health_check(
    session_id: str,
    host: HealthCheckHost,
    *,
    maintenance_only: bool = False,
) -> None:
'''

dedented = []
for line in body.splitlines():
    if line.startswith("        "):
        dedented.append("    " + line[8:])
    else:
        dedented.append(line)
body = "\n".join(dedented)

out = ROOT / "backend/services/full_auto/health_check_cycle.py"
out.write_text(header + body + "\n", encoding="utf-8")
print(f"wrote {out} ({out.read_text(encoding='utf-8').count(chr(10))} lines)")
