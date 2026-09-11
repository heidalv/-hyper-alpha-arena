# -*- coding: utf-8 -*-
"""[§90 自查 2026-09-11] 审计口径是否被**已归档测试账户**污染？（只读）

背景：`research` 层 30 天 −$413.08 落在账号 **147/149**，而 `accounts` 表里这两个账号的名字是
**`[已归档-测试残留] …`** ⇒ 不是策略资金，是测试残留。于是必须自查：
**我此前发布的中长线数字（mid/long 净额、peak 分桶、long 层止损审计）里有没有混进这些账户？**

本脚本逐口径对比「全部账户」vs「仅 account_id=14（小资金 PAPER）」。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
from dotenv import load_dotenv  # noqa: E402

load_dotenv(str(ROOT / ".env"), override=False)

from backend.database.connection import SessionLocal  # noqa: E402
from sqlalchemy import text  # noqa: E402

NET = ("(case when lower(side) in ('long','buy') then (close_price-entry_price)*size "
       "else -(close_price-entry_price)*size end) "
       "+ coalesce(partial_realized_pnl,0) - coalesce(partial_fee_paid,0)")
LIVE = 14


def main() -> int:
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    db = SessionLocal()
    try:
        db.execute(text("set app.is_admin='on'"))
        print("=" * 100)
        print(f"账户口径自查（近 {days} 天）—— 全部账户 vs 仅 account_id={LIVE}")
        print("=" * 100)
        print("\n① 按层 × 账户：笔数与净额")
        rows = db.execute(text(f"""
            select lower(coalesce(timeframe_tier,'?')), account_id, count(*), round(sum({NET})::numeric,2)
            from paper_positions
            where status='closed' and closed_at >= now() - interval '{days} day'
            group by 1,2 order by 1, 3 desc
        """)).fetchall()
        print(f"  {'层':10s} {'账户':>6s} {'笔数':>6s} {'净额$':>10s}")
        for t, a, n, net in rows:
            print(f"  {t:10s} {int(a):>6} {int(n):>6} {net:>10}")

        print("\n② mid/long 核心口径对照（我在报告里用过的数字）")
        for label, where in (("全部账户", "1=1"), (f"仅账户 {LIVE}", f"account_id={LIVE}")):
            r = db.execute(text(f"""
                select count(*), round(sum({NET})::numeric,2),
                       round(avg({NET})::numeric,3),
                       round(100.0*count(*) filter (where {NET} > 0)/count(*),1)
                from paper_positions
                where status='closed' and closed_at >= now() - interval '{days} day'
                  and lower(coalesce(timeframe_tier,'')) in ('mid','long') and {where}
            """)).fetchone()
            print(f"  {label:12s} n={int(r[0]):>3} 净额 {r[1]:>9} 单笔 {r[2]:>7} 胜率 {r[3]:>5}%")

        print("\n③ peak 分桶对照（mid/long）")
        for label, where in (("全部账户", "1=1"), (f"仅账户 {LIVE}", f"account_id={LIVE}")):
            r = db.execute(text(f"""
                select count(*) filter (where coalesce(peak_pnl_pct,0) < 0.02) as lo,
                       count(*) as n,
                       round(sum({NET}) filter (where coalesce(peak_pnl_pct,0) < 0.02)::numeric,2) as lo_net,
                       round(sum({NET}) filter (where coalesce(peak_pnl_pct,0) >= 0.02)::numeric,2) as hi_net
                from paper_positions
                where status='closed' and closed_at >= now() - interval '{days} day'
                  and lower(coalesce(timeframe_tier,'')) in ('mid','long') and {where}
            """)).fetchone()
            print(f"  {label:12s} peak<2% {int(r[0])}/{int(r[1])} 笔（净额 {r[2]}）｜peak≥2% 净额 {r[3]}")

        print("\n④ 归档/测试账户清单（`accounts.name` 含已归档/测试字样）")
        accs = db.execute(text("""
            select id, name, account_type from accounts
            where name ilike '%归档%' or name ilike '%测试%' or name ilike '%test%'
            order by id
        """)).fetchall()
        for i, nm, ty in accs:
            cnt = db.execute(text("select count(*) from paper_positions where account_id=:a"),
                             {"a": int(i)}).scalar()
            print(f"  #{int(i):<4} {str(nm)[:40]:40s} {ty:6s} 历史仓位 {int(cnt)}")
    finally:
        db.close()
    print("\n⇒ 结论口径：**发布数字一律按 account_id=14（活跃 PAPER 账户）**；"
          "\n   归档/测试账户只用于研究，不参与业绩口径。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
