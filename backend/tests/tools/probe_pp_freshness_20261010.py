# -*- coding: utf-8 -*-
"""[2026-10-10] 只读：PP 往返账与账本新鲜度（判断入口是否会真的动）。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from backend.core.tenant import system_identity  # noqa: E402
from backend.database.connection import SessionLocal  # noqa: E402

ERA = 1791567757  # data/pp_era_start_ts.txt

with system_identity():
    with SessionLocal() as db:
        r = db.execute(text(
            "SELECT count(*), max(ts) FROM lane_ledger"
            " WHERE lane_id='mm_asterdex' AND meta_json->>'rt_bp' IS NOT NULL"
            " AND ts >= to_timestamp(:era)"), {"era": ERA}).fetchone()
        print("rt_bp 平仓腿（era 起）:", r[0], "| 最新:", r[1])
        r2 = db.execute(text(
            "SELECT count(*) FROM lane_ledger WHERE lane_id='mm_asterdex'"
            " AND ts > now() - make_interval(mins => 10)")).scalar()
        print("最近 10 分钟腿数:", r2)
        r3 = db.execute(text(
            "SELECT count(*) FROM strategy_trades"
            " WHERE strategy_id='mm_asterdex_pp'")).scalar()
        print("strategy_trades(mm_asterdex_pp，应恒 0):", r3)
