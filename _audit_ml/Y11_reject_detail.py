# -*- coding: utf-8 -*-
"""被拒减仓的持仓细节（Y11）：为什么 chunk 只有 $2.76 而仓位 ≥$10？"""
import os

from sqlalchemy import create_engine, text

eng = create_engine(os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena"))
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    for pid in (4306, 4089, 4068):
        p = c.execute(text("""
            select id, symbol, side, timeframe_tier, trade_nature, entry_price, close_price,
                   size, original_size, margin, original_margin, leverage, reduce_count,
                   partial_realized_pnl, partial_fee_paid, peak_pnl_pct, close_reason,
                   opened_at, closed_at, exit_state_json
            from paper_positions where id=:p
        """), {"p": pid}).fetchone()
        if not p:
            continue
        d = dict(p._mapping)
        entry = float(d["entry_price"] or 0)
        print(f"\n=== pid={pid} {d['symbol']} {d['side']} {d['timeframe_tier']}/{d['trade_nature']} ===")
        print(f"  entry={entry} close={d['close_price']} size={d['size']} original={d['original_size']}")
        print(f"  margin={d['margin']} orig_margin={d['original_margin']} lev={d['leverage']} "
              f"reduce_count={d['reduce_count']}")
        print(f"  partial_pnl={d['partial_realized_pnl']} partial_fee={d['partial_fee_paid']} "
              f"peak={float(d['peak_pnl_pct'] or 0)*100:.2f}%")
        print(f"  当前 size 名义 = ${float(d['size'] or 0)*entry:.2f} | "
              f"original 名义 = ${float(d['original_size'] or 0)*entry:.2f}")
        print(f"  close_reason={str(d['close_reason'])[:60]}")
        es = d["exit_state_json"]
        print(f"  exit_state_json={str(es)[:400]}")
        evs = c.execute(text("""
            select event_type, exit_channel, quantity, price, close_ratio, created_at
            from position_exit_events where position_id=:p
            order by created_at limit 8
        """), {"p": pid}).fetchall()
        print("  前 8 条事件:")
        for e in evs:
            print("   ", tuple(str(x)[:40] for x in e))
