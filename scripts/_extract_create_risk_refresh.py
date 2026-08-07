"""Extract strategy creation, symbol risk, refresh positions from monolith."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "backend/services/full_auto_trading_service.py"
lines = path.read_text(encoding="utf-8").splitlines(True)
FA = ROOT / "backend/services/full_auto"


def extract_body(start_pat: str, end_pat: str, method: str) -> str:
    start = next(i for i, l in enumerate(lines) if start_pat in l)
    end = next(i for i, l in enumerate(lines) if i > start and end_pat in l)
    chunk = "".join(lines[start:end])
    after = chunk.split(f"def {method}", 1)[1]
    m = re.search(r'"""[\s\S]*?"""\n(.*)', after, re.DOTALL)
    if m:
        return m.group(1).rstrip() + "\n"
    m2 = re.search(r"\)\s*(?:->[^:]*)?:\n(.*)", after, re.DOTALL)
    if not m2:
        raise SystemExit(f"no body for {method}")
    return m2.group(1).rstrip() + "\n"


def apply(text: str, attrs=(), fns=()) -> str:
    text = re.sub(r"\bself\.", "host.", text)
    for a in attrs:
        text = text.replace(f"host._{a}", f"host.{a}")
    for f in fns:
        text = text.replace(f"host._{f}", f"host.{f}")
    return text


# --- refresh positions ---
refresh = extract_body(
    "def _refresh_positions_local(self",
    "# ══════════════════════════════════════════════════",
    "_refresh_positions_local",
)
# end might be wrong if comment appears earlier — use next public API
start = next(i for i, l in enumerate(lines) if "def _refresh_positions_local(self" in l)
end = next(i for i, l in enumerate(lines) if i > start and "def start_session(self" in l)
chunk = "".join(lines[start:end])
after = chunk.split("def _refresh_positions_local", 1)[1]
m = re.search(r'"""[\s\S]*?"""\n(.*)', after, re.DOTALL)
refresh = m.group(1).rstrip() + "\n"
refresh = apply(refresh, attrs=("NATURE_TO_TIER_MAP",), fns=())
(FA / "_refresh_positions_body.tmp").write_text(refresh, encoding="utf-8")
print("refresh", len(refresh.splitlines()))

# --- strategy creation ---
create_methods = [
    ("_try_create_from_template", "def _auto_create_strategy"),
    ("_auto_create_strategy", "def _infer_timeframe_slots"),
    ("_infer_timeframe_slots", "def _infer_timeframe_slot"),
    ("_infer_timeframe_slot", "def _bg_create_strategy"),
    ("_bg_create_strategy", "def _evaluate_dynamic_risk"),
]
for method, end_pat in create_methods:
    body = extract_body(f"def {method}", end_pat, method)
    body = apply(
        body,
        attrs=("strategy_creation_ts", "STRATEGY_CREATION_COOLDOWN"),
        fns=(
            "try_create_from_template", "infer_timeframe_slots", "infer_timeframe_slot",
            "append_event", "get_trading_account_id", "session_trading_mode",
        ),
    )
    # recursive/module calls
    body = body.replace(
        "host.try_create_from_template(",
        "try_create_from_template(",
    )
    body = body.replace(
        "host.infer_timeframe_slots(",
        "infer_timeframe_slots(",
    )
    body = body.replace(
        "host.infer_timeframe_slot(",
        "infer_timeframe_slot(",
    )
    (FA / f"_{method}_body.tmp").write_text(body, encoding="utf-8")
    print(method, len(body.splitlines()))

# --- symbol risk block ---
# evaluate_dynamic_risk
dyn = extract_body(
    "def _evaluate_dynamic_risk(self",
    "class _PerSymbolRiskResult",
    "_evaluate_dynamic_risk",
)
dyn = apply(dyn, attrs=(), fns=("append_event", "should_log_pause_event"))
(FA / "_evaluate_dynamic_risk_body.tmp").write_text(dyn, encoding="utf-8")
print("dynamic_risk", len(dyn.splitlines()))

# PerSymbolRiskResult class
start = next(i for i, l in enumerate(lines) if "class _PerSymbolRiskResult:" in l)
end = next(i for i, l in enumerate(lines) if i > start and "def _update_symbol_daily_pnl" in l)
# include @dataclass above
while start > 0 and ("@dataclass" in lines[start - 1] or lines[start - 1].strip() == ""):
    start -= 1
    if "@dataclass" in lines[start]:
        break
cls_text = "".join(lines[start:end])
# dedent class from class body indent (4 spaces for class inside FullAuto)
cls_lines = []
for line in cls_text.splitlines():
    if line.startswith("    "):
        cls_lines.append(line[4:])
    else:
        cls_lines.append(line)
(FA / "_per_symbol_risk_result.tmp").write_text("\n".join(cls_lines).rstrip() + "\n", encoding="utf-8")

risk_methods = [
    ("_update_symbol_daily_pnl", "def _freeze_symbol_strategies"),
    ("_freeze_symbol_strategies", "def _unfreeze_recovered_symbols"),
    ("_unfreeze_recovered_symbols", "def _check_per_symbol_risk"),
    ("_check_per_symbol_risk", "def _check_global_risk"),
    ("_check_global_risk", "def _run_analyst_system"),
]
risk_attrs = (
    "symbol_daily_pnl", "frozen_symbols", "defensive_entered_at",
    "PEAK_DECAY_GRACE_HOURS", "PEAK_DECAY_RATE_PER_HOUR", "PEAK_DECAY_ACCEL_HOURS",
    "recovery_until", "RECOVERY_DURATION_HOURS", "RECOVERY_POSITION_SCALE",
)
risk_fns = (
    "get_trading_account_id", "get_lock_profile", "append_event",
    "should_log_pause_event", "record_strategy_pause", "clear_strategy_pause_meta",
    "update_symbol_daily_pnl", "freeze_symbol_strategies",
)
for method, end_pat in risk_methods:
    body = extract_body(f"def {method}", end_pat, method)
    body = apply(body, attrs=risk_attrs, fns=risk_fns)
    body = body.replace("host.PerSymbolRiskResult()", "PerSymbolRiskResult()")
    body = body.replace("host._PerSymbolRiskResult()", "PerSymbolRiskResult()")
    body = body.replace(
        "host.update_symbol_daily_pnl(",
        "update_symbol_daily_pnl(",
    )
    body = body.replace(
        "host.freeze_symbol_strategies(",
        "freeze_symbol_strategies(",
    )
    (FA / f"_{method}_body.tmp").write_text(body, encoding="utf-8")
    print(method, len(body.splitlines()))

print("extract done")
