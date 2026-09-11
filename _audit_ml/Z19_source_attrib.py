# -*- coding: utf-8 -*-
"""Z19：入场来源层归因——trade_facts.source / strategy_id / resonance / factor_exposures。

Z18 发现 `trade_facts` 有 `source`（来源）、`resonance`（共振）、`factor_exposures`
（因子暴露）三列入场侧信息，且带 `position_id` 可与 `paper_positions` 对齐。
本轮把「先盈利后大亏」按**来源**拆开，看它是否集中在某个来源（若集中，
来源层封堵就是最后一个可落地的杠杆）。
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sqlalchemy import create_engine, text  # noqa: E402

URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")


def main() -> int:
    eng = create_engine(URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        print("=== trade_facts 覆盖度（近 75 天 mid/long）===")
        for r in c.execute(text("""
            select tier, source, count(*) n, min(ts)::date, max(ts)::date
            from trade_facts
            where tier in ('mid','long') and ts >= now() - interval '75 days'
            group by 1,2 order by 1,3 desc
        """)).fetchall():
            print(f"  {str(r[0]):<5}{str(r[1]):<24}{r[2]:>5}  {r[3]} ~ {r[4]}")

        print("\n=== 关联 paper_positions：按 source 的总 USD / 模式率 ===")
        rows = c.execute(text("""
            select f.source, f.tier, count(*) n,
                   sum(coalesce(p.unrealized_pnl,0)+coalesce(p.partial_realized_pnl,0)
                       -coalesce(p.partial_fee_paid,0)) usd,
                   sum(case when p.peak_pnl_pct >= 0.005
                             and coalesce(p.unrealized_pnl,0)+coalesce(p.partial_realized_pnl,0)
                                 -coalesce(p.partial_fee_paid,0) < 0 then 1 else 0 end) pat_n,
                   sum(case when p.peak_pnl_pct >= 0.005
                             and coalesce(p.unrealized_pnl,0)+coalesce(p.partial_realized_pnl,0)
                                 -coalesce(p.partial_fee_paid,0) < 0
                            then coalesce(p.unrealized_pnl,0)+coalesce(p.partial_realized_pnl,0)
                                 -coalesce(p.partial_fee_paid,0) else 0 end) pat_usd,
                   sum(case when p.original_size*p.entry_price > 0
                             and (coalesce(p.unrealized_pnl,0)+coalesce(p.partial_realized_pnl,0)
                                  -coalesce(p.partial_fee_paid,0))/(p.original_size*p.entry_price)*100
                                 <= -2 then 1 else 0 end) big_n
            from trade_facts f join paper_positions p on p.id::text = f.position_id
            where f.tier in ('mid','long') and f.ts >= now() - interval '75 days'
              and p.status='closed'
            group by 1,2 order by 4
        """)).fetchall()
        print(f"  {'source':<24}{'tier':<6}{'n':>5}{'总USD':>10}{'模式n':>7}{'模式USD':>10}"
              f"{'模式率':>8}{'大亏n':>7}")
        for r in rows:
            n = r[2] or 1
            print(f"  {str(r[0]):<24}{str(r[1]):<6}{n:>5}{float(r[3] or 0):>+10.2f}"
                  f"{r[4]:>7}{float(r[5] or 0):>+10.2f}{r[4]/n:>8.3f}{r[6]:>7}")

        print("\n=== strategy_id 维度（近 75 天 mid/long）===")
        rows = c.execute(text("""
            select coalesce(strategy_id,'(null)') s, timeframe_tier, count(*) n,
                   sum(coalesce(unrealized_pnl,0)+coalesce(partial_realized_pnl,0)
                       -coalesce(partial_fee_paid,0)) usd
            from paper_positions
            where timeframe_tier in ('mid','long') and status='closed'
              and closed_at >= now() - interval '75 days'
            group by 1,2 order by 4
        """)).fetchall()
        for r in rows:
            print(f"  {str(r[0]):<42}{str(r[1]):<6}{r[2]:>5}{float(r[3] or 0):>+10.2f}")

        print("\n=== resonance / factor_exposures 是否有值（抽 5 行）===")
        for r in c.execute(text("""
            select position_id, source, resonance, factor_exposures
            from trade_facts where tier in ('mid','long')
            order by ts desc limit 5
        """)).fetchall():
            print(f"  pos={r[0]} source={r[1]} resonance={str(r[2])[:120]} "
                  f"factors={str(r[3])[:160]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
