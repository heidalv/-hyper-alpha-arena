"""Extract _run_health_check body from monolith."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
lines = (ROOT / "backend/services/full_auto_trading_service.py").read_text(encoding="utf-8").splitlines(True)
start = next(i for i, l in enumerate(lines) if "def _run_health_check(self" in l)
end = next(i for i, l in enumerate(lines) if i > start and l.strip().startswith("def _sanitize_market_summary_for_qaa"))
chunk = "".join(lines[start:end])
m = re.search(
    r'"""[\s\S]*?"""\n(.*)',
    chunk.split("def _run_health_check(self", 1)[1],
    re.DOTALL,
)
if not m:
    raise SystemExit("body not found")
body = m.group(1).rstrip() + "\n"
# 截断到 health_check finally 结束，避免带上 monolith 后续类注释
_cut = body.rfind("        db.close()")
if _cut >= 0:
    body = body[: _cut + len("        db.close()")] + "\n"

replacements = [
    (r"\bself\.", "host.", "regex"),
    ("host._market_scan_cache", "host.market_scan_cache"),
    ("host._market_scan_cache_ts", "host.market_scan_cache_ts"),
    ("host._active_db_sessions", "host.active_db_sessions"),
    ("host._current_trace_id", "host.current_trace_id"),
    ("host._defensive_entered_at", "host.defensive_entered_at"),
    ("host._last_orch_bias_by_symbol", "host.last_orch_bias_by_symbol"),
    ("host._last_orch_decisions", "host.last_orch_decisions"),
    ("host._last_orch_decisions_ts", "host.last_orch_decisions_ts"),
    ("host._last_unified_snapshot", "host.last_unified_snapshot"),
    ("host._recovery_until", "host.recovery_until"),
    ("host._strategy_creation_ts", "host.strategy_creation_ts"),
    ("host._sub_mgr", "host.sub_mgr"),
    ("host._unified_tick_count", "host.unified_tick_count"),
    ("host._NATURE_TO_TIER_MAP", "host.nature_to_tier_map"),
    ("host._PEAK_DECAY_GRACE_HOURS", "host.peak_decay_grace_hours"),
    ("host._RECOVERY_DURATION_HOURS", "host.recovery_duration_hours"),
    ("host._RECOVERY_POSITION_SCALE", "host.recovery_position_scale"),
    ("host._STRATEGY_CREATION_COOLDOWN", "host.strategy_creation_cooldown"),
    ('getattr(host, "_midlong_evidence_metrics"', 'getattr(host, "midlong_evidence_metrics"'),
    ('getattr(host, "_last_unified_snapshot"', 'getattr(host, "last_unified_snapshot"'),
]
for item in replacements:
    if len(item) == 3 and item[2] == "regex":
        body = re.sub(item[0], item[1], body)
    else:
        body = body.replace(item[0], item[1])

for name in (
    "active_exchange",
    "adapt_strategy_params",
    "append_event",
    "attach_scalp_advisory_for_ui",
    "auto_create_strategy",
    "bootstrap_market_summary",
    "cap_paper_active_strategies",
    "check_data_health",
    "check_live_constitutional_session_risk",
    "check_per_symbol_risk",
    "ensure_market_prices",
    "evaluate_dynamic_risk",
    "evaluate_strategy_switches",
    "freeze_symbol_strategies",
    "get_trading_account_id",
    "infer_timeframe_slots",
    "invalidate_session_status_cache",
    "is_champion_strategy",
    "live_constitutional_enabled",
    "normalize_orchestrator_for_ui",
    "orch_payload_from_decision",
    "paper_auto_unlock_session",
    "paper_loss_locks_disabled",
    "pause_champion_strategy",
    "purge_stale_caches",
    "record_strategy_pause",
    "resolve_session_trade_symbols",
    "run_analyst_system",
    "run_v3_factor_pipeline",
    "run_with_timeout",
    "safe_commit",
    "should_log_pause_event",
    "should_switch_mode",
    "should_terminate_strategy",
    "snapshot_strategy_genome",
    "terminate_strategy",
    "unfreeze_recovered_symbols",
    "update_session_stats",
    "update_symbol_daily_pnl",
):
    body = body.replace(f"host._{name}", f"host.{name}")

out = ROOT / "backend/services/full_auto/_health_check_body.tmp"
out.write_text(body, encoding="utf-8")
print(f"wrote {out} ({body.count(chr(10))} lines)")
