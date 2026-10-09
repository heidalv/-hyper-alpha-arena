# -*- coding: utf-8 -*-
"""[h857] 130 条往返的条件期望分析:哪一类进场/哪一类币/什么条件下能赚。

这是探索模式真正该产出的东西 —— 不是"平均 y",而是**条件平均 y**:
  · 按币  · 按方向  · 按持仓时长档  · 按是否挂单离场  · 按名义大小档
只有样本足够(≥20)的格子才认。
"""
import io
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

rows = [json.loads(x) for x in (ROOT / "data" / "flow_roundtrip_log.jsonl")
        .read_text(encoding="utf-8").splitlines() if x.strip()]
rows = [r for r in rows if r.get("y_bp") is not None]
print(f"样本 {len(rows)} 条往返(全部历史)")

def table(name, keyfn, min_n=20):
    g = defaultdict(list)
    for r in rows:
        g[keyfn(r)].append(float(r["y_bp"]))
    out = []
    for k, ys in g.items():
        if len(ys) >= min_n:
            m = float(np.mean(ys))
            se = float(np.std(ys, ddof=1) / np.sqrt(len(ys))) if len(ys) > 1 else 0.0
            out.append((k, len(ys), m, se, m / se if se > 0 else 0.0))
    out.sort(key=lambda x: -x[2])
    print(f"\n== {name}(n≥{min_n})==")
    for k, n, m, se, t in out:
        print(f"  {str(k):<22} n={n:>3} 平均 {m:+8.2f}bp ± {se:5.2f} t={t:+5.2f}"
              f"{'  ★正' if m > 0 and t > 1.5 else ''}")
    return out

table("按离场路径", lambda r: str(r.get("why") or "?")[:20], min_n=10)
table("按币", lambda r: str(r.get("symbol") or "?"), min_n=20)
table("按是否挂单往返", lambda r: "挂单(0费)" if float(r.get("fee_bp") or 0) == 0
      else "吃单(4bp)", min_n=10)
table("按持仓时长", lambda r: ("≤30s" if float(r.get("hold_sec") or 0) <= 30 else
                            "31-60s" if float(r.get("hold_sec") or 0) <= 60 else
                            "61-120s" if float(r.get("hold_sec") or 0) <= 120 else
                            ">120s"), min_n=15)
