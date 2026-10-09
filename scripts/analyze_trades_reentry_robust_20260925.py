# -*- coding: utf-8 -*-
"""[交易分析 R8] 重入规则的稳健性检验：多个阈值 N + 三个更窄的变体。

R7 结论：6h 禁重入只 +14.33（且拦掉多个赢家）；12h +42.68、24h +31.35 ⇒ **非单调，疑为噪声**。
本脚本：(a) 补 N=1/2/3 的完整阈值曲线；(b) 三个更窄变体：
   V1 仅拦"距上次止损 ≤1h"的重入；
   V2 仅拦"该标的 24h 内已止损 ≥2 次"后的重入；
   V3 仅针对 R7 里最集中的组合（UNI + tpl_mid_range_444876）。
"""
from __future__ import annotations

import sys

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")

from sqlalchemy import text  # noqa: E402

from backend.database.connection import SessionLocal  # noqa: E402

PNL = """
  (CASE WHEN lower(side) IN ('long','buy') THEN (close_price - entry_price)
        ELSE (entry_price - close_price) END) * size
"""
NET = f"(({PNL}) - coalesce(partial_fee_paid,0) - coalesce(final_fee_paid,0))"

with SessionLocal() as s:
    rows = s.execute(text(f"""
        SELECT symbol, timeframe_tier, lower(side), strategy_id, opened_at, closed_at,
               COALESCE(close_reason,''), {NET}
        FROM paper_positions WHERE status='closed' AND closed_at >= '2026-09-15'
        ORDER BY symbol, opened_at
    """)).fetchall()

recs = [dict(sym=r[0], tier=r[1], side=r[2], strat=r[3], op=r[4], cl=r[5],
             reason=r[6], net=float(r[7])) for r in rows]
total = sum(r["net"] for r in recs)
print(f"窗口总净={total:.2f} n={len(recs)}")

print("\n=== 阈值曲线：禁止止损后 N 小时内同标的同向重入 ===")
last_sl = {}
for N in (1, 2, 3, 6, 12, 24, 48):
    last_sl.clear()
    blocked = []
    for r in recs:
        k = (r["sym"], r["side"])
        prev = last_sl.get(k)
        if prev is not None and (r["op"] - prev).total_seconds() / 3600.0 <= N:
            blocked.append(r)
        if r["reason"].startswith("sl"):
            last_sl[k] = r["cl"]
    bnet = sum(x["net"] for x in blocked)
    print(f"  N={N:2d}h  拦 {len(blocked):2d} 笔  其净={bnet:8.2f}  ⇒ 净变化={-bnet:+8.2f}")

print("\n=== V1 仅拦距上次止损 ≤1h 的重入 ===")
last_sl.clear(); v1 = []
for r in recs:
    k = (r["sym"], r["side"]); prev = last_sl.get(k)
    if prev is not None and (r["op"] - prev).total_seconds() / 3600.0 <= 1:
        v1.append(r)
    if r["reason"].startswith("sl"):
        last_sl[k] = r["cl"]
print(f"  拦 {len(v1)} 笔 净={sum(x['net'] for x in v1):8.2f} ⇒ 净变化={-sum(x['net'] for x in v1):+8.2f}")

print("\n=== V2 仅拦'该标的 24h 内已止损≥2次'后的重入 ===")
sl_times = {}
v2 = []
for r in recs:
    hist = [t for t in sl_times.get(r["sym"], []) if (r["op"] - t).total_seconds() / 3600.0 <= 24]
    if len(hist) >= 2:
        v2.append(r)
    if r["reason"].startswith("sl"):
        sl_times.setdefault(r["sym"], []).append(r["cl"])
print(f"  拦 {len(v2)} 笔 净={sum(x['net'] for x in v2):8.2f} ⇒ 净变化={-sum(x['net'] for x in v2):+8.2f}")
for x in v2:
    print(f"    {str(x['op'])[5:16]} {x['sym']:8s} 净={x['net']:7.2f} {x['reason'][:18]}")

print("\n=== V3 仅针对 UNI + tpl_mid_range_444876 ===")
v3 = [r for r in recs if r["sym"] == "UNI" and str(r["strat"]).startswith("tpl_mid_range_444876")]
print(f"  该组合 {len(v3)} 笔 净={sum(x['net'] for x in v3):8.2f} ⇒ 若整组停用，净变化={-sum(x['net'] for x in v3):+8.2f}")
uni_all = [r for r in recs if r["sym"] == "UNI"]
print(f"  （对照：UNI 全部 {len(uni_all)} 笔 净={sum(x['net'] for x in uni_all):8.2f}）")
