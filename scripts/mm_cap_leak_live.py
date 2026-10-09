# -*- coding: utf-8 -*-
"""[F230] 上限又漏了（XRP/SOL/ETH 各 -$461 = 5.3× 单币上限）——逐笔重建定位绕过路径。
"""
import sys
sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena")

from sqlalchemy import text  # noqa: E402
from backend.database.connection import SessionLocal  # noqa: E402

SINCE = "2026-09-15T23:08:00+08:00"

with SessionLocal() as db:
    rows = db.execute(text(
        "SELECT ts, symbol, notional, net_bp, meta_json"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND ts >= CAST(:s AS timestamptz)"
        " ORDER BY ts ASC"), {"s": SINCE}).mappings().all()
    pos = {}
    print(f"{'时间':<9}{'币':<5}{'侧':<5}{'名义$':>8}{'平仓?':<5}   净仓(名义$)")
    for r in rows:
        s = r["symbol"]
        m = r["meta_json"] or {}
        side = str(m.get("side") or "")[:4]
        fl = "★" if str(m.get("flatten") or "") in ("true", "True", "1") else ""
        signed = float(r["notional"] or 0) * (1 if side in ("buy",) else -1)
        pos[s] = pos.get(s, 0.0) + signed
        print(f"{r['ts'].strftime('%H:%M:%S'):<9}{s:<5}{side:<5}{float(r['notional'] or 0):>8.1f}"
              f"{fl:<5}   {s} {pos[s]:+.1f}")
