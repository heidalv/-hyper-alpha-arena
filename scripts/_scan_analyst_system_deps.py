"""List self._* usages in analyst system methods for host wiring."""

import re

from pathlib import Path



ROOT = Path(__file__).resolve().parents[1]

lines = (ROOT / "backend/services/full_auto_trading_service.py").read_text(encoding="utf-8").splitlines()

start = next(i for i, l in enumerate(lines) if "def _run_analyst_system(self" in l)

end = next(i for i, l in enumerate(lines) if i > start and l.strip().startswith("def _build_tier_protection"))

chunk = "\n".join(lines[start:end])

methods = sorted(set(re.findall(r"self\.(_[a-zA-Z0-9_]+)", chunk)))

print("count", len(methods))

for m in methods:

    print(m)

