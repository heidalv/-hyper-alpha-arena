#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""补丁2: journal 读取 trade_facts 新增的 strategy_id 列(2026-08-25)。"""
import shutil
PATH = "backend/services/ai_trade_journal_service.py"
BAK = PATH + ".bak_20260825b"
shutil.copy(PATH, BAK)
print("backup ->", BAK)
src = open(PATH, encoding="utf-8").read()

old1 = '''            rows = db.execute(_sa_text(
                "SELECT ts, symbol, tier, side, entry_price, exit_price, fees, pnl, "
                "outcome, close_reason, position_id "
                "FROM trade_facts WHERE ts::date = :d ORDER BY ts"
            ), {"d": date_str}).fetchall()'''
new1 = '''            rows = db.execute(_sa_text(
                "SELECT ts, symbol, tier, side, entry_price, exit_price, fees, pnl, "
                "outcome, close_reason, position_id, strategy_id "
                "FROM trade_facts WHERE ts::date = :d ORDER BY ts"
            ), {"d": date_str}).fetchall()'''
assert src.count(old1) == 1, f"e1={src.count(old1)}"
src = src.replace(old1, new1)

old1b = '''                    "strategy_id": "",
                    "close_reason": r.close_reason or "",
                    "created_at": str(r.ts),
                })'''
new1b = '''                    "strategy_id": getattr(r, "strategy_id", "") or "",
                    "close_reason": r.close_reason or "",
                    "created_at": str(r.ts),
                })'''
assert src.count(old1b) == 1, f"e1b={src.count(old1b)}"
src = src.replace(old1b, new1b)

old2 = '''            rows = db.execute(_sa_text(
                "SELECT ts, symbol, tier, side, entry_price, exit_price, fees, pnl, "
                "outcome, close_reason, position_id "
                "FROM trade_facts WHERE ts >= :s AND ts < :e ORDER BY ts"
            ), {"s": start, "e": end}).fetchall()'''
new2 = '''            rows = db.execute(_sa_text(
                "SELECT ts, symbol, tier, side, entry_price, exit_price, fees, pnl, "
                "outcome, close_reason, position_id, strategy_id "
                "FROM trade_facts WHERE ts >= :s AND ts < :e ORDER BY ts"
            ), {"s": start, "e": end}).fetchall()'''
assert src.count(old2) == 1, f"e2={src.count(old2)}"
src = src.replace(old2, new2)

old2b = '''                    "pnl": float(r.pnl or 0), "strategy_id": "",
                    "close_reason": r.close_reason or "", "created_at": str(r.ts),'''
new2b = '''                    "pnl": float(r.pnl or 0),
                    "strategy_id": getattr(r, "strategy_id", "") or "",
                    "close_reason": r.close_reason or "", "created_at": str(r.ts),'''
assert src.count(old2b) == 1, f"e2b={src.count(old2b)}"
src = src.replace(old2b, new2b)

open(PATH, "w", encoding="utf-8").write(src)
print("patched OK")
