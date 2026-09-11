# -*- coding: utf-8 -*-
"""Z52：live 模式组合闸失效的修复前置——live 持仓存在哪张表？

`midlong_helpers` 只在 paper 分支取 `_pos_list`/`_portfolio`，live 分支 `_portfolio=None`
→ `collect_midlong_positions(None, None)` 返回 [] → **净敞口闸与并发上限在实盘完全不生效**。
修复前必须确认：live 持仓是否也写在 `paper_positions`（同一 account_id）里。
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
    print("=== accounts 表的交易模式/交易所 ===")
    cols = [r[0] for r in c.execute(text("""
        select column_name from information_schema.columns
        where table_name='accounts' order by ordinal_position
    """)).fetchall()]
    print("  accounts 列:", ", ".join(cols))
    for r in c.execute(text("""
        select id, coalesce(trading_mode,'?') tm, coalesce(selected_exchange,'?') ex,
               coalesce(is_active::text,'?') act, coalesce(account_type,'?') at
        from accounts order by id limit 10
    """)).fetchall():
        print(f"  acct={r[0]:<5} mode={r[1]:<8} exchange={r[2]:<12} active={r[3]:<6} type={r[4]}")

    print("\n=== paper_positions 按 account_id / exchange 分布 ===")
    for r in c.execute(text("""
        select account_id, coalesce(exchange,'(null)') ex, count(*) n,
               sum(case when status='open' then 1 else 0 end) n_open,
               min(opened_at)::date, max(opened_at)::date
        from paper_positions group by 1,2 order by 3 desc limit 12
    """)).fetchall():
        print(f"  acct={r[0]:<5} exchange={r[1]:<14} n={r[2]:<6} open={r[3]:<4} "
              f"{r[4]} ~ {r[5]}")

    print("\n=== 结论判据 ===")
    live_accts = c.execute(text("""
        select count(*) from accounts where lower(coalesce(trading_mode,''))='live'
    """)).scalar()
    print(f"  trading_mode='live' 的账户数 = {live_accts}")
    if live_accts:
        rows = c.execute(text("""
            select p.account_id, count(*) from paper_positions p
            join accounts a on a.id = p.account_id
            where lower(coalesce(a.trading_mode,''))='live' group by 1
        """)).fetchall()
        print(f"  其中在 paper_positions 里有持仓记录的账户 = {len(rows)} 个：{dict(rows)}")
        print("  → 若 >0：live 持仓**也写 paper_positions**，修复=在 live 分支同样取 _pos_list 即可")
    else:
        print("  （当前无 live 账户；修复仍应按「live 也取本地镜像持仓」实现，避免上线时踩坑）")
