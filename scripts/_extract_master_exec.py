"""Extract _execute_master_decisions body from monolith."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
lines = (ROOT / "backend/services/full_auto_trading_service.py").read_text(encoding="utf-8").splitlines(True)
start = next(i for i, l in enumerate(lines) if "def _execute_master_decisions" in l)
end = next(i for i, l in enumerate(lines) if i > start and l.strip().startswith("def _validate_tp_sl_by_nature"))
chunk = "".join(lines[start:end])
m = re.search(
    r'"""[\s\S]*?"""\n(.*)',
    chunk.split("def _execute_master_decisions", 1)[1],
    re.DOTALL,
)
if not m:
    raise SystemExit("body not found")
body = m.group(1).rstrip() + "\n"

replacements = [
    (r"\bself\.", "host.", "regex"),
    ("host._market_scan_cache", "host.market_scan_cache"),
    ("host._partial_close_tracker", "host.partial_close_tracker"),
    ("host._deferred_signals", "host.deferred_signals"),
    ("host._last_reduce_time", "host.last_reduce_time"),
    ("host._position_last_decision_ts", "host.position_last_decision_ts"),
    ("host._master_strat_cache", "host.master_strat_cache"),
    ("host._current_decision_tier", "host.current_decision_tier"),
    ("host._sub_mgr", "host.sub_mgr"),
    ("host._NATURE_TO_TIER_MAP", "host.nature_to_tier_map"),
    ("host._POSITION_MIN_DECISION_INTERVAL", "host.position_min_decision_interval"),
    ("host._DEFERRED_MAX_RETRIES", "host.deferred_max_retries"),
]
for item in replacements:
    if len(item) == 3 and item[2] == "regex":
        body = re.sub(item[0], item[1], body)
    else:
        body = body.replace(item[0], item[1])

# host API without leading underscore
for name in (
    "append_event",
    "clear_master_strat_cache",
    "get_lock_profile",
    "refresh_positions_local",
    "expand_multi_tier_decisions",
    "orchestrator_blocks_open",
    "ensure_bound_strategy",
    "load_strategy_by_id",
    "execute_paper_trade",
    "execute_mlto_lane",
    "try_execute_independent_agent_open",
    "mark_master_decision_executed",
    "backfill_dec_confidence_from_orch",
    "build_midlong_agent_envelope",
    "midlong_persistence_allow",
    "factor_veto_check",
    "get_today_realized_pnl",
    "get_account_risk_score",
    "tiny_close_allowed_by_hardfact",
    "paper_loss_locks_disabled",
    "safe_commit",
    "session_trading_mode",
    "extract_ai_position_pct",
    "resolve_alignment_scale",
    "resolve_decision_leverage",
    "calibrate_confidence",
    "ai_dynamic_position_pct",
    "apply_tdi_position_advice",
    "get_direction_win_rate",
    "get_symbol_direction_wr",
    "log_pipeline_audit",
    "validate_tp_sl_by_nature",
    "is_reduce_cooldown_exempt",
    "should_evaluate_position",
    "record_position_decision",
    "clear_deferred_signal",
    "deferred_signal_key",
    "clear_hold_timeout_queue_entry",
    "event_scope_label",
):
    body = body.replace(f"host._{name}", f"host.{name}")

out = ROOT / "backend/services/full_auto/_master_exec_body.tmp"
out.write_text(body, encoding="utf-8")
print(f"wrote {out} ({body.count(chr(10))} lines)")
