# -*- coding: utf-8 -*-
"""按 nature 输出 close_reason 分布 + pnl 合计（原始字符串分组）。"""
import sys
from collections import defaultdict
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from backend.core.tenant import set_system_identity
set_system_identity()

from backend.database.connection import SessionLocal
from sqlalchemy import text

with SessionLocal() as db:
    rows = db.execute(text("""
        SELECT trade_nature, close_reason,
               EXTRACT(EPOCH FROM (closed_at - opened_at)) as hold_sec,
               partial_realized_pnl
        FROM paper_positions
        WHERE status = 'closed' AND close_price IS NOT NULL
        ORDER BY closed_at DESC
    """)).fetchall()

by = defaultdict(lambda: dict(n=0, pnl=0.0, hold=0.0))
for r in rows:
    nature = r[0] or "?"
    reason = (r[1] or "??")
    # 取原因主干（截断到第一个冒号）
    stem = reason.split(":")[0][:45]
    b = by[(nature, stem)]
    b["n"] += 1
    b["pnl"] += float(r[3] or 0)
    b["hold"] += float(r[2] or 0) / 3600

for (nature, stem), b in sorted(by.items(), key=lambda kv: (kv[0][0], -kv[1]["n"])):
    print(f"{nature:<14} {stem:<45} n={b['n']:>5} pnl合计={b['pnl']:+9.1f} 均持仓={b['hold']/max(b['n'],1):.1f}h")
