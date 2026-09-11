# -*- coding: utf-8 -*-
"""Z50：事件流可追溯性审计（第 7 轮 (f) 项）。

核验 5 类一致性问题（真实数据，不是纸面推演）：
  1. 已平仓但**没有任何 `final_trade_outcome` 事件**；
  2. **多个** `final_trade_outcome`（§38.5 的三事件竞态，已加幂等，这里看存量）；
  3. `position.close_reason` 与事件的 `exit_channel` **不一致**；
  4. `reduce_count` 与 `partial_exit_event` 条数**不一致**；
  5. 事件指向**不存在的持仓**（孤儿事件）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sqlalchemy import create_engine, text  # noqa: E402

URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
DAYS = int(os.getenv("Z50_DAYS", "75"))


def main() -> int:
    eng = create_engine(URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        win = f"closed_at >= now() - interval '{DAYS} days'"

        print(f"=== 审计窗口：近 {DAYS} 天已平仓 mid/long ===")
        base = c.execute(text(f"""
            select count(*) from paper_positions
            where timeframe_tier in ('mid','long') and status='closed' and {win}
        """)).scalar()
        print(f"  已平仓数 = {base}")

        print("\n--- 1) 缺 final_trade_outcome 的已平仓仓位 ---")
        r1 = c.execute(text(f"""
            select count(*) from paper_positions p
            where p.timeframe_tier in ('mid','long') and p.status='closed'
              and p.closed_at >= now() - interval '{DAYS} days'
              and not exists (select 1 from position_exit_events e
                              where e.position_id=p.id and e.event_type='final_trade_outcome')
        """)).scalar()
        print(f"  {r1} / {base} 笔（{r1/max(base,1):.1%}）")

        print("\n--- 2) 多个 final_trade_outcome（竞态存量）---")
        for r in c.execute(text(f"""
            select p.id, p.symbol, count(*) n
            from paper_positions p join position_exit_events e on e.position_id=p.id
            where p.timeframe_tier in ('mid','long') and p.status='closed'
              and p.closed_at >= now() - interval '{DAYS} days'
              and e.event_type='final_trade_outcome'
            group by 1,2 having count(*)>1 order by 3 desc limit 10
        """)).fetchall():
            print(f"    #{r[0]} {r[1]} 事件数={r[2]}")

        print("\n--- 3) close_reason 与事件 exit_channel 不一致 ---")
        r3 = c.execute(text(f"""
            select count(*) from (
              select p.id, p.close_reason,
                     (select e.exit_channel from position_exit_events e
                      where e.position_id=p.id and e.event_type='final_trade_outcome'
                      order by e.created_at desc limit 1) ch
              from paper_positions p
              where p.timeframe_tier in ('mid','long') and p.status='closed'
                and p.closed_at >= now() - interval '{DAYS} days'
            ) t
            where ch is not null
              and left(coalesce(close_reason,''), 18) <> left(coalesce(ch,''), 18)
        """)).scalar()
        print(f"  不一致 {r3} 笔（前缀前 18 字符比较；长理由串可能有截断差异）")
        for r in c.execute(text(f"""
            select p.id, p.symbol, left(coalesce(p.close_reason,''),30),
                   left(coalesce((select e.exit_channel from position_exit_events e
                        where e.position_id=p.id and e.event_type='final_trade_outcome'
                        order by e.created_at desc limit 1),''),30)
            from paper_positions p
            where p.timeframe_tier in ('mid','long') and p.status='closed'
              and p.closed_at >= now() - interval '{DAYS} days'
            order by p.closed_at desc limit 5
        """)).fetchall():
            print(f"    #{r[0]} {r[1]}: pos='{r[2]}' vs ev='{r[3]}'")

        print("\n--- 4) reduce_count vs partial_exit_event 条数 ---")
        r4 = c.execute(text(f"""
            select count(*) from (
              select p.id, coalesce(p.reduce_count,0) rc,
                     (select count(*) from position_exit_events e
                      where e.position_id=p.id and e.event_type='partial_exit_event') n
              from paper_positions p
              where p.timeframe_tier in ('mid','long')
                and p.closed_at >= now() - interval '{DAYS} days'
            ) t where rc <> n
        """)).scalar()
        print(f"  不一致 {r4} 笔")

        print("\n--- 5) 孤儿事件（position_id 无对应持仓）---")
        r5 = c.execute(text(f"""
            select count(*) from position_exit_events e
            where e.created_at >= now() - interval '{DAYS} days'
              and not exists (select 1 from paper_positions p where p.id=e.position_id)
        """)).scalar()
        print(f"  {r5} 条")

        print("\n--- 6) 事件类型分布（窗口内）---")
        for r in c.execute(text(f"""
            select event_type, count(*) n from position_exit_events
            where created_at >= now() - interval '{DAYS} days'
            group by 1 order by 2 desc
        """)).fetchall():
            print(f"    {str(r[0]):<24}{r[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
