"""Extract _execute_ai_decisions body from monolith."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
lines = (ROOT / "backend/services/full_auto_trading_service.py").read_text(encoding="utf-8").splitlines(True)
start = next(i for i, l in enumerate(lines) if "def _execute_ai_decisions(self" in l)
end = next(i for i, l in enumerate(lines) if i > start and l.strip().startswith("def _is_unified_executor_on(self"))
chunk = "".join(lines[start:end])
m = re.search(
    r'"""[\s\S]*?"""\n(.*)',
    chunk.split("def _execute_ai_decisions(self", 1)[1],
    re.DOTALL,
)
if not m:
    raise SystemExit("body not found")
body = m.group(1).rstrip() + "\n"

replacements = [
    (r"\bself\.", "host.", "regex"),
    ('getattr(host, "_last_unified_snapshot"', 'getattr(host, "last_unified_snapshot"'),
]
for item in replacements:
    if len(item) == 3 and item[2] == "regex":
        body = re.sub(item[0], item[1], body)
    else:
        body = body.replace(item[0], item[1])

for name in (
    "append_event",
    "ensure_bound_strategy",
    "execute_live_trade",
    "execute_paper_trade",
    "expand_multi_tier_decisions",
    "extract_ai_position_pct",
    "get_trading_account_id",
    "resolve_alignment_scale",
    "resolve_decision_leverage",
):
    body = body.replace(f"host._{name}", f"host.{name}")

out = ROOT / "backend/services/full_auto/_ai_decisions_body.tmp"
out.write_text(body, encoding="utf-8")
print(f"wrote {out} ({body.count(chr(10))} lines)")
