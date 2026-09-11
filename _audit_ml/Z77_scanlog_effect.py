# -*- coding: utf-8 -*-
"""Z77: S1-12「account_id=0 兜底落库」的真实效果核验（ai_decision_logs）。"""
from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

from sqlalchemy import create_engine, text  # noqa: E402

URL = os.getenv("ANALYTICS_DATABASE_URL") or os.getenv("DATABASE_URL", "")
print("analytics url:", (URL.split("@")[-1] if "@" in URL else URL))
eng = create_engine(URL)
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    tabs = [r[0] for r in c.execute(text(
        "select table_name from information_schema.tables where table_schema='public' "
        "and table_name like '%decision%' order by 1")).fetchall()]
    print("decision 相关表:", tabs)

    for t in ("ai_decision_logs",):
        if t not in tabs:
            continue
        cols = [r[0] for r in c.execute(text(
            "select column_name from information_schema.columns where table_name=:t"), {"t": t}).fetchall()]
        print(f"\n{t} 列:", [x for x in cols if x in (
            "id", "account_id", "operation", "symbol", "executed", "realized_pnl",
            "decision_time", "decision_source", "confidence", "mid_confidence")] or cols[:12])
        n = c.execute(text(f"select count(*) from {t}")).scalar()
        print(f"  总行数: {n}")
        print("  按 account_id:", [tuple(x) for x in c.execute(text(
            f"select account_id, count(*) from {t} group by 1 order by 2 desc limit 8")).fetchall()])
        print("  按 executed:", [tuple(x) for x in c.execute(text(
            f"select executed, count(*) from {t} group by 1 order by 2 desc limit 8")).fetchall()])
        print("  按 operation:", [tuple(x) for x in c.execute(text(
            f"select operation, count(*) from {t} group by 1 order by 2 desc limit 8")).fetchall()])
        try:
            print("  realized_pnl 非空:", c.execute(text(
                f"select count(*) from {t} where realized_pnl is not null")).scalar())
        except Exception as e:
            c.rollback()
            print("  realized_pnl 查询失败:", str(e)[:80])
        # 校准器样本口径（ai_decision_calibrator:282 的过滤条件）
        try:
            q = (f"select count(*) from {t} where operation in ('buy','sell') and executed='true' "
                 f"and realized_pnl is not null and realized_pnl <> 0")
            print("  校准器可用样本（executed=true 且已回填盈亏）:", c.execute(text(q)).scalar())
        except Exception as e:
            c.rollback()
            print("  样本查询失败:", str(e)[:100])
        print("  最近的 5 行 (time, acct, op, executed, pnl):")
        for r in c.execute(text(
                f"select decision_time, account_id, operation, executed, realized_pnl from {t} "
                f"order by decision_time desc limit 5")).fetchall():
            print("     ", [str(x) for x in r])
