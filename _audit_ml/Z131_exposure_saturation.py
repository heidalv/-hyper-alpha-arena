# -*- coding: utf-8 -*-
"""Z131: 净敞口闸是否长期"顶到上限"？（真实持仓重建 + 当前账面）

目的：22,042 次净敞口拦截意味着"书已满"。若长期贴顶，则中长线车道的**结构性瓶颈**是
`MIDLONG_MAX_NET_EXPOSURE_PCT=1.5`，而非入场闸；同时核验 9/9 夜（0.94x）为何没被这道闸挡住。
"""
from __future__ import annotations

import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

from sqlalchemy import text  # noqa: E402
from backend.database.connection import SessionLocal  # noqa: E402

db = SessionLocal()
try:
    print("=== 当前账面（账户 14）===")
    r = db.execute(text(
        "select coalesce(sum(size*entry_price),0) as notional, count(*) "
        "from paper_positions where account_id=14 and status='open'"
    )).first()
    eq = db.execute(text("select total_equity from paper_balances where account_id=14")).scalar()
    print(f"  open 持仓 {r[1]} 个，名义合计 ${float(r[0]):.0f}；权益 ${float(eq or 0):.0f} "
          f"⇒ 净敞口 {float(r[0])/max(1,float(eq or 1)):.0%}（上限 150%）")
    for row in db.execute(text(
        "select id, symbol, side, timeframe_tier, trade_nature, size, entry_price, leverage "
        "from paper_positions where account_id=14 and status='open' order by id"
    )).fetchall():
        n = float(row[5] or 0) * float(row[6] or 0)
        print(f"     #{row[0]} {row[1]:<9} {row[2]:<5} {row[3]}/{row[4]:<12} 名义 ${n:>7.0f} lev={row[7]}")

    print("\n=== 单笔名义分布（近 14 天开仓，mid/long）===")
    rows = db.execute(text(
        "select size*entry_price as n, timeframe_tier from paper_positions "
        "where account_id=14 and opened_at > now() - interval '14 days' "
        "and timeframe_tier in ('mid','long') order by opened_at desc"
    )).fetchall()
    vals = [float(x[0] or 0) for x in rows]
    if vals:
        vals_sorted = sorted(vals)
        import statistics as st
        print(f"  n={len(vals)} 中位 ${st.median(vals):.0f} 均值 ${st.mean(vals):.0f} "
              f"P10 ${vals_sorted[len(vals)//10]:.0f} P90 ${vals_sorted[int(len(vals)*0.9)]:.0f}")

    print("\n=== 历史净敞口 vs 150% 上限（按平仓/开仓事件重建，近 30 天）===")
    # 重建：以 mid/long 持仓的开/平时间序列累计名义
    rows = db.execute(text(
        "select symbol, size*entry_price as n, opened_at, coalesce(closed_at, now()) as c, status "
        "from paper_positions where account_id=14 and timeframe_tier in ('mid','long') "
        "and opened_at > now() - interval '30 days' order by opened_at"
    )).fetchall()
    events = []
    for sym, n, o, c, status in rows:
        events.append((o, +float(n or 0)))
        events.append((c, -float(n or 0)))
    events.sort(key=lambda x: x[0])
    eq_hist = float(eq or 0) or 1.0
    cur = 0.0
    at_cap_min = 0
    total_min = 0
    peak = 0.0
    prev_t = events[0][0] if events else None
    for t, delta in events:
        if prev_t is not None and t > prev_t:
            mins = (t - prev_t).total_seconds() / 60.0
            total_min += mins
            if cur / eq_hist > 1.5:
                at_cap_min += mins
        cur += delta
        peak = max(peak, cur)
        prev_t = t
    print(f"  峰值名义 ${peak:.0f} = {peak/eq_hist:.0%} 权益")
    if total_min > 0:
        print(f"  ≈{at_cap_min/total_min:.1%} 的时间处于 >150% 净敞口（按事件时间加权）")
    print(f"  事件数 {len(events)}；观测跨度 {total_min/1440:.1f} 天")
finally:
    db.close()
