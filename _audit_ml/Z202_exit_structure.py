# -*- coding: utf-8 -*-
"""Z202（出场侧审计·数据先行）：真实平仓原因与出场通道分布 + 每笔的盈亏结构。

口径：
  * `trade_facts`：每笔已平仓的 (tier, pnl, fees, outcome, close_reason)；
  * `position_exit_events`：同一笔的**出场通道**（exit_channel / event_type）；
  * 关注：哪些通道在真正实现盈亏、有没有"通道从未触发"（死闸）、
    以及"先盈利后大亏离场"的形态（peak 高但最终亏）。
"""
from __future__ import annotations

import os
import sys
from collections import Counter, defaultdict
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
    print("=== 1. trade_facts 出场原因分布（近 14 天，mid/long）===")
    rows = db.execute(t(
        """
        select tier, outcome, count(*) n, round(sum(pnl)::numeric,2) gross,
               round(sum(fees)::numeric,2) fees, round((sum(pnl)-sum(fees))::numeric,2) net
        from trade_facts
        where ts > now() - interval '14 days' and tier in ('mid','long')
        group by 1,2 order by 1,3 desc
        """
    )).fetchall()
    for r in rows:
        print(f"  tier={r[0]:5s} outcome={r[1]:6s} n={r[2]:4d} 毛={r[3]:>9} 费={r[4]:>7} 净={r[5]:>9}")

    print("\n=== 2. close_reason 前缀分布（近 14 天，mid/long；取冒号前 40 字）===")
    rows = db.execute(t(
        """
        select coalesce(nullif(split_part(coalesce(close_reason,''),':',1),''),'(空)') as kind,
               tier, count(*) n, round((sum(pnl)-sum(fees))::numeric,2) net
        from trade_facts
        where ts > now() - interval '14 days' and tier in ('mid','long')
        group by 1,2 order by 3 desc limit 25
        """
    )).fetchall()
    for r in rows:
        print(f"  {r[0][:44]:46s} tier={r[1]:5s} n={r[2]:4d} 净={r[3]:>9}")

    print("\n=== 3. 出场通道（position_exit_events.exit_channel / event_type，近 14 天）===")
    rows = db.execute(t(
        """
        select coalesce(exit_channel,'(空)') ch, coalesce(event_type,'(空)') et,
               count(*) n, round(sum(coalesce(pnl,0))::numeric,2) pnl
        from position_exit_events
        where created_at > now() - interval '14 days'
        group by 1,2 order by 3 desc limit 25
        """
    )).fetchall()
    for r in rows:
        print(f"  channel={r[0][:30]:32s} type={r[1][:28]:30s} n={r[2]:4d} pnl={r[3]:>9}")
    if not rows:
        print("  （近 14 天无出场事件）")

    print("\n=== 4.「先盈利后大亏」形态（近 14 天 mid/long，peaked>0 但最终净亏）===")
    rows = db.execute(t(
        """
        select p.symbol, p.timeframe_tier, p.side,
               round(p.peak_unrealized_pnl::numeric,2) peak,
               round(p.peak_pnl_pct::numeric,4) peak_pct,
               round((coalesce(p.realized_pnl,0)+coalesce(p.partial_realized_pnl,0))::numeric,2) gross,
               round(coalesce(p.total_fee_paid,0)::numeric,2) fees,
               p.close_reason, p.opened_at, p.closed_at
        from paper_positions p
        where p.status='closed' and p.closed_at > now() - interval '14 days'
          and p.timeframe_tier in ('mid','long')
          and coalesce(p.peak_unrealized_pnl,0) > 0
          and (coalesce(p.realized_pnl,0)+coalesce(p.partial_realized_pnl,0)-coalesce(p.total_fee_paid,0)) < 0
        order by p.peak_unrealized_pnl desc limit 12
        """
    )).fetchall()
    print(f"  形态笔数（前 12 笔展示）: {len(rows)}")
    for r in rows:
        print(f"  {r[0]:6s} {r[1]:5s} {r[2]:5s} peak=${r[3]:>7} ({r[4]}) 毛={r[5]:>8} 费={r[6]:>6} "
              f"| {str(r[7])[:40]}")
    cnt = db.execute(t(
        """
        select count(*) from paper_positions p
        where p.status='closed' and p.closed_at > now() - interval '14 days'
          and p.timeframe_tier in ('mid','long')
          and coalesce(p.peak_unrealized_pnl,0) > 0
          and (coalesce(p.realized_pnl,0)+coalesce(p.partial_realized_pnl,0)-coalesce(p.total_fee_paid,0)) < 0
        """
    )).scalar()
    tot = db.execute(t(
        """select count(*) from paper_positions p where p.status='closed'
           and p.closed_at > now() - interval '14 days' and p.timeframe_tier in ('mid','long')"""
    )).scalar()
    print(f"  合计: {cnt} / {tot} 笔（{cnt/max(tot,1):.1%}）属于『先盈利后净亏』")
finally:
    db.rollback()
    db.close()
