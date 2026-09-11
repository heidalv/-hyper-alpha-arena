# -*- coding: utf-8 -*-
"""数据管线体检：mid/long 决策所用的 1h/4h/1d K 线完整性、新鲜度、异常值。"""
from __future__ import annotations

import statistics as st
from collections import defaultdict

from sqlalchemy import create_engine, text

MARKET = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
ARENA = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")

# mid/long 车道在用的 symbol（来自日志 tick 固定池）
SYMS = ["XPL", "UNI", "ETH", "VIRTUAL", "SOL", "XRP", "BTC", "ASTER", "BNB", "TIA", "AR"]


def main():
    with MARKET.connect() as c:
        c.execute(text("set statement_timeout='600000'"))
        print("=== 各周期覆盖（mid/long 在用 symbol，asterdex）===")
        for tf in ("1h", "4h", "1d"):
            rows = c.execute(text("""
                select symbol, count(*) n, min(timestamp) mn, max(timestamp) mx
                from crypto_klines
                where period=:tf and exchange='asterdex' and symbol = any(:syms)
                group by 1 order by 1
            """), {"tf": tf, "syms": SYMS}).fetchall()
            print(f"-- {tf} --")
            for s, n, mn, mx in rows:
                import datetime as dt
                age_h = (dt.datetime.now().timestamp() - int(mx)) / 3600 if mx else None
                print(f"   {s:<8} n={n:<6} last={dt.datetime.fromtimestamp(int(mx)):%Y-%m-%d %H:%M} "
                      f"age={age_h:6.1f}h")

        print("\n=== 1h 缺口检查（最近 30 天，>2 个 bar 的间隔）===")
        for s in SYMS:
            ts = [r[0] for r in c.execute(text("""
                select timestamp from crypto_klines
                where period='1h' and exchange='asterdex' and symbol=:s
                  and timestamp > extract(epoch from now())::bigint - 30*86400
                order by timestamp
            """), {"s": s}).fetchall()]
            gaps = []
            for a, b in zip(ts, ts[1:]):
                d = (b - a) / 3600
                if d > 2:
                    gaps.append((int(a), int(d)))
            print(f"   {s:<8} bars={len(ts):<5} gaps={len(gaps)} "
                  f"{'max_gap=' + str(max([g[1] for g in gaps])) + 'h' if gaps else ''}")

        print("\n=== 异常值检查：|1h 收益| > 20% 或 high<low ===")
        for s in SYMS:
            bad = c.execute(text("""
                select count(*) from crypto_klines
                where period='1h' and exchange='asterdex' and symbol=:s
                  and timestamp > extract(epoch from now())::bigint - 30*86400
                  and (high_price < low_price or close_price <= 0 or open_price <= 0
                       or abs((close_price-open_price)/nullif(open_price,0)) > 0.20)
            """), {"s": s}).scalar()
            zero_vol = c.execute(text("""
                select count(*) from crypto_klines
                where period='1h' and exchange='asterdex' and symbol=:s
                  and timestamp > extract(epoch from now())::bigint - 30*86400
                  and coalesce(volume,0) = 0
            """), {"s": s}).scalar()
            print(f"   {s:<8} 异常={bad:<4} 零成交量={zero_vol}")

    print("\n=== 决策数据链路：paper_positions 里 mid/long 仓的关键字段缺失率 ===")
    with ARENA.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        row = c.execute(text("""
            select count(*) n,
                   count(*) filter (where sl_price is null or sl_price=0) no_sl,
                   count(*) filter (where tp_price is null or tp_price=0) no_tp,
                   count(*) filter (where exit_state_json is null) no_state,
                   count(*) filter (where expected_hold_hours is null) no_ehh,
                   count(*) filter (where peak_pnl_pct is null) no_peak
            from paper_positions where timeframe_tier in ('mid','long')
        """)).mappings().first()
        print(dict(row))


if __name__ == "__main__":
    main()
