# -*- coding: utf-8 -*-
"""Z130: 净敞口闸的**生效上限**随时间分布（判定 .env 1.5 是否真的在生效）。"""
from __future__ import annotations

import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

from backend.services.mlto.midlong_direction_audit import _iter_rows  # noqa: E402

RX = re.compile(r"net_exposure ([\d.]+)%>([\d.]+)%")
cap_by_day: dict = {}
cap_all = Counter()
first = last = None
n = 0
for row in _iter_rows():
    m = RX.search(str(row.get("reason") or ""))
    if not m:
        continue
    n += 1
    cap = m.group(2)
    cap_all[cap] += 1
    ts = float(row.get("epoch") or 0)
    if ts:
        d = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
        cap_by_day.setdefault(d, Counter())[cap] += 1
        last = last or d

print(f"净敞口拦截 {n} 行；出现过的 cap 值: {dict(cap_all.most_common(8))}")
print("\n按日 × cap：")
for d in sorted(cap_by_day):
    print(f"   {d}  {dict(cap_by_day[d].most_common(4))}")

print("\n=== 当前生效值（进程内）===")
from backend.config import settings as S  # noqa: E402
import os  # noqa: E402
for k in ("MIDLONG_MAX_NET_EXPOSURE_PCT", "MIDLONG_NIBBLE_NET_EXPOSURE_PCT"):
    print(f"   {k}: env={os.getenv(k)}  settings={getattr(S, k, '<未定义>')}")
