# -*- coding: utf-8 -*-
"""样本量探查（Z1）：分折验证前的数据可得性检查。

目标：learned 门（up+chg∈[3,6) / chop pos≥60&chg≥2）与「门只作用 mid」这两个
结论都来自 8/10–9/9 这段样本。要做分折/样本外验证，先看：
  1. mid/long 成交按月分布（paper_positions）；
  2. 各月成交的 1h/1d K 线覆盖情况（能否算 chg24/pos24/regime）。
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text  # noqa: E402

ARENA_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
MARKET_URL = os.getenv("MARKET_DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")


def main() -> int:
    eng = create_engine(ARENA_URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        rows = c.execute(text("""
            select to_char(opened_at, 'YYYY-MM') as mon, timeframe_tier, count(*) n,
                   min(opened_at)::date, max(opened_at)::date
            from paper_positions
            where timeframe_tier in ('mid','long') and status='closed'
              and opened_at >= '2026-05-01'
            group by 1,2 order by 1,2
        """)).fetchall()
    print("=== paper_positions 按月 ===")
    for r in rows:
        print("  ", tuple(r))

    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        st_rows = c.execute(text("""
            select to_char(opened_at, 'YYYY-MM') as mon, count(*) n
            from strategy_trades
            where side in ('long','short') and status='closed' and entry_price is not null
              and opened_at >= '2026-05-01'
            group by 1 order by 1
        """)).fetchall()
    print("\n=== strategy_trades 按月（含方向）===")
    for r in st_rows:
        print("  ", tuple(r))

    # K 线覆盖
    with create_engine(MARKET_URL).connect() as c:
        c.execute(text("set statement_timeout='600000'"))
        for per in ("1h", "1d"):
            r = c.execute(text("""
                select exchange, min(timestamp) mn, max(timestamp) mx, count(*) n
                from crypto_klines where period=:p group by exchange order by exchange
            """), {"p": per}).fetchall()
            print(f"\n=== crypto_klines {per} ===")
            for x in r:
                import datetime as _dt
                mn = _dt.datetime.utcfromtimestamp(int(x[1])).date() if x[1] else None
                mx = _dt.datetime.utcfromtimestamp(int(x[2])).date() if x[2] else None
                print(f"   {x[0]:<10} {mn} → {mx}  n={x[3]:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
