# -*- coding: utf-8 -*-
"""[F246] 从头审计：账户/车道/预算现状快照（回答"现在套利具体是怎么回事"）。"""
import json
import sys

sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena")

from sqlalchemy import text  # noqa: E402
from backend.database.connection import SessionLocal  # noqa: E402

with SessionLocal() as db:
    print("== arbitrage_paper_accounts ==")
    for r in db.execute(text(
        "SELECT id, name, total_equity, available_balance, frozen_balance, realized_pnl,"
        " allocation_preset, status FROM arbitrage_paper_accounts ORDER BY id")).mappings().all():
        print("  ", dict(r))
    print("== arbitrage_paper_exchange_balances (101) ==")
    for r in db.execute(text(
        "SELECT account_id, exchange, allocated_usd, available_usd, frozen_usd"
        " FROM arbitrage_paper_exchange_balances WHERE account_id=101")).mappings().all():
        print("  ", dict(r))
    print("== 车道绑定 ==")
    for r in db.execute(text(
        "SELECT lane_id, meta_json->>'paper_account_id' AS acct,"
        " meta_json->>'venue' AS venue, meta_json->>'name' AS name"
        " FROM lane_registry ORDER BY lane_id")).mappings().all():
        print("  ", dict(r))

from backend.services import lane_registry as reg  # noqa: E402
lanes = reg.list_lanes()
for ln in lanes:
    m = ln.get("meta") or {}
    if m.get("paper_account_id") == 101:
        print("== mm_asterdex meta 关键键 ==")
        for k in ("paper_account_id", "venue", "symbols", "stats_since", "shadow_equity",
                  "strategy_type", "name"):
            print(f"   {k} = {m.get(k)}")
        break

import urllib.request  # noqa: E402
d = json.load(urllib.request.urlopen("http://127.0.0.1:8000/api/trading/portfolio/summary", timeout=30))
print("== /portfolio/summary ==")
for k in ("equity", "equity_source", "pnl_today_usd", "lane_budgets", "budget_used_usd",
          "budget_used_pct", "lanes_active", "lanes_total"):
    print(f"   {k} = {d.get(k)}")
