# -*- coding: utf-8 -*-
"""Audit: recent 24h mm_asterdex lane — maker vs taker share (main DB)."""
import sys
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")

from sqlalchemy import text
from backend.core.tenant import system_identity
from backend.database.connection import SessionLocal

LANE = "mm_asterdex"

with system_identity():
    with SessionLocal() as db:
        rows = db.execute(text(
            "SELECT meta_json->>'exit_path' AS path, COUNT(*), "
            " SUM(notional), SUM(fee_bp*notional/1e4), "
            " SUM(net_bp*notional)/NULLIF(SUM(notional),0) "
            "FROM lane_ledger WHERE lane_id=:l AND event='fill' "
            "AND ts > NOW() - INTERVAL '24 hours' "
            "AND meta_json->>'exit_path' IS NOT NULL "
            "GROUP BY 1 ORDER BY 3 DESC NULLS LAST"), {"l": LANE}).fetchall()
        print("== 近24h 出场路径 ==")
        tot_n = tot_taker = 0
        for path, n, notional, fee_usd, net_bp in rows:
            tk = "taker" in (path or "")
            tot_n += n
            tot_taker += n if tk else 0
            print(f"  {path:24} n={n:5}  名义=${float(notional or 0):12,.0f}  "
                  f"费=${float(fee_usd or 0):8.4f}  均净={float(net_bp or 0):+8.2f}bp")
        print(f"  合计 n={tot_n}  taker腿数占比={tot_taker/max(1,tot_n):.1%}")

        rows2 = db.execute(text(
            "SELECT COUNT(*), SUM(fee_bp*notional/1e4), "
            " SUM(net_bp*notional)/NULLIF(SUM(notional),0) "
            "FROM lane_ledger WHERE lane_id=:l AND event='fill' "
            "AND ts > NOW() - INTERVAL '24 hours' "
            "AND meta_json->>'exit_path' IS NULL"), {"l": LANE}).fetchone()
        print("== 近24h 进场腿(无 exit_path) ==")
        print(f"  n={rows2[0]}  费=${float(rows2[1] or 0):.4f}  "
              f"均净={float(rows2[2] or 0):+.2f}bp")

        # 全历史对照
        rows3 = db.execute(text(
            "SELECT (meta_json->>'exit_path' LIKE '%taker%') AS is_taker, "
            "COUNT(*), SUM(fee_bp*notional/1e4) "
            "FROM lane_ledger WHERE lane_id=:l AND event='fill' "
            "GROUP BY 1"), {"l": LANE}).fetchall()
        print("== 全历史(含出场/进场) ==")
        for is_tk, n, fee in rows3:
            print(f"  taker={is_tk}  n={n}  费=${float(fee or 0):.4f}")
