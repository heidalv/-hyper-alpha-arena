# -*- coding: utf-8 -*-
"""Z59: trend_e1 F4 守卫的真实生效性（三准则）+ MM check_lane_limits 死代码判定。"""
from __future__ import annotations
import os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sqlalchemy import create_engine, text
URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
eng = create_engine(URL)

import backend.services.trend_e1_engine as E
import backend.services.trend_e1_f4_gate as G

print("=== A. trend_e1 账户与车道开关 ===")
print("  e1_enabled      =", E.e1_enabled())
print("  dry_run         =", E.dry_run())
print("  long_lane_excl  =", E.long_lane_exclusive())
print("  long_lane_open_allowed('long','position') =", E.long_lane_open_allowed("long", "position", None))
print("  L2/paper        =", E.long_lane_open_allowed("mid", "swing", None))
print("  live_aster_req  =", G.live_aster_requested())
accts = E.e1_account_ids()
print("  e1_account_ids  =", accts)
f4 = G.evaluate_f4_gate(persist=False)
print("  f4.passed       =", f4.get("passed"), " live_allowed =", f4.get("live_allowed"))
for c in f4.get("checks") or []:
    print("     -", c.get("name"), "ok=", c.get("ok"), str(c.get("reason"))[:80])

print()
print("=== B. 真实数据：E1 车道在库内的交易与账户模式 ===")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    for a in accts:
        r = c.execute(text("select id, name, trading_mode, initial_balance from paper_accounts where id=:a"), {"a": a}).first()
        print("  acct", tuple(r) if r else None)
    try:
        rows = c.execute(text("""select trading_mode, count(*) from paper_accounts group by 1 order by 2 desc""")).fetchall()
        print("  全账户 trading_mode 分布:", [tuple(x) for x in rows])
    except Exception as e:
        print("  trading_mode 查询失败:", str(e)[:120])
        c.rollback()
    rows = c.execute(text("""select strategy_id, count(*), sum(realized_pnl)
                             from strategy_trades
                             where strategy_id like 'trend_e1%'
                             group by 1 order by 2 desc limit 10""")).fetchall()
    print("  trend_e1 历史成交:", [tuple(x) for x in rows])
    rows = c.execute(text("""select count(*) from paper_positions where timeframe_tier='long'""")).first()
    print("  tier=long 仓位总数:", rows[0])

