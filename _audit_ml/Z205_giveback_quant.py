# -*- coding: utf-8 -*-
"""Z205（出场侧核心量化）：**"先盈利后大亏离场"到底占多少、亏了多少**。

正确口径（`paper_positions` 没有 realized_pnl/total_fee_paid）：
    毛 = (close_price − entry_price) × size × 方向 + partial_realized_pnl
    净 = 毛 − partial_fee_paid
（与 `portfolio_budget._strategy_drawdown_sigma` 同源；费用不含开仓 order fee，属**保守下限**）

分桶：按峰值浮盈 `peak_pnl_pct` 分级，看"曾经浮盈多少 → 最终净亏"的回吐结构。
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

db = SessionLocal()
db.execute(t("set app.is_admin='on'"))
for days in (14, 30):
    print(f"\n{'='*70}\n窗口：近 {days} 天｜tier in (mid,long) 已平仓\n{'='*70}")
    try:
        r = db.execute(t(f"""
            select count(*) n,
                   round(sum({NET})::numeric,2) net_total,
                   round(sum(case when {NET} > 0 then {NET} else 0 end)::numeric,2) win_sum,
                   round(sum(case when {NET} < 0 then {NET} else 0 end)::numeric,2) loss_sum,
                   sum(case when {NET} > 0 then 1 else 0 end) wins
            from paper_positions p
            where p.status='closed' and p.closed_at > now() - interval '{days} days'
              and p.timeframe_tier in ('mid','long') and p.close_price is not null
        """)).first()
        if r and r[0]:
            print(f"  笔数 {r[0]}｜净合计 {r[1]}｜盈利笔合计 +{r[2]}｜亏损笔合计 {r[3]}｜胜率 {r[4]/r[0]:.1%}")

        print("\n  ──「曾经浮盈 → 最终净亏」结构（按峰值浮盈分级）──")
        rows = db.execute(t(f"""
            select case
                     when coalesce(p.peak_pnl_pct,0) <= 0 then '(从未浮盈)'
                     when p.peak_pnl_pct < 0.02 then '峰值 0-2%'
                     when p.peak_pnl_pct < 0.05 then '峰值 2-5%'
                     when p.peak_pnl_pct < 0.10 then '峰值 5-10%'
                     else '峰值 >10%' end as band,
                   count(*) n,
                   sum(case when {NET} < 0 then 1 else 0 end) n_loss,
                   round(sum({NET})::numeric,2) net,
                   round(avg(coalesce(p.peak_unrealized_pnl,0))::numeric,2) avg_peak_usd
            from paper_positions p
            where p.status='closed' and p.closed_at > now() - interval '{days} days'
              and p.timeframe_tier in ('mid','long') and p.close_price is not null
            group by 1 order by 1
        """)).fetchall()
        for x in rows:
            share = (x[2] / x[1]) if x[1] else 0
            print(f"    {x[0]:12s} 笔数={x[1]:4d} 其中净亏={x[2]:4d}（{share:5.1%}）"
                  f" 净合计={x[3]:>9} 平均峰值浮盈=${x[4]}")

        print("\n  ── 典型样本（峰值浮盈 >$10 但最终净亏，最多 10 笔）──")
        rows = db.execute(t(f"""
            select p.symbol, p.timeframe_tier, round(coalesce(p.peak_unrealized_pnl,0)::numeric,2) peak_usd,
                   round(coalesce(p.peak_pnl_pct,0)::numeric,4) peak_pct,
                   round({NET}::numeric,2) net, coalesce(p.close_reason,'')::text reason,
                   p.opened_at, p.closed_at
            from paper_positions p
            where p.status='closed' and p.closed_at > now() - interval '{days} days'
              and p.timeframe_tier in ('mid','long') and p.close_price is not null
              and coalesce(p.peak_unrealized_pnl,0) > 10 and {NET} < 0
            order by coalesce(p.peak_unrealized_pnl,0) desc limit 10
        """)).fetchall()
        for x in rows:
            print(f"    {str(x[0]):6s} {str(x[1]):5s} peak=${float(x[2]):>7.2f}({float(x[3]):.2%}) "
                  f"净={float(x[4]):>8.2f} | {x[5][:48]}")
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        print("  查询失败:", type(exc).__name__, str(exc)[:200])
db.rollback()
db.close()
