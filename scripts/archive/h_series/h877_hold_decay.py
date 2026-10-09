# -*- coding: utf-8 -*-
"""[h877 底层设计诊断] 挂单往返的 y 按持仓时长拆 —— 分清"价差收益"与"逆向漂移"。

核心怀疑:
  挂单往返 y = 入场捕获(半价差)+ 出场捕获(半价差)+ mid 漂移
  这些币的价差 20~60bp ⇒ 价差本身贡献 +20~60bp;
  而 h828 实测 markout 随持仓恶化:30s −1.6bp → 180s −13.1bp
  ⇒ **逆向漂移随持仓时间累积** ⇒ 持仓越短,往返越接近"纯价差";持仓越长,漂移+止损吃掉它。
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
print("\n== 挂单往返(0 费)按持仓时长 ==")
mk = [r for r in rows if float(r.get("fee_bp") or 0) == 0
      and float(r.get("hold_sec") or 0) > 0]
edges = [0, 15, 30, 45, 60, 90, 120, 180, 10 ** 9]
for lo, hi in zip(edges[:-1], edges[1:]):
    grp = [float(r["y_bp"]) for r in mk if lo <= float(r.get("hold_sec") or 0) < hi]
    if len(grp) >= 8:
        m = float(np.mean(grp))
        se = float(np.std(grp, ddof=1) / np.sqrt(len(grp)))
        t = m / se if se > 0 else 0.0
        print(f"  持仓 [{lo:>3},{hi:>3})s n={len(grp):>3} 平均 y {m:+7.2f}bp "
              f"t={t:+5.2f}{'  ★显著' if t > 2 else ''}")

print("\n== 吃单往返(4bp)按持仓时长 ==")
tk = [r for r in rows if float(r.get("fee_bp") or 0) > 0
      and float(r.get("hold_sec") or 0) > 0]
for lo, hi in zip(edges[:-1], edges[1:]):
    grp = [float(r["y_bp"]) for r in tk if lo <= float(r.get("hold_sec") or 0) < hi]
    if len(grp) >= 8:
        print(f"  持仓 [{lo:>3},{hi:>3})s n={len(grp):>3} 平均 y {np.mean(grp):+7.2f}bp")

print("\n== 挂单往返按币的平均 y 与平均持仓(看是不是价差在主导)==")
by = defaultdict(list)
for r in mk:
    by[str(r.get("symbol"))].append(r)
for s, rs in sorted(by.items(), key=lambda x: -len(x[1]))[:10]:
    if len(rs) < 10:
        continue
    ys = [float(r["y_bp"]) for r in rs]
    hs = [float(r.get("hold_sec") or 0) for r in rs]
    print(f"  {s:<9} n={len(rs):>3} y {np.mean(ys):+7.2f}bp 持仓中位 {np.median(hs):>4.0f}s")
