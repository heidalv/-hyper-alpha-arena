"""Fix leftover self refs in extracted modules."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FA = ROOT / "backend/services/full_auto"

# --- mlto_cycle ---
p = FA / "mlto_cycle.py"
t = p.read_text(encoding="utf-8")
if "\n    @staticmethod\n" in t:
    t = t.split("\n    @staticmethod\n")[0].rstrip() + "\n"

repls = [
    ('getattr(self, "_mlto_handled_keys", None) or set()', "host.mlto_handled_keys"),
    ('getattr(self, "_mlto_handled_lock", None)', "host.mlto_handled_lock"),
    ('getattr(self, "_current_ai_tiers", None)', "host.current_ai_tiers"),
    ('getattr(self, "_last_orch_decisions", {}) or {}', "host.last_orch_decisions or {}"),
    ('getattr(self, "_mlto_handled_keys", None)', "host.mlto_handled_keys"),
    ('float(getattr(self, "_last_orch_decisions_ts", 0) or 0)', "float(host.last_orch_decisions_ts or 0)"),
]
for a, b in repls:
    t = t.replace(a, b)

# ensure handled is a real set bound to host
old = "handled = host.mlto_handled_keys"
if old in t and "if not isinstance(handled, set)" not in t:
    t = t.replace(
        old,
        "handled = host.mlto_handled_keys\n"
        "    if not isinstance(handled, set):\n"
        "        handled = set(handled or [])\n"
        "        host.mlto_handled_keys = handled",
        1,
    )

t = t.replace(
    "            if _handled_set is None:\n"
    "                _handled_set = set()\n"
    "                host.mlto_handled_keys = _handled_set",
    "            if not isinstance(_handled_set, set):\n"
    "                _handled_set = set(_handled_set or [])\n"
    "                host.mlto_handled_keys = _handled_set",
)
p.write_text(t, encoding="utf-8")
print("fixed mlto_cycle, self leftovers:", "self" in t and "getattr(self" in t)

# --- hold_timeout ---
p2 = FA / "hold_timeout_trend_review.py"
t2 = p2.read_text(encoding="utf-8")
t2 = t2.replace(
    'getattr(self, "_last_analyst_reports", {}) or {}',
    "host.last_analyst_reports or {}",
)
p2.write_text(t2, encoding="utf-8")
print("fixed hold_timeout")

# --- quick orch RiskCheckResult ---
p3 = FA / "quick_orchestrator_eval.py"
t3 = p3.read_text(encoding="utf-8")
if "RiskCheckResult" in t3 and "from backend.services.risk_control_service import RiskCheckResult" not in t3:
    needle = "def run_quick_orchestrator_eval(session_id: str, host: QuickOrchHost) -> None:\n"
    insert = (
        needle
        + "    from backend.services.risk_control_service import RiskCheckResult\n"
    )
    if needle in t3:
        t3 = t3.replace(needle, insert, 1)
        p3.write_text(t3, encoding="utf-8")
        print("added RiskCheckResult import")
    else:
        print("WARN: could not find run_quick header")
else:
    print("quick orch RiskCheckResult ok")
