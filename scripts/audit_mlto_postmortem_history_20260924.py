# -*- coding: utf-8 -*-
"""[R22] 判定 record_outcome（MLTO 学习桥）在生产是否真的执行过。

record_outcome 成功执行的唯一持久化痕迹 = mlto_thesis_events 里的 'postmortem' 事件
（learning_bridge.py L152-163）。因此：postmortem 的时间分布 = 学习桥的存活史。
"""
from __future__ import annotations

import sys

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")

from sqlalchemy import text  # noqa: E402

from backend.database.connection import analytics_engine  # noqa: E402


def q(sql, **kw):
    with analytics_engine.connect() as c:
        return c.execute(text(sql), kw).fetchall()


print("=== mlto_thesis_events 按 event_type ===")
for r in q(
    "SELECT event_type, count(*) n, min(ts) first_ts, max(ts) last_ts "
    "FROM mlto_thesis_events GROUP BY event_type ORDER BY n DESC LIMIT 15"
):
    print(f"  {str(r[0]):22s} n={r[1]:<7} {r[2]} -> {r[3]}")

print("\n=== postmortem 按日 ===")
rows = q(
    "SELECT date_trunc('day', ts)::date d, count(*) n FROM mlto_thesis_events "
    "WHERE event_type='postmortem' GROUP BY 1 ORDER BY 1 DESC LIMIT 12"
)
if not rows:
    print("  (无 postmortem 事件)")
for r in rows:
    print(f"  {r[0]}  n={r[1]}")

print("\n=== mlto_signal_weights 全部行 ===")
for r in q(
    "SELECT session_id, tier, source, weight, updated_at FROM mlto_signal_weights "
    "ORDER BY updated_at DESC LIMIT 15"
):
    print(f"  {str(r[0])[:24]:24s} {r[1]:5s} {str(r[2]):10s} w={r[3]} {r[4]}")

print("\n=== mlto_thesis_events 近 7 天按日 ===")
for r in q(
    "SELECT date_trunc('day', ts)::date d, count(*) n FROM mlto_thesis_events "
    "WHERE ts > now() - interval '7 days' GROUP BY 1 ORDER BY 1 DESC"
):
    print(f"  {r[0]}  n={r[1]}")
