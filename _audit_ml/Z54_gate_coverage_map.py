# -*- coding: utf-8 -*-
"""Z54：组合闸的**入口覆盖图**（第 10 轮 (c) 项）。

问题：`check_portfolio_open_allowed`（净敞口 + 并发上限）全仓**只有一个生产调用点**
（`midlong_helpers.py:760`，经 `try_execute_independent_agent_open`）。
其余入口若直接 `paper_engine.place_order`，则**完全不受组合风控约束**。

本脚本：
  A. 静态列出 mid/long 相关入口是否经过闸；
  B. 用真实成交按 `strategy_id` 家族统计「可能绕过闸」的开仓占比。
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sqlalchemy import create_engine, text  # noqa: E402

URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")


def main() -> int:
    print("=== A. 入口 → 是否经组合闸（静态）===")
    paths = [
        ("mlto brain → midlong_executor → try_execute_independent_agent_open",
         "✅ 经闸（midlong_helpers:760）"),
        ("master_execution:1017 → host.try_execute_independent_agent_open",
         "✅ 经闸"),
        ("trend_e1_engine:413 → paper_engine.place_order", "❌ **绕过闸**"),
        ("master_execution:2713/2786/2981/3062 → paper_engine.place_order", "❌ **绕过闸**"),
        ("midlong_factor_route.factor_route_open", "❓ 需确认（未直接调用 place_order）"),
        ("midlong_position_manager:868 → paper_engine.place_order（加仓/补仓）", "❌ **绕过闸**"),
        ("paper_execution:588 → paper_engine.place_order", "❌ **绕过闸**"),
    ]
    for p, v in paths:
        print(f"  {v:<34}{p}")

    print("\n=== B. 真实成交：按家族看开仓路径占比（近 30 天 mid/long）===")
    eng = create_engine(URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        rows = c.execute(text("""
            select coalesce(strategy_id,'(null)') sid, count(*) n
            from paper_positions
            where timeframe_tier in ('mid','long')
              and opened_at >= now() - interval '30 days'
            group by 1 order by 2 desc
        """)).fetchall()
        tot = sum(r[1] for r in rows)
        fam = defaultdict(int)
        for sid, n in rows:
            s = (sid or "").lower()
            for pref in ("trend_e1", "tpl_", "gen_", "auto_", "scalp"):
                if s.startswith(pref):
                    fam[pref] += n
                    break
            else:
                fam["其它"] += n
        print(f"  30 天 mid/long 开仓合计 = {tot}")
        for k, v in sorted(fam.items(), key=lambda x: -x[1]):
            flag = "← 已知可绕过组合闸" if k == "trend_e1" else ""
            print(f"    {k:<10}{v:>5}  ({v/max(tot,1):.1%})  {flag}")

        print("\n=== C. 按 entry_source（9/6 起有该字段）===")
        # exit_state_json 的 open_metadata.entry_source
        rows2 = c.execute(text("""
            select coalesce(nullif(exit_state_json->'open_metadata'->>'entry_source',''), '(未标注)') src,
                   count(*) n
            from paper_positions
            where timeframe_tier in ('mid','long') and opened_at >= '2026-09-06'
            group by 1 order by 2 desc
        """)).fetchall()
        for r in rows2:
            print(f"    {str(r[0]):<14}{r[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
