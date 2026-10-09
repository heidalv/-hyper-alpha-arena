# -*- coding: utf-8 -*-
"""看 alpha_market 的 K 线表真实 schema（只读）。"""
from __future__ import annotations

import io
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402
from backend.database.connection import MarketSessionLocal as S  # noqa: E402

db = S()
try:
    db.execute(text("SET app.is_admin='on'"))
    for tbl in ("crypto_klines", "kline_sync_heartbeat", "kline_collection_tasks",
                "market_spot_klines"):
        print("=" * 90)
        cols = db.execute(text("""
            SELECT column_name, data_type FROM information_schema.columns
            WHERE table_name = :t ORDER BY ordinal_position
        """), {"t": tbl}).fetchall()
        print(f"{tbl}: " + ", ".join(f"{c}({d})" for c, d in cols)[:500])
        try:
            n = db.execute(text(f"SELECT count(*) FROM {tbl}")).fetchone()[0]
            print(f"   行数 = {n:,}")
        except Exception as exc:  # noqa: BLE001
            print(f"   行数查询失败: {type(exc).__name__}")
    print("=" * 90)
    print("kline_sync_heartbeat 最近 10 行（同步心跳，直接反映采集是否在跑）")
    try:
        rows = db.execute(text("SELECT * FROM kline_sync_heartbeat ORDER BY 1 DESC LIMIT 10")).fetchall()
        for r in rows:
            print("   ", tuple(r)[:8])
    except Exception as exc:  # noqa: BLE001
        print("   失败:", type(exc).__name__, str(exc)[:120])
finally:
    db.close()
