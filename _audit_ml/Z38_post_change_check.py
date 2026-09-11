# -*- coding: utf-8 -*-
"""Z38：落码后核验——配置生效值 + 组合闸行为 + 当前敞口。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import settings  # noqa: E402
from backend.services.mlto.midlong_portfolio_risk import (  # noqa: E402
    check_portfolio_open_allowed,
)

print("=== 生效配置 ===")
for k in ("MIDLONG_PORTFOLIO_GATE_ENABLED", "MIDLONG_MAX_OPEN_POSITIONS",
          "MIDLONG_CORR_CLUSTER_MAX", "MIDLONG_CORR_CLUSTER_SYMBOLS",
          "MIDLONG_MAX_NET_EXPOSURE_PCT"):
    print(f"  {k} = {getattr(settings, k, '(未定义)')}")


def _pos(sym, tier="mid", nature="swing", side="long", size=1.0, px=100.0):
    return {"symbol": sym, "side": side, "size": size, "entry_price": px,
            "mark_price": px, "trade_nature": nature, "timeframe_tier": tier}


print("\n=== 组合闸行为核验（真实函数）===")
for n in (3, 4, 5):
    positions = [_pos(f"S{i}") for i in range(n)]
    ok, why = check_portfolio_open_allowed(
        symbol="NEW", action="buy",
        portfolio={"balance": {"total_equity": 4800.0}, "positions": positions},
        new_notional=900.0,
    )
    print(f"  已有 {n} 笔 mid/long → {'放行' if ok else '拦截'}  ({why[:60]})")

print("\n=== 当前账户敞口 ===")
from sqlalchemy import create_engine, text  # noqa: E402
eng = create_engine(os.getenv("DATABASE_URL",
                              "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena"))
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    for r in c.execute(text("""
        select id, symbol, timeframe_tier, trade_nature,
               round((coalesce(unrealized_pnl,0)+coalesce(partial_realized_pnl,0)
                      -coalesce(partial_fee_paid,0))::numeric,2) usd,
               round((original_size*entry_price)::numeric,0) ntl
        from paper_positions where status='open' order by opened_at
    """)).fetchall():
        print(f"  #{r[0]} {str(r[1]):<9}{str(r[2]):<5}{str(r[3] or ''):<13}"
              f"USD={r[4]:>8} 名义={r[5]}")
    for r in c.execute(text("""
        select round(total_equity::numeric,2), round(available_balance::numeric,2)
        from paper_balances where account_id=14
    """)).fetchall():
        print(f"  账户14 equity={r[0]} avail={r[1]}")
