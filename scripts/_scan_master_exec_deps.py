"""List self._* usages in _execute_master_decisions for host wiring."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
lines = (ROOT / "backend/services/full_auto_trading_service.py").read_text(encoding="utf-8").splitlines()
start = next(i for i, l in enumerate(lines) if l.strip().startswith("def _execute_master_decisions"))
end = next(i for i, l in enumerate(lines) if i > start and l.strip().startswith("def _validate_tp_sl_by_nature"))
chunk = "\n".join(lines[start:end])
methods = sorted(set(re.findall(r"self\.(_[a-zA-Z0-9_]+)", chunk)))
attrs = sorted(set(re.findall(r"self\.(_[A-Z][A-Z0-9_]*)", chunk)))
print("methods", len(methods))
for m in methods:
    print(m)
print("attrs", attrs)
