"""List self._* usages in _run_health_check for host wiring."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
lines = (ROOT / "backend/services/full_auto_trading_service.py").read_text(encoding="utf-8").splitlines()
start = next(i for i, l in enumerate(lines) if l.strip().startswith("def _run_health_check(self"))
end = next(i for i, l in enumerate(lines) if i > start and l.strip().startswith("def _sanitize_market_summary_for_qaa"))
chunk = "\n".join(lines[start:end])
methods = sorted(set(re.findall(r"self\.(_[a-zA-Z0-9_]+)", chunk)))
attrs = sorted(set(re.findall(r"self\.(_[a-zA-Z0-9_]+)", chunk)))
print("methods", len(methods))
for m in methods:
    print(m)
print("attrs sample", [a for a in attrs if a.isupper() or a[0].isupper()][:20])
