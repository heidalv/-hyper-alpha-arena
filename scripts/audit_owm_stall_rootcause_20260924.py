# -*- coding: utf-8 -*-
"""[R22] 判定 OWM 权重表为何自 09-18 12:40 起不动。

两条证据：
  A. postmortem 的两个写入方的时间分布（learning_bus vs learning_bridge.record_outcome）。
     若"bridge 侧 postmortem"在 09-18 12:40 前后绝迹，说明 record_outcome 被
     `_has_postmortem` 去重挡在 `_bump_owm` 之前（bus 的异步 postmortem 先落库）。
  B. 在生产 analytics 库上，用 SAVEPOINT 包裹真实 `_bump_owm` 调用并最终回滚，
     证明该代码路径本身通不通（零生产副作用）。
"""
from __future__ import annotations

import logging
import sys

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")

from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from backend.database.connection import analytics_engine  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

print("=== A. postmortem 写入方按日（bus=learning_bus, bridge=record_outcome）===")
SQL = """
SELECT date_trunc('day', ts)::date d,
       count(*) FILTER (WHERE payload_json LIKE '%"async": true%') AS bus,
       count(*) FILTER (WHERE payload_json NOT LIKE '%"async": true%') AS bridge
FROM mlto_thesis_events WHERE event_type='postmortem'
GROUP BY 1 ORDER BY 1 DESC LIMIT 14
"""
with analytics_engine.connect() as c:
    for r in c.execute(text(SQL)):
        print(f"  {r[0]}  bus={r[1]:<4} bridge={r[2]}")

print("\n--- bridge 侧 postmortem 最近 8 条 ---")
with analytics_engine.connect() as c:
    for r in c.execute(
        text(
            "SELECT ts, left(payload_json,120) FROM mlto_thesis_events "
            "WHERE event_type='postmortem' AND payload_json NOT LIKE '%\"async\": true%' "
            "ORDER BY ts DESC LIMIT 8"
        )
    ):
        print(f"  {r[0]}  {r[1]}")

print("\n=== B. 真实 _bump_owm 的 SAVEPOINT 实测（结束回滚，零生产副作用）===")
from backend.services.mlto import learning_bridge as LB  # noqa: E402

conn = analytics_engine.connect()
outer = conn.begin()
sess = Session(bind=conn, join_transaction_mode="create_savepoint")
try:
    before = sess.execute(
        text(
            "SELECT weight, win_count, loss_count, updated_at FROM mlto_signal_weights "
            "WHERE session_id='' AND tier='mid' AND source='llm'"
        )
    ).fetchall()
    print(f"  before: {before}")
    res = LB._bump_owm(
        None, "", "mid", [], 1.0, {"close_reason": "tp"}, sess
    )
    print(f"  _bump_owm 返回: {res}")
    after = sess.execute(
        text(
            "SELECT weight, win_count, loss_count, updated_at FROM mlto_signal_weights "
            "WHERE session_id='' AND tier='mid' AND source='llm'"
        )
    ).fetchall()
    print(f"  after : {after}")
finally:
    sess.close()
    outer.rollback()
    conn.close()

with analytics_engine.connect() as c:
    n = c.execute(
        text(
            "SELECT weight, win_count FROM mlto_signal_weights "
            "WHERE session_id='' AND tier='mid' AND source='llm'"
        )
    ).fetchall()
    print(f"  回滚后（应与 before 一致）: {n}")
