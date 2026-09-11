# -*- coding: utf-8 -*-
"""Z207：按**时间切片**判断"快速砍仓"是否仍然存在（保护 2026-08-22 才加上）。

避免误报：`_review_min_hold_check`（M0-11）+ `_channel_shadowed(trend_broken)` + mixed 缓冲
都是 2026-08-22 之后才有的；30 天窗口会把保护之前的日子混进来。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT / "backend"))

from sqlalchemy import text as t  # noqa: E402

from backend.database.connection import SessionLocal  # noqa: E402

NET = ("((p.close_price - p.entry_price) * p.size * "
       "case when lower(p.side) in ('long','buy') then 1 else -1 end"
       " + coalesce(p.partial_realized_pnl,0) - coalesce(p.partial_fee_paid,0))")
HOLD = "extract(epoch from (p.closed_at - p.opened_at))/3600.0"

db = SessionLocal()
db.execute(t("set app.is_admin='on'"))
try:
    print("=== 1. trend_broken / master_running_close 的时间切片（是否仍有碎平）===")
    for label, cond in (("近 30 天", "now() - interval '30 days'"),
                        ("2026-08-22 之后", "timestamp '2026-08-22'"),
                        ("近 7 天", "now() - interval '7 days'"),
                        ("近 3 天", "now() - interval '3 days'")):
        rows = db.execute(t(f"""
            select split_part(coalesce(p.close_reason,''),':',1) kind, count(*) n,
                   round(avg({HOLD})::numeric,1) avg_hold,
                   round(sum({NET})::numeric,2) net,
                   sum(case when {NET}>0 then 1 else 0 end) wins
            from paper_positions p
            where p.status='closed' and p.closed_at > {cond}
              and p.timeframe_tier in ('mid','long') and p.close_price is not null
            group by 1 having count(*) >= 1 order by 2 desc limit 8
        """)).fetchall()
        print(f"\n  ── {label} ──")
        for r in rows:
            wr = r[4] / r[1] if r[1] else 0
            print(f"    {str(r[0])[:34]:36s} n={r[1]:4d} 平均持仓={r[2]:>5}h 净={float(r[3] or 0):>9.2f} 胜率={wr:.0%}")

    print("\n=== 2. 是否还有 4h 内被 trend_broken 平掉的仓位（近 7 天）===")
    rows = db.execute(t(f"""
        select p.symbol, p.timeframe_tier, round({HOLD}::numeric,2) h, round({NET}::numeric,2) net,
               coalesce(p.close_reason,'')::text
        from paper_positions p
        where p.status='closed' and p.closed_at > now() - interval '7 days'
          and p.timeframe_tier in ('mid','long') and p.close_price is not null
          and {HOLD} < 4 and coalesce(p.close_reason,'') ilike 'trend_broken%'
        order by p.closed_at desc limit 12
    """)).fetchall()
    print(f"  近 7 天 4h 内 trend_broken 平仓: {len(rows)} 笔")
    for r in rows:
        print(f"    {str(r[0]):6s} {str(r[1]):5s} hold={r[2]}h 净={float(r[3]):>7.2f} | {r[4][:50]}")

    print("\n=== 3. 近 7 天全部 mid/long 平仓的持仓时长分布（保护后口径）===")
    rows = db.execute(t(f"""
        select case when {HOLD} < 2 then '<2h' when {HOLD} < 6 then '2-6h'
                    when {HOLD} < 12 then '6-12h' when {HOLD} < 24 then '12-24h'
                    when {HOLD} < 72 then '24-72h' else '≥72h' end band,
               count(*) n, round(sum({NET})::numeric,2) net,
               sum(case when {NET}>0 then 1 else 0 end) wins
        from paper_positions p
        where p.status='closed' and p.closed_at > now() - interval '7 days'
          and p.timeframe_tier in ('mid','long') and p.close_price is not null
        group by 1 order by min({HOLD})
    """)).fetchall()
    for r in rows:
        wr = r[3] / r[1] if r[1] else 0
        print(f"  {r[0]:8s} n={r[1]:4d} 净={float(r[2] or 0):>9.2f} 胜率={wr:.0%}")
finally:
    db.rollback()
    db.close()
