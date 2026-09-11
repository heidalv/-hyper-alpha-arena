import re, sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena"); sys.path.insert(0, str(ROOT))
from backend.services.mlto.midlong_direction_audit import _iter_rows
RX = re.compile(r"^L1=")
by_day = Counter()
for row in _iter_rows():
    if not RX.match(str(row.get("reason") or "")):
        continue
    ts = row.get("epoch")
    if ts:
        by_day[datetime.fromtimestamp(float(ts), timezone.utc).strftime("%Y-%m-%d")] += 1
for d, n in sorted(by_day.items()):
    print(f"   {d}  {n}")
