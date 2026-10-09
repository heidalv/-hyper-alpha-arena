# -*- coding: utf-8 -*-
"""读系统自己的否决台账 mlto_thesis_events（Analytics 库）。只读 SELECT。

这是比"我从日志凑的计数"更权威的一手证据：`_emit_open_blocked` /
`_emit_open_execute_false` 写的就是 maybe_open 的逐次否决原因（30 分钟节流）。
"""
from __future__ import annotations

import io
import sys
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

    print("=" * 96)
    print("mlto_thesis_events —— 中线开仓否决台账（近 48h）")
    print("=" * 96)

    print("\n[1] 事件类型分布（近 48h）")
    for r in db.execute(text("""
        SELECT event_type, count(*) FROM mlto_thesis_events
        WHERE ts >= now() - interval '48 hours'
        GROUP BY 1 ORDER BY 2 DESC
    """)).fetchall():
        print(f"    {str(r[0]):24s} {r[1]}")

    print("\n[2] open_blocked / open_execute_false 的原因分布（近 48h）")
    rows = db.execute(text("""
        SELECT event_type,
               coalesce(
                   nullif(payload_json, '')::jsonb ->> 'reason',
                   nullif(payload_json, '')::jsonb ->> 'why',
                   '(payload 无 reason)'
               ) AS reason,
               count(*) AS n
        FROM mlto_thesis_events
        WHERE ts >= now() - interval '48 hours'
          AND event_type IN ('open_blocked', 'open_execute_false')
          AND payload_json ~ '^\\s*[{\\[]'
        GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 40
    """)).fetchall()
    for et, reason, n in rows:
        print(f"    {n:5d}  {et:20s} {str(reason)[:110]}")

    print("\n[3] 按 tier × 事件类型（近 48h）")
    for r in db.execute(text("""
        SELECT t.tier, te.event_type, count(*)
        FROM mlto_thesis_events te JOIN mlto_thesis t ON t.thesis_id = te.thesis_id
        WHERE te.ts >= now() - interval '48 hours'
        GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20
    """)).fetchall():
        print(f"    {str(r[0]):8s} {str(r[1]):22s} {r[2]}")

    print("\n[4] 近 48h 论题的可开仓意向")
    for r in db.execute(text("""
        SELECT tier, direction, accepted, recommend_open, count(*)
        FROM mlto_thesis
        WHERE created_at >= now() - interval '48 hours'
        GROUP BY 1,2,3,4 ORDER BY 5 DESC LIMIT 20
    """)).fetchall():
        print(f"    tier={str(r[0]):6s} dir={str(r[1]):6s} accepted={str(r[2]):5s} "
              f"rec_open={str(r[3]):5s}  n={r[4]}")

    print("\n[5] 最近 15 条否决事件（原始 payload）")
    for r in db.execute(text("""
        SELECT te.ts, t.symbol, t.tier, t.direction, te.event_type,
               left(coalesce(te.payload_json::text, ''), 200)
        FROM mlto_thesis_events te JOIN mlto_thesis t ON t.thesis_id = te.thesis_id
        WHERE te.event_type IN ('open_blocked', 'open_execute_false')
        ORDER BY te.ts DESC LIMIT 15
    """)).fetchall():
        print(f"    {r[0]} {str(r[1]):10s} {str(r[2]):5s} dir={str(r[3]):6s} {r[4]}")
        print(f"        {r[5]}")
finally:
    db.close()
