# -*- coding: utf-8 -*-
"""[R23] 清理探针污染 + 核对真实写入方。

背景：`thesis_store.append_event`（thesis_store.py L233-247）**显式忽略调用方传入的 db**，
用独立 `AnalyticsSessionLocal` 短连接自行 commit。因此任何"用 SAVEPOINT 包住 + 回滚"
的验证方法**对事件表无效** —— R22 的 A/B 脚本泄漏了 2 条 postmortem + 1 条 owm_bump。
本脚本：1) 列出将被删的行；2) 删除"可证明为合成探针"的行；3) 复核。
"""
from __future__ import annotations

import sys

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")

from sqlalchemy import text  # noqa: E402

from backend.database.connection import analytics_engine  # noqa: E402

PROBE_IDS = ("th-1", "t-probe-2")

with analytics_engine.connect() as c:
    print("=== 待删（探针合成行）===")
    rows = c.execute(
        text(
            "SELECT id, thesis_id, event_type, ts, left(payload_json, 70) "
            "FROM mlto_thesis_events WHERE thesis_id = ANY(:ids) ORDER BY ts"
        ),
        {"ids": list(PROBE_IDS)},
    ).fetchall()
    for r in rows:
        print(f"  id={r[0]} {r[1]:10s} {r[2]:12s} {r[3]} {r[4]}")

if not rows:
    print("  (无待删行)")
else:
    with analytics_engine.begin() as c:
        n = c.execute(
            text("DELETE FROM mlto_thesis_events WHERE thesis_id = ANY(:ids)"),
            {"ids": list(PROBE_IDS)},
        ).rowcount
    print(f"\n已删除 {n} 行")

with analytics_engine.connect() as c:
    print("\n=== 复核 ===")
    for r in c.execute(
        text(
            "SELECT count(*) FROM mlto_thesis_events WHERE thesis_id = ANY(:ids)"
        ),
        {"ids": list(PROBE_IDS)},
    ):
        print(f"  残留探针行 = {r[0]}")
    print("  --- postmortem 按写入方（近 14 天）---")
    for r in c.execute(
        text(
            "SELECT date_trunc('day', ts)::date d, "
            "count(*) FILTER (WHERE payload_json LIKE '%\"async\": true%') bus, "
            "count(*) FILTER (WHERE payload_json NOT LIKE '%\"async\": true%') bridge "
            "FROM mlto_thesis_events WHERE event_type='postmortem' "
            "AND ts > now() - interval '14 days' GROUP BY 1 ORDER BY 1 DESC"
        )
    ):
        print(f"  {r[0]}  bus={r[1]:<4} bridge={r[2]}")
