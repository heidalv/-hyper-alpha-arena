# -*- coding: utf-8 -*-
"""Z129: `midlong_portfolio_block` 的真实构成（当前 TOP3，近 5 天 19.9%）+ 阈值核验。

关注：
  1. 具体是哪条子规则在拦（净敞口 / 相关簇 / 并发上限 / 探针豁免）；
  2. 生效阈值（含探针放宽 `NIBBLE_NET_EXPOSURE`）；
  3. 被拦标的后续走势（粗反事实：拦对了还是拦错了）。
"""
from __future__ import annotations

import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

from backend.services.mlto.midlong_direction_audit import _iter_rows  # noqa: E402

SUB = {
    "net_exposure": re.compile(r"net_exposure"),
    "corr_cluster": re.compile(r"corr_cluster"),
    "open_positions": re.compile(r"midlong_open_positions"),
    "other": re.compile(r".*"),
}
sub = Counter()
per_day = Counter()
per_sym = Counter()
samples = {}
n = 0
cutoff = time.time() - 7 * 86400
for row in _iter_rows():
    reason = str(row.get("reason") or "")
    if not reason.startswith("midlong_portfolio_block"):
        continue
    ts = float(row.get("epoch") or 0)
    n += 1
    for k, rx in SUB.items():
        if rx.search(reason):
            sub[k] += 1
            samples.setdefault(k, reason[:160])
            break
    if ts >= cutoff:
        per_day[datetime.fromtimestamp(ts, timezone.utc).strftime("%m-%d")] += 1
        per_sym[str(row.get("symbol") or "?")] += 1

print(f"=== midlong_portfolio_block 全量 {n} 行 ===")
for k, v in sub.most_common():
    print(f"   {v:>7}  {v/max(1,n):>6.1%}  {k}")
    if k in samples:
        print(f"            样本: {samples[k]}")
print("\n近 7 天按日:", dict(sorted(per_day.items())))
print("近 7 天按标的:", dict(per_sym.most_common(10)))

print("\n=== 生效阈值 ===")
from backend.services.mlto import midlong_portfolio_risk as mpr  # noqa: E402
for k, dflt in (("MIDLONG_PORTFOLIO_GATE_ENABLED", True),
                ("MIDLONG_MAX_NET_EXPOSURE_PCT", 1.5),
                ("MIDLONG_NIBBLE_NET_EXPOSURE_PCT", 2.0),
                ("MIDLONG_CORR_CLUSTER_MAX", 2),
                ("MIDLONG_MAX_OPEN_POSITIONS", 4)):
    try:
        if isinstance(dflt, bool):
            v = mpr._cfg_bool(k, dflt)
        elif isinstance(dflt, int) and "MAX" in k and "PCT" not in k:
            v = mpr._cfg_int_allow_zero(k, dflt)
        else:
            v = mpr._cfg_float(k, float(dflt))
    except Exception as e:
        v = f"err {e}"
    import os as _os
    print(f"   {k:<40} env={str(_os.getenv(k)):<8} 生效={v}")
print("   相关簇标的:", mpr._parse_cluster_symbols())
