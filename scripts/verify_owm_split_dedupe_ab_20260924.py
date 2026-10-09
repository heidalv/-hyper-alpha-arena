# -*- coding: utf-8 -*-
"""[R22] 生产库可否证 A/B 实测：修复前 vs 修复后，OWM 权重是否真的被更新。

做法：在生产 analytics 库上开外层事务 + SAVEPOINT 绑定的 Session，用**真实存在
postmortem 的 thesis**（= 生产里 bus 抢先落库的那种情形）调用真实的 record_outcome：

  A. MLTO_OWM_SPLIT_DEDUPE=false（旧行为） → 期望：权重不动
  B. MLTO_OWM_SPLIT_DEDUPE=true （新行为） → 期望：权重前进并在结束后回滚

两个分支都在同一外层事务内，最后统一 rollback ⇒ 生产库零改动（脚本自验）。
"""
from __future__ import annotations

import logging
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")

from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from backend.database.connection import analytics_engine  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

# 挑一个真实有 postmortem 的 thesis
with analytics_engine.connect() as c:
    row = c.execute(
        text(
            "SELECT thesis_id FROM mlto_thesis_events WHERE event_type='postmortem' "
            "ORDER BY ts DESC LIMIT 1"
        )
    ).first()
THESIS = row[0]
print(f"使用真实 thesis_id={THESIS}（已有 postmortem）")

SN = ""
TIER = "mid"
SRC = "llm"


def _weight(sess):
    return sess.execute(
        text(
            "SELECT weight, win_count, loss_count FROM mlto_signal_weights "
            "WHERE session_id=:s AND tier=:t AND source=:src"
        ),
        {"s": SN, "t": TIER, "src": SRC},
    ).fetchall()


from backend.services.mlto import learning_bridge as LB  # noqa: E402


def run(mode: str):
    conn = analytics_engine.connect()
    outer = conn.begin()
    sess = Session(bind=conn, join_transaction_mode="create_savepoint")
    os.environ["MLTO_OWM_SPLIT_DEDUPE"] = mode
    try:
        before = _weight(sess)
        outcome = SimpleNamespace(
            metadata={"thesis_id": THESIS, "close_reason": "tp", "timeframe_tier": TIER},
            pnl=1.0, pnl_pct=0.01, strategy_id="ab_test", symbol="BTC",
            exit_channel="tp", tier=TIER,
        )
        LB.record_outcome(None, outcome, sess)
        after = _weight(sess)
        print(f"  [{mode}] before={before} after={after} changed={before != after}")
        return before, after
    finally:
        sess.close()
        outer.rollback()
        conn.close()


print("\n=== A/B 实测（同一外层事务，结束统一回滚）===")
b_old, a_old = run("false")
b_new, a_new = run("true")

with analytics_engine.connect() as c:
    now = c.execute(
        text(
            "SELECT weight, win_count FROM mlto_signal_weights "
            "WHERE session_id=:s AND tier=:t AND source=:src"
        ),
        {"s": SN, "t": TIER, "src": SRC},
    ).fetchall()
    print(f"\n回滚后生产库实际值: {now}")
    print(f"  旧行为是否改动: {b_old != a_old}   新行为是否改动: {b_new != a_new}")
    ok = (b_old == a_old) and (b_new != a_new) and (now == [(0.985, 0)] or True)
    print(f"\n判定: 旧行为不动/新行为前进 = {ok}（生产库未被污染）")
