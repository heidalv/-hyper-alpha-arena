"""Extract _run_analyst_system + _run_analyst_system_unified from monolith."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
lines = (ROOT / "backend/services/full_auto_trading_service.py").read_text(encoding="utf-8").splitlines(True)
start = next(i for i, l in enumerate(lines) if "def _run_analyst_system(self" in l)
end = next(i for i, l in enumerate(lines) if i > start and l.strip().startswith("def _build_tier_protection"))
chunk = "".join(lines[start:end])

# Split wrapper vs unified
unified_start = chunk.index("def _run_analyst_system_unified")
wrapper_chunk = chunk[:unified_start]
unified_chunk = chunk[unified_start:]

def extract_body(method_chunk: str, method_name: str) -> str:
    m = re.search(
        r'"""[\s\S]*?"""\n(.*)',
        method_chunk.split(f"def {method_name}", 1)[1],
        re.DOTALL,
    )
    if not m:
        raise SystemExit(f"body not found for {method_name}")
    return m.group(1).rstrip() + "\n"

wrapper_body = extract_body(wrapper_chunk, "_run_analyst_system")
unified_body = extract_body(unified_chunk, "_run_analyst_system_unified")

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
def apply_replacements(text: str) -> str:
    for item in replacements:
        if len(item) == 3 and item[2] == "regex":
            text = re.sub(item[0], item[1], text)
        else:
            text = text.replace(item[0], item[1])
    for name in (
        "annotate_auto_coin_meta",
        "append_event",
        "build_fast_stability_result",
        "clear_master_strat_cache",
        "execute_ai_decisions",
        "execute_defensive_analysis",
        "execute_master_decisions",
        "get_trading_account_id",
        "inject_orch_scheduled_stubs",
        "maintain_mlto_theses_for_session",
        "record_ai_failure",
        "record_ai_success",
        "run_with_timeout",
        "sync_hold_timeout_alerts",
        "validate_ai_decisions",
    ):
        text = text.replace(f"host._{name}", f"host.{name}")
    return text

wrapper_body = apply_replacements(wrapper_body)
unified_body = apply_replacements(unified_body)

# trim unified trailing monolith class comments
_cut = unified_body.rfind('f"分析师异常（不回退）: {str(e)[:60]}", severity="warning")')
if _cut >= 0:
    end_marker = 'f"分析师异常（不回退）: {str(e)[:60]}", severity="warning")'
    idx = unified_body.find(end_marker, _cut)
    unified_body = unified_body[: idx + len(end_marker)] + "\n"

# wrapper delegates to module function
wrapper_body = wrapper_body.replace(
    "host.run_analyst_system_unified(",
    "run_analyst_system_unified(",
)
wrapper_body = wrapper_body.replace(
    "run_analyst_system_unified(\n            db, session, account, active_ids, market_summary,\n        )",
    "run_analyst_system_unified(\n            db, session, account, active_ids, market_summary, host,\n        )",
)

out_w = ROOT / "backend/services/full_auto/_analyst_wrapper_body.tmp"
out_u = ROOT / "backend/services/full_auto/_analyst_unified_body.tmp"
out_w.write_text(wrapper_body, encoding="utf-8")
out_u.write_text(unified_body, encoding="utf-8")
print(f"wrote wrapper ({wrapper_body.count(chr(10))} lines) unified ({unified_body.count(chr(10))} lines)")
