# -*- coding: utf-8 -*-
"""中线开仓运行态事实（只读 SELECT）。判据：最近一次 mid 开仓是什么时候、现在持仓几个。"""
from __future__ import annotations

import io
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402
from backend.database.connection import SessionLocal  # noqa: E402

db = SessionLocal()
try:
    db.execute(text("SET app.is_admin='on'"))
    print("=" * 88)
    print("中线（tier=mid）开仓运行态")
    print("=" * 88)

    r = db.execute(text("""
        SELECT status, count(*) FROM paper_positions
        WHERE timeframe_tier = 'mid' GROUP BY status ORDER BY 2 DESC
    """)).fetchall()
    print("\n[1] tier=mid 持仓状态分布")
    for st, n in r:
        print(f"    {st:12s} {n}")

    r = db.execute(text("""
        SELECT symbol, side, status, opened_at, closed_at, entry_price,
               round(coalesce(partial_realized_pnl,0)::numeric,2) AS pnl
        FROM paper_positions WHERE timeframe_tier='mid'
        ORDER BY opened_at DESC NULLS LAST LIMIT 15
    """)).fetchall()
    print("\n[2] 最近 15 条 tier=mid 持仓（按开仓时间倒序）")
    for row in r:
        print(f"    {str(row[0]):10s} {str(row[1]):5s} {str(row[2]):8s} "
              f"open={row[3]} close={row[4]} pnl={row[5]}")

    r = db.execute(text("""
        SELECT date_trunc('day', opened_at) AS d, count(*)
        FROM paper_positions WHERE timeframe_tier='mid' AND opened_at >= now() - interval '10 days'
        GROUP BY 1 ORDER BY 1 DESC
    """)).fetchall()
    print("\n[3] 近 10 天 tier=mid 每日开仓数")
    for d, n in r:
        print(f"    {d}  {n}")

    r = db.execute(text("""
        SELECT max(opened_at) FROM paper_positions WHERE timeframe_tier='mid'
    """)).fetchone()
    print(f"\n[4] 最近一次 mid 开仓时间: {r[0]}")

    r = db.execute(text("""
        SELECT current_setting('server_version'), now()
    """)).fetchone()
    print(f"[5] DB 时间基准: now()={r[1]}")

    # 跨 tier 对比：今天各 tier 开仓数
    r = db.execute(text("""
        SELECT timeframe_tier, count(*) FROM paper_positions
        WHERE opened_at >= date_trunc('day', now()) GROUP BY 1 ORDER BY 2 DESC
    """)).fetchall()
    print("\n[6] 今日各 tier 开仓数")
    for t, n in r:
        print(f"    {str(t):10s} {n}")
finally:
    db.close()

