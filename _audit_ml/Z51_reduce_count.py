# -*- coding: utf-8 -*-
"""Z51：`reduce_count` 与 `partial_exit_event` 条数不一致的定性。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sqlalchemy import create_engine, text  # noqa: E402

URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")

eng = create_engine(URL)
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    print("=== 不一致的分布（近 75 天 mid/long）===")
    for r in c.execute(text("""
        select t.rc, t.n, count(*) cnt from (
          select p.id, coalesce(p.reduce_count,0) rc,
                 (select count(*) from position_exit_events e
                  where e.position_id=p.id and e.event_type='partial_exit_event') n
          from paper_positions p
          where p.timeframe_tier in ('mid','long')
            and p.closed_at >= now() - interval '75 days'
        ) t where t.rc <> t.n group by 1,2 order by 3 desc limit 12
    """)).fetchall():
        print(f"  reduce_count={r[0]:<4} 事件数={r[1]:<4} → {r[2]} 笔")

    print("\n=== 抽样 6 笔明细 ===")
    for r in c.execute(text("""
        select p.id, p.symbol, p.status, coalesce(p.reduce_count,0) rc, p.size, p.original_size,
               p.partial_realized_pnl, p.close_reason,
               (select count(*) from position_exit_events e
                where e.position_id=p.id and e.event_type='partial_exit_event') n_ev,
               (select count(*) from position_exit_events e
                where e.position_id=p.id and e.event_type='partial_exit_rejected') n_rej
        from paper_positions p
        where p.timeframe_tier in ('mid','long')
          and p.closed_at >= now() - interval '75 days'
        order by p.closed_at desc limit 6
    """)).fetchall():
        print(f"  #{r[0]:<5}{str(r[1]):<9}{str(r[2]):<8}reduce={r[3]:<3} size={r[4]}/{r[5]} "
              f"partial_pnl={r[6]} 事件={r[8]} 被拒={r[9]}  {str(r[7])[:20]}")

    print("\n=== 全库口径（不限窗口）===")
    r = c.execute(text("""
        select
          count(*) filter (where coalesce(reduce_count,0) > 0) with_rc,
          count(*) filter (where exists (select 1 from position_exit_events e
              where e.position_id=paper_positions.id and e.event_type='partial_exit_event')) with_ev,
          count(*) total
        from paper_positions where account_id=14
    """)).mappings().first()
    print(f"  reduce_count>0 的={r['with_rc']}；有 partial_exit_event 的={r['with_ev']}；总={r['total']}")
    print("  → 若两者接近，说明只是计数口径不同（如 reduce_count 计『减仓次数』含拒单重试）")
    print("  → 若差距大，说明事件流或计数字段有一方不可信（需修）")
