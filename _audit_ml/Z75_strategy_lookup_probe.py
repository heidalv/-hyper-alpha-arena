# -*- coding: utf-8 -*-
"""Z75: 「无 active 策略」是否真的无策略？（UNI/long 505 条为主）DB 交叉核验。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

from sqlalchemy import text  # noqa: E402
from backend.database.connection import SessionLocal  # noqa: E402

db = SessionLocal()
try:
    print("=== strategies 表结构（关键列）===")
    cols = db.execute(text(
        "select column_name from information_schema.columns where table_name='ai_strategies' "
        "order by ordinal_position"
    )).fetchall()
    names = [c[0] for c in cols]
    print("  ", [n for n in names if n in (
        "id", "strategy_id", "primary_symbol", "timeframe_tier", "status", "account_id",
        "updated_at", "created_at", "name")] or names[:15])

    print("\n=== 被点名标的的策略行（UNI/ONDO/BNB/LTC/DOT）===")
    rows = db.execute(text(
        "select primary_symbol, timeframe_tier, status, account_id, count(*), max(updated_at) "
        "from ai_strategies where primary_symbol in ('UNI','ONDO','BNB','LTC','DOT') "
        "group by 1,2,3,4 order by 1,2,3,4"
    )).fetchall()
    for r in rows:
        print("  ", [str(x) for x in r])

    print("\n=== 全表 status 分布 ===")
    for r in db.execute(text("select status, count(*) from ai_strategies group by 1 order by 2 desc")).fetchall():
        print("  ", [str(x) for x in r])
    print("\n=== 全表 tier 分布（active）===")
    for r in db.execute(text(
        "select timeframe_tier, count(*) from ai_strategies where status='active' group by 1 order by 2 desc"
    )).fetchall():
        print("  ", [str(x) for x in r])
    print("\n=== UNI 策略明细 ===")
    for r in db.execute(text(
        "select id, strategy_id, timeframe_tier, status, account_id, updated_at from ai_strategies "
        "where primary_symbol='UNI' order by updated_at desc limit 12"
    )).fetchall():
        print("  ", [str(x) for x in r])
finally:
    db.close()
