# -*- coding: utf-8 -*-
"""[交易分析 R7] 反事实：若禁止"止损后 N 小时内同标的同向重入"，窗口盈亏会变成多少？

规则（与 .env 的 SYMBOL_RISK_BAN_HOURS=6 对齐，便于直接落地）：
  若某笔开仓时间 落在"同标的上一次止损平仓时刻 + N 小时"之内 ⇒ 视为被该规则拦掉。
同时给出反例检查：<1h 桶里也有赚钱的（breakeven_tp），说明规则不该按"持仓时长"而应按"止损后重入"来定。
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
        SELECT id, symbol, timeframe_tier, side, opened_at, closed_at,
               COALESCE(close_reason,'') reason, {NET} net
        FROM paper_positions
        WHERE status='closed' AND closed_at >= '2026-09-15'
        ORDER BY symbol, opened_at
    """)).fetchall()

rows = [dict(id=r[0], sym=r[1], tier=r[2], side=(r[3] or "").lower(), op=r[4], cl=r[5],
             reason=r[6], net=float(r[7])) for r in rows]
total = sum(r["net"] for r in rows)
print(f"窗口总净 = {total:.2f}  笔数={len(rows)}")

for N in (1, 3, 6, 12, 24):
    last_sl = {}          # (sym, side) -> 最近一次止损平仓时刻
    blocked = []
    for r in rows:
        key = (r["sym"], r["side"])
        prev = last_sl.get(key)
        if prev is not None and (r["op"] - prev).total_seconds() / 3600.0 <= N:
            blocked.append(r)
        if r["reason"].startswith("sl"):
            last_sl[key] = r["cl"]
    bnet = sum(r["net"] for r in blocked)
    print(f"  禁止止损后 {N:2d}h 内同向重入: 拦掉 {len(blocked):2d} 笔  这些笔净={bnet:8.2f}  "
          f"⇒ 反事实窗口净 = {total - bnet:8.2f}（改善 {-bnet:+8.2f}）")

print("\n=== 被 6h 规则拦掉的那批逐笔（反例已剔除赢家）===")
last_sl = {}
for r in rows:
    key = (r["sym"], r["side"])
    prev = last_sl.get(key)
    if prev is not None and (r["op"] - prev).total_seconds() / 3600.0 <= 6:
        gap = (r["op"] - prev).total_seconds() / 3600.0
        print(f"  {str(r['op'])[5:16]} {r['sym']:9s} {r['tier']:5s} {r['side']:5s} 净={r['net']:7.2f} "
              f"(距上次止损 {gap:4.2f}h) {r['reason'][:20]}")
    if r["reason"].startswith("sl"):
        last_sl[key] = r["cl"]

print("\n=== 反例检查：<1h 桶里的赢家（不应被'重入规则'误伤）===")
for r in rows:
    h = (r["cl"] - r["op"]).total_seconds() / 3600.0
    if h < 1 and r["net"] > 0:
        print(f"  {str(r['op'])[5:16]} {r['sym']:9s} {r['tier']:5s} 净={r['net']:7.2f} {h:4.2f}h {r['reason'][:22]}")
