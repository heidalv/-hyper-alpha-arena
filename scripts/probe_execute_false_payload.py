# -*- coding: utf-8 -*-
"""open_execute_false 的 payload 结构与样例（判断"无 reason"是真空还是字段名不同）。只读。"""
from __future__ import annotations

import io
import json
import sys
from collections import Counter
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402
from backend.database.connection import AnalyticsSessionLocal  # noqa: E402

db = AnalyticsSessionLocal()
try:
    db.execute(text("SET app.is_admin='on'"))

    print("[1] open_execute_false 的 payload 顶层键分布（近 48h）")
    rows = db.execute(text("""
        SELECT payload_json FROM mlto_thesis_events
        WHERE event_type = 'open_execute_false' AND ts >= now() - interval '48 hours'
          AND payload_json ~ '^\\s*[{\\[]'
        LIMIT 500
    """)).fetchall()
    keys = Counter()
    for (raw,) in rows:
        try:
            d = json.loads(raw)
            if isinstance(d, dict):
                for k in d:
                    keys[k] += 1
        except Exception:  # noqa: BLE001
            keys["<解析失败>"] += 1
    print(f"    样本 {len(rows)} 条；键分布：")
    for k, v in keys.most_common(20):
        print(f"      {k:28s} {v}")

    print("\n[2] 原始样例（末 6 条）")
    for r in db.execute(text("""
        SELECT te.ts, t.symbol, t.tier, te.payload_json
        FROM mlto_thesis_events te JOIN mlto_thesis t ON t.thesis_id = te.thesis_id
        WHERE te.event_type = 'open_execute_false'
        ORDER BY te.ts DESC LIMIT 6
    """)).fetchall():
        print(f"    {r[0]} {str(r[1]):10s} {str(r[2]):5s}  {str(r[3])[:260]}")

    print("\n[3] open_blocked 的 payload 键分布（对照）")
    rows2 = db.execute(text("""
        SELECT payload_json FROM mlto_thesis_events
        WHERE event_type = 'open_blocked' AND ts >= now() - interval '48 hours'
          AND payload_json ~ '^\\s*[{\\[]'
        LIMIT 500
    """)).fetchall()
    keys2 = Counter()
    for (raw,) in rows2:
        try:
            d = json.loads(raw)
            if isinstance(d, dict):
                for k in d:
                    keys2[k] += 1
        except Exception:  # noqa: BLE001
            keys2["<解析失败>"] += 1
    print(f"    样本 {len(rows2)} 条；键分布：")
    for k, v in keys2.most_common(20):
        print(f"      {k:28s} {v}")
finally:
    db.close()
