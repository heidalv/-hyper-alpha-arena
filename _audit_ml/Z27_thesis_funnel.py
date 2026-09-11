# -*- coding: utf-8 -*-
"""Z27：论题漏斗（mlto_episodes）——流量到底卡在哪一段？（§24 #25）

Z26 证明「放宽门」无样本外支持。Z25 显示 mid/swing 开仓自 9/6 起全部带 source=mlto。
那么流量上限由**论题供给**决定：论题 → accepted → recommend_open → opened → 结局。
本脚本把这条链逐日、逐 tier 铺开。
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

URL = os.getenv("ANALYTICS_DATABASE_URL",
                os.getenv("DATABASE_URL",
                          "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_analytics"))


def main() -> int:
    eng = create_engine(URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        try:
            cols = [r[0] for r in c.execute(text("""
                select column_name from information_schema.columns
                where table_name='mlto_episodes' order by ordinal_position
            """)).fetchall()]
        except Exception as e:
            print("mlto_episodes 不可用:", e)
            return 1
        print(f"mlto_episodes 列: {', '.join(cols)}")

        print("\n=== 近 14 天论题漏斗（逐 tier）===")
        rows = c.execute(text("""
            select tier, count(*) n,
                   sum(case when accepted=1 then 1 else 0 end) acc,
                   sum(case when recommend_open=1 then 1 else 0 end) rec,
                   sum(case when opened=1 then 1 else 0 end) op
            from mlto_episodes
            where created_at >= now() - interval '14 days'
            group by 1 order by 2 desc
        """)).fetchall()
        for r in rows:
            print(f"  {str(r[0]):<6} 论题={r[1]:>5} accepted={r[2]:>5} recommend_open={r[3]:>4} "
                  f"opened={r[4]:>4}")

        print("\n=== 近 14 天逐日（全部 tier）===")
        print(f"  {'日期':<12}{'论题':>6}{'accepted':>10}{'rec_open':>10}{'opened':>8}")
        for r in c.execute(text("""
            select created_at::date d, count(*) n,
                   sum(case when accepted=1 then 1 else 0 end) acc,
                   sum(case when recommend_open=1 then 1 else 0 end) rec,
                   sum(case when opened=1 then 1 else 0 end) op
            from mlto_episodes
            where created_at >= now() - interval '14 days'
            group by 1 order by 1
        """)).fetchall():
            print(f"  {str(r[0]):<12}{r[1]:>6}{r[2]:>10}{r[3]:>10}{r[4]:>8}")

        print("\n=== 近 14 天 opened=1 的结局 ===")
        for r in c.execute(text("""
            select symbol, tier, direction, created_at::date,
                   round(outcome_pnl::numeric,2), round(outcome_pct::numeric,2),
                   round(outcome_hold_hours::numeric,1), outcome_close_reason
            from mlto_episodes
            where created_at >= now() - interval '14 days' and opened=1
            order by created_at
        """)).fetchall():
            print(f"  {str(r[3])} {str(r[0]):<9}{str(r[1]):<6}{str(r[2]):<8}"
                  f"pnl={str(r[4]):>8} pct={str(r[5]):>7} hold={str(r[6]):>6}h {str(r[7] or '')[:26]}")

        print("\n=== 逐 symbol 论题量（近 14 天 TOP15）===")
        for r in c.execute(text("""
            select symbol, count(*) n,
                   sum(case when recommend_open=1 then 1 else 0 end) rec,
                   sum(case when opened=1 then 1 else 0 end) op
            from mlto_episodes
            where created_at >= now() - interval '14 days'
            group by 1 order by 2 desc limit 15
        """)).fetchall():
            print(f"  {str(r[0]):<10}论题={r[1]:>4} rec={r[2]:>3} opened={r[3]:>3}")

        print("\n=== 30 天总量对比 ===")
        for r in c.execute(text("""
            select count(*) n,
                   sum(case when accepted=1 then 1 else 0 end) acc,
                   sum(case when recommend_open=1 then 1 else 0 end) rec,
                   sum(case when opened=1 then 1 else 0 end) op
            from mlto_episodes where created_at >= now() - interval '30 days'
        """)).fetchall():
            print(f"  30 天：论题={r[0]} accepted={r[1]} rec_open={r[2]} opened={r[3]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
