# -*- coding: utf-8 -*-
"""[P0 大轮回 2026-09-27] 历史平仓的快照关联回填（宁缺勿错）。

背景：平仓时刻的 DecisionSnapshot 匹配发生在 TradeOutcome 构建之后，
导致历史学习行 `strategy_trades.decision_context.snapshot_id` 普遍为空
（代码根因已在 paper_trading_engine 修复：开仓写入决策身份 + 平仓回读 + 匹配后回填）。
本脚本只回填**历史**缺口，规则从严：
  1. 候选 = analytics.decision_snapshots：同 symbol + action∈{buy,sell} 与持仓方向一致
     + pnl IS NULL + timestamp ∈ [opened_at−2h, opened_at+2h]；
  2. 有 executed=True 的候选时只用 executed=True；
  3. 恰好唯一候选才回填（ambiguous 跳过并计数）；
  4. 只改 strategy_trades.decision_context.snapshot_id，不触碰决策与账务。

用法：
  .venv\\Scripts\\python.exe scripts/p0_backfill_snapshot_link.py --days 30            # 干跑
  .venv\\Scripts\\python.exe scripts/p0_backfill_snapshot_link.py --days 30 --apply     # 落库
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"), override=False)

from sqlalchemy import create_engine, text  # noqa: E402

URL = os.getenv("DATABASE_URL", "")
ARENA = create_engine(URL, future=True)
ANALYTICS = create_engine(URL.replace("/alpha_arena", "/alpha_analytics"), future=True)


def _patch_one(conn, sid: str, ppid: str) -> bool:
    conn.execute(text(
        "UPDATE strategy_trades SET decision_context = "
        "jsonb_set(COALESCE(decision_context::jsonb, '{}'::jsonb), "
        "'{snapshot_id}', to_jsonb(CAST(:sid AS text))) "
        "WHERE decision_context->>'paper_position_id' = :ppid"
    ), {"sid": sid, "ppid": ppid})
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", default="30")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    days = int(a.days)
    since = datetime.now() - timedelta(days=days)

    with ARENA.connect() as ac:
        ac.exec_driver_sql("SET LOCAL app.tenant_id = '326'")
        ac.exec_driver_sql("SET LOCAL app.is_admin = 'on'")
        rows = ac.execute(text("""
            SELECT st.id AS st_id, p.id AS ppos_id, p.symbol, p.side, p.opened_at,
                   st.decision_context->>'snapshot_id' AS snap
            FROM paper_positions p
            JOIN strategy_trades st
              ON st.decision_context->>'paper_position_id' = p.id::text
            WHERE p.status='closed' AND p.closed_at >= :since
              AND COALESCE(st.decision_context->>'snapshot_id','') = ''
        """), {"since": since}).fetchall()
        pending = [dict(r._mapping) for r in rows]

    patched, skipped_amb, skipped_none = 0, 0, 0
    details = []
    with ANALYTICS.connect() as c:
        for t in pending:
            want = "buy" if str(t["side"]).lower().startswith("l") else "sell"
            # ±6h：executed=True 快照稀缺（7 天 1 万条里仅 76 条），入场决策快照
            # 与 opened_at 的时差可达数小时（评估→下单链路的固有延迟）。
            lo = t["opened_at"] - timedelta(hours=6)
            hi = t["opened_at"] + timedelta(hours=6)
            cands = c.execute(text("""
                SELECT id, trace_id, proposal_id, executed, strategy_id, timestamp
                FROM decision_snapshots
                WHERE symbol = :sym AND action = :act AND pnl IS NULL
                  AND timestamp >= :lo AND timestamp <= :hi
                ORDER BY timestamp
            """), {"sym": t["symbol"], "act": want, "lo": lo, "hi": hi}).fetchall()
            pool = [dict(r._mapping) for r in cands]
            exec_pool = [x for x in pool if x["executed"]]
            use = exec_pool if exec_pool else []
            if not use:
                skipped_none += 1
                details.append({"ppos": t["ppos_id"], "sym": t["symbol"], "why": "no_executed_cand"})
                continue
            if len(use) != 1:
                skipped_amb += 1
                details.append({"ppos": t["ppos_id"], "sym": t["symbol"],
                                "why": f"ambiguous({len(use)})"})
                continue
            snap = use[0]
            sid = str(snap["trace_id"] or snap["proposal_id"] or snap["id"])[:80]
            if not a.apply:
                details.append({"ppos": t["ppos_id"], "sym": t["symbol"], "would_patch": sid})
            else:
                with ARENA.connect() as ac2:
                    ac2.exec_driver_sql("SET LOCAL app.tenant_id = '326'")
                    ac2.exec_driver_sql("SET LOCAL app.is_admin = 'on'")
                    _patch_one(ac2, sid, str(t["ppos_id"]))
                    ac2.commit()
                details.append({"ppos": t["ppos_id"], "sym": t["symbol"], "patched": sid})
            patched += 1

    print(json.dumps({
        "pending": len(pending), "patched": patched,
        "skipped_ambiguous": skipped_amb, "skipped_no_cand": skipped_none,
        "applied": bool(a.apply), "details": details[:40],
    }, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
