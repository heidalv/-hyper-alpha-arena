# -*- coding: utf-8 -*-
"""Z81: EV 闸翻转对 mid 开仓的真实影响（近 7 天按 tier×小时的开仓数）+ 校准样本时间线。"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

from sqlalchemy import text  # noqa: E402
from backend.database.connection import SessionLocal  # noqa: E402

db = SessionLocal()
try:
    print("=== A. 近期开仓：按 tier × 天 ===")
    rows = db.execute(text(
        "select date_trunc('day', opened_at)::date as d, timeframe_tier, count(*) "
        "from paper_positions where opened_at > now() - interval '7 days' "
        "group by 1,2 order by 1 desc, 2"
    )).fetchall()
    for r in rows:
        print("  ", [str(x) for x in r])

    print("\n=== B. mid 层最近 12 笔开仓（含 nature/来源）===")
    rows = db.execute(text(
        "select id, symbol, opened_at, trade_nature, status, strategy_id, leverage "
        "from paper_positions where timeframe_tier='mid' order by opened_at desc limit 12"
    )).fetchall()
    for r in rows:
        print("  ", [str(x) for x in r])

    print("\n=== C. 校准样本时间线：swing_agent_score 有 pnl 的样本 ===")
    rows = db.execute(text(
        "select date_trunc('day', created_at)::date d, count(*), count(trade_pnl) "
        "from signal_trade_feedback where signal_type='swing_agent_score' "
        "group by 1 order by 1"
    )).fetchall()
    for r in rows:
        print("  ", [str(x) for x in r])
    rows = db.execute(text(
        "select created_at, symbol, signal_value, trade_pnl from signal_trade_feedback "
        "where signal_type='swing_agent_score' and trade_pnl is not null "
        "order by created_at limit 5"
    )).fetchall()
    print("  最早 5 条有 pnl 样本:", [[str(x) for x in r] for r in rows])
    rows = db.execute(text(
        "select created_at, symbol, signal_value, trade_pnl from signal_trade_feedback "
        "where signal_type='swing_agent_score' and trade_pnl is not null "
        "order by created_at desc limit 5"
    )).fetchall()
    print("  最晚 5 条:", [[str(x) for x in r] for r in rows])

    print("\n=== D. long 侧样本（trend_agent_score）===")
    rows = db.execute(text(
        "select signal_type, count(*), count(trade_pnl) from signal_trade_feedback "
        "group by 1 order by 2 desc"
    )).fetchall()
    for r in rows:
        print("  ", [str(x) for x in r])
finally:
    db.close()
