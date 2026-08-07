"""Fix analyst tmp bodies and re-assemble."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def apply_replacements(text: str) -> str:
    replacements = [
        (r"\bself\.", "host.", "regex"),
        ("host._market_scan_cache", "host.market_scan_cache"),
        ("host._long_tier_staged_tp_state", "host.long_tier_staged_tp_state"),
        ("host._tick_symbol_subset", "host.tick_symbol_subset"),
        ("host._pre_screen_results", "host.pre_screen_results"),
        ("host._pre_screen_passed", "host.pre_screen_passed"),
        ("host._mlto_handled_keys", "host.mlto_handled_keys"),
        ("append_event=host._append_event", "append_event=host.append_event"),
    ]
    for item in replacements:
        if len(item) == 3 and item[2] == "regex":
            text = re.sub(item[0], item[1], text)
        else:
            text = text.replace(item[0], item[1])
    for name in (
        "annotate_auto_coin_meta", "append_event", "build_fast_stability_result",
        "clear_master_strat_cache", "execute_ai_decisions", "execute_defensive_analysis",
        "execute_master_decisions", "get_trading_account_id", "inject_orch_scheduled_stubs",
        "maintain_mlto_theses_for_session", "record_ai_failure", "record_ai_success",
        "run_with_timeout", "sync_hold_timeout_alerts", "validate_ai_decisions",
    ):
        text = text.replace(f"host._{name}", f"host.{name}")
    return text

w = apply_replacements((ROOT / "backend/services/full_auto/_analyst_wrapper_body.tmp").read_text(encoding="utf-8"))
u = apply_replacements((ROOT / "backend/services/full_auto/_analyst_unified_body.tmp").read_text(encoding="utf-8"))

w = w.replace("host.run_analyst_system_unified(", "run_analyst_system_unified(")
w = w.replace(
    "run_analyst_system_unified(\n            db, session, account, active_ids, market_summary,\n        )",
    "run_analyst_system_unified(\n            db, session, account, active_ids, market_summary, host,\n        )",
)

# trim unified trailing class junk
_cut = u.rfind('f"分析师异常（不回退）: {str(e)[:60]}", severity="warning")')
if _cut >= 0:
    end_marker = 'f"分析师异常（不回退）: {str(e)[:60]}", severity="warning")'
    idx = u.find(end_marker, _cut)
    u = u[: idx + len(end_marker)] + "\n"

(ROOT / "backend/services/full_auto/_analyst_wrapper_body.tmp").write_text(w, encoding="utf-8")
(ROOT / "backend/services/full_auto/_analyst_unified_body.tmp").write_text(u, encoding="utf-8")
print("fixed tmp bodies")
