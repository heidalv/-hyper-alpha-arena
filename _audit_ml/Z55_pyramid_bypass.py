# -*- coding: utf-8 -*-
"""Z55：不受组合闸约束的「加仓/补仓」路径影响量化（第 11 轮）。

覆盖图结论（§48）：组合闸只在 3/7 入口生效，其中
**`midlong_position_manager:868` 的加仓路径不受约束**——并发上限按「笔数」计，
而加仓不增加笔数、只增加**名义敞口** ⇒ 净敞口闸被绕过。

`paper_positions` 有 `add_count` / `dca_count` / `dca_total_added` 可量化。
"""
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
    print("=== 加仓/补仓规模（近 75 天 mid/long）===")
    r = c.execute(text("""
        select count(*) n,
               sum(case when coalesce(add_count,0)>0 then 1 else 0 end) n_add,
               sum(case when coalesce(dca_count,0)>0 then 1 else 0 end) n_dca,
               round(coalesce(sum(dca_total_added),0)::numeric,2) sum_dca_added,
               round(coalesce(avg(add_count),0)::numeric,2) avg_add,
               max(coalesce(add_count,0)) max_add
        from paper_positions
        where timeframe_tier in ('mid','long')
          and coalesce(closed_at, opened_at) >= now() - interval '75 days'
    """)).mappings().first()
    for k, v in r.items():
        print(f"  {k} = {v}")

    print("\n=== 加仓的仓位：最终规模 vs 首仓（原名义）===")
    rows = c.execute(text("""
        select id, symbol, timeframe_tier, add_count, dca_count,
               round((original_size*entry_price)::numeric,2) notional0,
               round((size*mark_price)::numeric,2) notional_now,
               round(coalesce(dca_total_added,0)::numeric,2) dca_added,
               leverage, status
        from paper_positions
        where timeframe_tier in ('mid','long')
          and (coalesce(add_count,0) > 0 or coalesce(dca_count,0) > 0)
        order by coalesce(dca_total_added,0) desc limit 12
    """)).fetchall()
    if rows:
        print(f"  {'id':>6}{'sym':<9}{'tier':<6}{'add':>4}{'dca':>4}{'原名义':>10}"
              f"{'当前名义':>10}{'加仓额':>10}{'lev':>5} status")
        for q in rows:
            print(f"  {q[0]:>6}{str(q[1]):<9}{str(q[2]):<6}{q[3] or 0:>4}{q[4] or 0:>4}"
                  f"{q[5] or 0:>10}{q[6] or 0:>10}{q[7] or 0:>10}{q[8]:>5} {q[9]}")
    else:
        print("  （近 75 天无 add_count/dca_count > 0 的仓位）")

    print("\n=== 结论 ===")
    if r and (r["n_add"] or r["n_dca"]):
        print(f"  有加仓记录的仓位 {r['n_add']} 笔、DCA {r['n_dca']} 笔；"
              f"DCA 累计加仓名义 {r['sum_dca_added']}")
        print("  → 这些加仓**不经组合闸**（§48.1），净敞口上限可被绕过")
    else:
        print("  近 75 天**没有**加仓/补仓发生 ⇒ 该绕过路径目前是**潜在**的、未被实际利用；")
        print("  但仍应在收口点加闸，否则一旦启用加仓即失去约束。")
