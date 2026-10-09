# -*- coding: utf-8 -*-
"""[交易分析 R9] 探查 K 线表（独立连接，避免事务被污染）。"""
from __future__ import annotations

import sys

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")

from sqlalchemy import text  # noqa: E402

from backend.database.connection import market_engine  # noqa: E402


def q(sql, **kw):
    with market_engine.connect() as c:
        return c.execute(text(sql), kw).fetchall()


tabs = [r[0] for r in q(
    "SELECT table_name FROM information_schema.tables "
    "WHERE table_schema='public' AND table_name LIKE '%kline%' ORDER BY table_name"
)]
print("含 kline 的表:", tabs)

for name in tabs:
    try:
        cols = [r[0] for r in q(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name=:t ORDER BY ordinal_position", t=name
        )]
        n = q(f"SELECT count(*) FROM {name}")[0][0]
        print(f"\n  {name}: 行数={n}")
        print(f"    列: {cols}")
        tcol = next((c for c in ("timestamp", "ts", "open_time", "time") if c in cols), None)
        if tcol:
            a, b = q(f"SELECT min({tcol}), max({tcol}) FROM {name}")[0]
            print(f"    {tcol}: {a} → {b}")
        pcol = next((c for c in ("period", "interval", "timeframe") if c in cols), None)
        if pcol:
            for r in q(f"SELECT {pcol}, count(*) FROM {name} GROUP BY 1 ORDER BY 2 DESC LIMIT 10"):
                print(f"      {pcol}={r[0]}: {r[1]}")
    except Exception as e:  # noqa: BLE001
        print(f"\n  {name}: ERR {str(e)[:100]}")
