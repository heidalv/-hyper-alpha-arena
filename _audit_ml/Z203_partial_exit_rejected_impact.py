# -*- coding: utf-8 -*-
"""Z203：`partial_exit_rejected`（减半被 minNotional 拒）的**仓位规模与最终结局**。

判定问题：
  1. 被拒的减仓，其仓位名义有多大？（是不是"太小没法减"）
  2. 这些仓位最后是**全平亏损**还是别的？（即保护动作失效的代价）
  3. 时间分布：这是历史现象还是仍在发生？
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

db = SessionLocal()
db.execute(t("set app.is_admin='on'"))
try:
    print("=== 1. 时间分布（近 20 天，按天）===")
    rows = db.execute(t(
        """
        select date_trunc('day', created_at)::date d, count(*) n
        from position_exit_events where event_type='partial_exit_rejected'
        group by 1 order by 1 desc limit 20
        """
    )).fetchall()
    for r in rows:
        print(f"  {r[0]}  {r[1]:5d}")

    print("\n=== 2. 被拒减仓的仓位规模与最终结局（join paper_positions）===")
    rows = db.execute(t(
        """
        select p.symbol, p.timeframe_tier, p.size, p.entry_price,
               round((p.size*p.entry_price)::numeric,2) notional_at_entry,
               round(coalesce(p.peak_unrealized_pnl,0)::numeric,2) peak,
               round((coalesce(p.realized_pnl,0)+coalesce(p.partial_realized_pnl,0)-coalesce(p.total_fee_paid,0))::numeric,2) net,
               p.status, e.max_dd_pct
        from paper_positions p
        join (
            select position_id, count(*) n,
                   max((regexp_replace(coalesce(metadata_json::text,''), '.*\\((\\d+)%\\).*', '\\1'))::int) as max_dd_pct
            from position_exit_events where event_type='partial_exit_rejected'
            group by position_id
        ) e on e.position_id = p.id
        order by notional_at_entry asc limit 25
        """
    )).fetchall()
    print(f"  {'symbol':7s} {'tier':6s} {'入场名义($)':>12s} {'峰值($)':>9s} {'净($)':>9s} {'状态':7s}")
    for r in rows:
        print(f"  {str(r[0]):7s} {str(r[1]):6s} {float(r[4] or 0):>12.2f} {float(r[5] or 0):>9.2f} "
              f"{float(r[6] or 0):>9.2f} {str(r[7]):7s}")

    print("\n=== 3. 汇总：有多少仓位被拒过减仓、它们合计净额 ===")
    r = db.execute(t(
        """
        select count(distinct p.id) n_pos,
               round(sum(coalesce(p.realized_pnl,0)+coalesce(p.partial_realized_pnl,0)-coalesce(p.total_fee_paid,0))::numeric,2) net,
               round(avg(p.size*p.entry_price)::numeric,2) avg_notional,
               sum(case when p.status='closed' and (coalesce(p.realized_pnl,0)+coalesce(p.partial_realized_pnl,0)-coalesce(p.total_fee_paid,0))<0 then 1 else 0 end) losers
        from paper_positions p
        where p.id in (select distinct position_id from position_exit_events where event_type='partial_exit_rejected')
        """
    )).first()
    if r:
        print(f"  受影响仓位 {r[0]} 个｜平均入场名义 ${r[2]}｜合计净 {r[1]}｜其中亏损 {r[3]} 个")

    print("\n=== 4. minNotional 当前值 ===")
    try:
        from backend.services.exit.feasibility_gate import resolve_min_notional_usd
        for ex in ("binance", "asterdex", "hyperliquid"):
            print(f"  {ex}: ${resolve_min_notional_usd(ex)}")
    except Exception as exc:  # noqa: BLE001
        print("  读取失败:", type(exc).__name__, str(exc)[:120])
finally:
    db.rollback()
    db.close()
