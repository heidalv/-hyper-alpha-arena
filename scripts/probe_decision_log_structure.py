# -*- coding: utf-8 -*-
"""看 ai_decision_logs.decision_snapshot 的原始结构。只读。"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402
from backend.database.connection import AnalyticsSessionLocal  # noqa: E402

SQL_SAMPLE = """
SELECT id, symbol, created_at, decision_snapshot
FROM ai_decision_logs
WHERE decision_snapshot IS NOT NULL AND decision_snapshot <> ''
ORDER BY id DESC LIMIT 5
"""

db = AnalyticsSessionLocal()
try:
    db.execute(text("SET app.is_admin='on'"))
    rows = db.execute(text(SQL_SAMPLE)).fetchall()
    print(f"样本数 = {len(rows)}")
    for rid, sym, ts, snap in rows:
        print(f"--- id={rid} symbol={sym} ts={ts} len={len(snap)} ---")
        print("   " + str(snap)[:360].replace("\n", " "))
        try:
            d = json.loads(snap)
            print("   JSON keys:", sorted(d.keys())[:18])
        except Exception as e:  # noqa: BLE001
            print("   JSON 解析失败:", type(e).__name__, str(e)[:80])

    print("\n=== decision_snapshot 含 _hub_decision_log 的条数（全表）===")
    print("  ", db.execute(text(
        "SELECT COUNT(*) FROM ai_decision_logs "
        "WHERE decision_snapshot LIKE '%_hub_decision_log%'")).fetchone()[0])
    r = db.execute(text("SELECT max(id), max(created_at), COUNT(*) FROM ai_decision_logs")).fetchone()
    print(f"   最大 id={r[0]}  最新 created_at={r[1]}  总行数={r[2]}")

    print("\n=== 最近 8 条的 operation / decision_source / symbol ===")
    for row in db.execute(text("""
        SELECT id, created_at, symbol, operation, decision_source
        FROM ai_decision_logs ORDER BY id DESC LIMIT 8
    """)).fetchall():
        print("  ", tuple(row))
finally:
    db.close()
