# -*- coding: utf-8 -*-
"""Z206：把"峰值 <2% 的亏损笔"和"无进展闸"对齐 —— 到底是谁在平这些仓、闸门是否可达。

问题链：
  * 30 天里 79% 的 mid/long 交易峰值浮盈 <2%，合计净 −$411（Z205）；
  * 而 no_progress 闸（hold≥36h/72h + peak_R<0.5 + 当前浮亏）近 14 天只触发 1 次。
  ⇒ 量化：这些"没走出来"的仓位到底是**被谁平的**、持有多久、有没有满足 no_progress 的条件。
"""
from __future__ import annotations

import os
import sys
from collections import Counter
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
    for days in (30,):
        print(f"=== 近 {days} 天：按「峰值浮盈档 × 出场通道」交叉（净额 / 笔数 / 平均持仓小时）===")
        rows = db.execute(t(f"""
            select case when coalesce(p.peak_pnl_pct,0) < 0.02 then 'A.峰值<2%'
                        when p.peak_pnl_pct < 0.05 then 'B.峰值2-5%'
                        else 'C.峰值≥5%' end as band,
                   coalesce(split_part(coalesce(p.close_reason,''),':',1),'') as kind,
                   count(*) n, round(sum({NET})::numeric,2) net,
                   round(avg({HOLD})::numeric,1) avg_hold_h
            from paper_positions p
            where p.status='closed' and p.closed_at > now() - interval '{days} days'
              and p.timeframe_tier in ('mid','long') and p.close_price is not null
            group by 1,2 order by 1, 3 desc limit 40
        """)).fetchall()
        for r in rows:
            print(f"  {r[0]:10s} {str(r[1])[:40]:42s} n={r[2]:4d} 净={float(r[3] or 0):>9.2f} 平均持仓={r[4]}h")

        print(f"\n=== 近 {days} 天：持仓时长分布（mid/long）===")
        rows = db.execute(t(f"""
            select case when {HOLD} < 6 then '<6h'
                        when {HOLD} < 12 then '6-12h'
                        when {HOLD} < 24 then '12-24h'
                        when {HOLD} < 36 then '24-36h'
                        when {HOLD} < 72 then '36-72h'
                        else '≥72h' end as hold_band,
                   count(*) n, round(sum({NET})::numeric,2) net,
                   sum(case when {NET} > 0 then 1 else 0 end) wins
            from paper_positions p
            where p.status='closed' and p.closed_at > now() - interval '{days} days'
              and p.timeframe_tier in ('mid','long') and p.close_price is not null
            group by 1 order by min({HOLD})
        """)).fetchall()
        for r in rows:
            wr = r[3] / r[1] if r[1] else 0
            print(f"  {r[0]:8s} n={r[1]:4d} 净={float(r[2] or 0):>9.2f} 胜率={wr:.1%}")

        print(f"\n=== 近 {days} 天：满足 no_progress 字面条件的仓位（hold≥阈值 + 峰值R<0.5 + 当前浮亏）===")
        rows = db.execute(t(f"""
            select p.symbol, p.timeframe_tier, round({HOLD}::numeric,1) hold_h,
                   round(coalesce(p.peak_pnl_pct,0)::numeric,4) peak_pct,
                   round({NET}::numeric,2) net, coalesce(p.sl_price,0) sl, p.entry_price, p.close_price
            from paper_positions p
            where p.status='closed' and p.closed_at > now() - interval '{days} days'
              and p.timeframe_tier in ('mid','long') and p.close_price is not null
              and ((p.timeframe_tier='long' and {HOLD} >= 72) or (p.timeframe_tier='mid' and {HOLD} >= 36))
            order by {HOLD} desc limit 20
        """)).fetchall()
        print(f"  持仓超过阈值的仓位数: {len(rows)}（展示最多 20）")
        for r in rows:
            print(f"    {str(r[0]):6s} {str(r[1]):5s} hold={r[2]}h peak={float(r[3]):.2%} 净={float(r[4]):>8.2f} "
                  f"sl={r[5]}")
        r2 = db.execute(t(f"""
            select count(*) from paper_positions p
            where p.status='closed' and p.closed_at > now() - interval '{days} days'
              and p.timeframe_tier in ('mid','long') and p.close_price is not null
              and ((p.timeframe_tier='long' and {HOLD} >= 72) or (p.timeframe_tier='mid' and {HOLD} >= 36))
        """)).scalar()
        r3 = db.execute(t(f"""
            select count(*) from paper_positions p
            where p.status='closed' and p.closed_at > now() - interval '{days} days'
              and p.timeframe_tier in ('mid','long') and p.close_price is not null
        """)).scalar()
        print(f"  ⇒ 超过 no_progress 时长阈值的占比: {r2}/{r3} = {r2/max(r3,1):.1%}")
finally:
    db.rollback()
    db.close()
