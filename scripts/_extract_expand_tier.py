"""Extract _expand_multi_tier_decisions body from monolith."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
lines = (ROOT / "backend/services/full_auto_trading_service.py").read_text(encoding="utf-8").splitlines(True)
start = next(i for i, l in enumerate(lines) if "def _expand_multi_tier_decisions" in l)
end = next(i for i, l in enumerate(lines) if i > start and l.strip().startswith("def _factor_veto_check(self"))
chunk = "".join(lines[start:end])
m = re.search(
    r'"""[\s\S]*?"""\n(.*)',
    chunk.split("def _expand_multi_tier_decisions", 1)[1],
    re.DOTALL,
)
if not m:
    raise SystemExit("body not found")
body = m.group(1).rstrip() + "\n"
# strip trailing return _expanded (keep it) - body should end with return _expanded

replacements = [
    (r"\bself\.", "host.", "regex"),
    ("host._NATURE_TO_TIER_MAP", "host.nature_to_tier_map"),
]
for item in replacements:
    if len(item) == 3 and item[2] == "regex":
        body = re.sub(item[0], item[1], body)
    else:
        body = body.replace(item[0], item[1])

body = body.replace("host._append_event", "host.append_event")

out = ROOT / "backend/services/full_auto/_expand_tier_body.tmp"
out.write_text(body, encoding="utf-8")
print(f"wrote {out} ({body.count(chr(10))} lines)")
