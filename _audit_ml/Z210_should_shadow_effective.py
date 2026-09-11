# -*- coding: utf-8 -*-
"""Z210：按**实际生效**阈值（MIN_N=15 / MAX_WR=0.40）列出"本应被熔断"的通道。

与 Z208 的差别：Z208 按默认 30 笔算；`.env` 实际把最小样本设为 **15** ⇒ 更多通道满足条件。
本脚本给出准确清单（用于 §77 与决策 P19）。
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

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env", override=False)

from sqlalchemy import text as t  # noqa: E402

from backend.database.connection import SessionLocal  # noqa: E402

MIN_N = int(float(os.environ.get("EXIT_CHANNEL_SHADOW_MIN_N", "30") or 30))
MAX_WR = float(os.environ.get("EXIT_CHANNEL_SHADOW_MAX_WR", "0.40") or 0.40)
NET = ("((p.close_price - p.entry_price) * p.size * "
       "case when lower(p.side) in ('long','buy') then 1 else -1 end"
       " + coalesce(p.partial_realized_pnl,0) - coalesce(p.partial_fee_paid,0))")
print(f"生效阈值：MIN_N={MIN_N}（窗口=min({MIN_N},30)）｜MAX_WR={MAX_WR}")

db = SessionLocal()
db.execute(t("set app.is_admin='on'"))
try:
    rows = db.execute(t(f"""
        with r as (
          select split_part(coalesce(p.close_reason,''),':',1) kind, p.timeframe_tier tier,
                 {NET} net,
                 row_number() over (partition by split_part(coalesce(p.close_reason,''),':',1),
                                                 p.timeframe_tier
                                    order by p.closed_at desc) rn
          from paper_positions p
          where p.status='closed' and p.close_price is not null
            and p.timeframe_tier in ('mid','long','short')
        )
        select kind, tier, count(*) n, sum(case when net>0 then 1 else 0 end) wins,
               round(sum(net)::numeric,2) netsum,
               round(avg(net)::numeric,3) avg_net
        from r where rn <= {MIN_N} group by 1,2 having count(*) >= {MIN_N}
        order by 3 desc
    """)).fetchall()
    should, ok = [], []
    for r in rows:
        wr = r[3] / r[2]
        (should if wr < MAX_WR else ok).append((r, wr))
    print(f"\n=== 满足熔断条件（近 {MIN_N} 笔 wr<{MAX_WR:.0%}）的通道: {len(should)} 条 ===")
    print(f"  {'通道':32s} {'tier':6s} {'n':>4s} {'胜率':>6s} {'净额':>9s} {'均笔':>8s}")
    for r, wr in should:
        print(f"  {str(r[0])[:32]:32s} {str(r[1]):6s} {r[2]:>4d} {wr:>6.0%} {float(r[4] or 0):>9.2f} {float(r[5] or 0):>8.3f}")
    print(f"\n=== 达标放行的通道: {len(ok)} 条（前 10）===")
    for r, wr in ok[:10]:
        print(f"  {str(r[0])[:32]:32s} {str(r[1]):6s} n={r[2]:>3d} wr={wr:>4.0%} 净={float(r[4] or 0):>8.2f}")
    tot = sum(float(r[4] or 0) for r, _ in should)
    print(f"\n⇒ 这些通道近 {MIN_N} 笔合计净额: {tot:.2f}")
finally:
    db.rollback()
    db.close()
