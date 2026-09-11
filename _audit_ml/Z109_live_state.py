# -*- coding: utf-8 -*-
"""Z109: 真实数据 —— 当前是否存在 live 账户/会话（决定 paper/live 分叉的严重度）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

import os  # noqa: E402
from sqlalchemy import text  # noqa: E402
from backend.database.connection import SessionLocal  # noqa: E402

print("=== 环境层 ===")
for k in ("TRADING_MODE", "LIVE_TRADING_ENABLED", "LIVE_KILL_SWITCH", "DRY_RUN"):
    print(f"  {k} = {os.getenv(k)}")

db = SessionLocal()
try:
    print("\n=== accounts（前 10）===")
    try:
        rows = db.execute(text(
            "select id, account_type, is_active, auto_trading_enabled from accounts order by id limit 10"
        )).fetchall()
        for r in rows:
            print("  ", [str(x) for x in r])
    except Exception as e:
        db.rollback()
        print("  查询失败:", str(e)[:100])
    print("\n=== paper_balances 账户（账户 14 是否唯一活跃）===")
    try:
        rows = db.execute(text(
            "select account_id, round(total_equity::numeric,2) from paper_balances order by account_id limit 12"
        )).fetchall()
        for r in rows:
            print("  ", [str(x) for x in r])
    except Exception as e:
        db.rollback()
        print("  查询失败:", str(e)[:100])
    print("\n=== 会话表（trading_mode 分布）===")
    for tbl in ("full_auto_sessions", "trading_sessions"):
        try:
            rows = db.execute(text(
                f"select trading_mode, status, count(*) from {tbl} group by 1,2 order by 3 desc limit 8"
            )).fetchall()
            print(f"  {tbl}:")
            for r in rows:
                print("     ", [str(x) for x in r])
        except Exception as e:
            db.rollback()
            print(f"  {tbl}: 无表或查询失败 ({str(e)[:60]})")
finally:
    db.close()
