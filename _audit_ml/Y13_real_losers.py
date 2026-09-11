# -*- coding: utf-8 -*-
"""真实亏损笔的形态（Y13）：总 USD 口径下「浮盈→亏损」到底是谁。"""
import os
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text

ARENA_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")


def main() -> int:
    eng = create_engine(ARENA_URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        poss = [dict(r._mapping) for r in c.execute(text("""
            select id, symbol, side, timeframe_tier, trade_nature, entry_price, close_price,
                   sl_price, size, original_size, peak_pnl_pct, unrealized_pnl,
                   partial_realized_pnl, partial_fee_paid, close_reason, opened_at, closed_at
            from paper_positions
            where timeframe_tier in ('mid','long') and status='closed'
              and closed_at >= now() - interval '30 days'
            order by opened_at
        """)).fetchall()]
        evs = [dict(r._mapping) for r in c.execute(text("""
            select position_id, event_type, exit_channel, quantity, price, created_at
            from position_exit_events
            where created_at >= now() - interval '40 days'
            order by position_id, created_at
        """)).fetchall()]
    by_pos = defaultdict(list)
    for e in evs:
        by_pos[e["position_id"]].append(e)

    rows = []
    for p in poss:
        entry = float(p["entry_price"] or 0)
        close = float(p["close_price"] or 0)
        sz0 = float(p["original_size"] or p["size"] or 0)
        if entry <= 0 or sz0 <= 0:
            continue
        usd = (float(p["unrealized_pnl"] or 0) + float(p["partial_realized_pnl"] or 0)
               - float(p["partial_fee_paid"] or 0))
        notional0 = sz0 * entry
        sign = 1.0 if str(p["side"]) == "long" else -1.0
        parts = [e for e in by_pos.get(p["id"], []) if e["event_type"] == "partial_exit_event"]
        n_rej = sum(1 for e in by_pos.get(p["id"], []) if e["event_type"] == "partial_exit_rejected")
        rows.append({
            "p": p, "usd": usd, "notional0": notional0,
            "pct": usd / notional0 * 100, "peak": float(p["peak_pnl_pct"] or 0) * 100,
            "price_final": sign * (close - entry) / entry * 100,
            "n_part": len(parts), "n_rej": n_rej,
            "part_usd": float(p["partial_realized_pnl"] or 0),
            "remain_notional": float(p["size"] or 0) * entry,
        })

    pat = [r for r in rows if r["peak"] >= 0.5 and r["usd"] < 0]
    print(f"总 USD 口径「浮盈→亏损」: n={len(pat)} 合计 USD={sum(r['usd'] for r in pat):+.2f}")
    print(f"\n{'时间':<12}{'币':<8}{'tier':<5}{'峰值':>7}{'总USD':>9}{'总%':>7}"
          f"{'剩余名义':>9}{'部分笔数':>9}{'部分USD':>9}{'被拒':>6}  {'原因':<26}")
    for r in sorted(pat, key=lambda x: x["usd"]):
        p = r["p"]
        print(f"{str(p['opened_at'])[5:16]:<12}{p['symbol']:<8}{str(p['timeframe_tier']):<5}"
              f"{r['peak']:>+7.2f}{r['usd']:>+9.2f}{r['pct']:>+7.2f}"
              f"{r['remain_notional']:>9.2f}{r['n_part']:>9}{r['part_usd']:>+9.2f}{r['n_rej']:>6}  "
              f"{str(p['close_reason'])[:26]}")

    print("\n=== 汇总 ===")
    no_part = [r for r in pat if r["n_part"] == 0]
    with_part = [r for r in pat if r["n_part"] > 0]
    print(f"  无部分平仓: n={len(no_part)} USD={sum(r['usd'] for r in no_part):+.2f} "
          f"均值={sum(r['usd'] for r in no_part)/max(1,len(no_part)):+.2f}")
    print(f"  有部分平仓: n={len(with_part)} USD={sum(r['usd'] for r in with_part):+.2f} "
          f"均值={sum(r['usd'] for r in with_part)/max(1,len(with_part)):+.2f}")

    print("\n=== 按 tier ===")
    for tier in ("mid", "long"):
        v = [r for r in pat if str(r["p"]["timeframe_tier"]) == tier]
        if v:
            print(f"  {tier:<5} n={len(v):>3} USD={sum(r['usd'] for r in v):>+8.2f} "
                  f"均值={sum(r['usd'] for r in v)/len(v):>+7.2f} "
                  f"峰值均值={sum(r['peak'] for r in v)/len(v):>+6.2f}%")
    print("\n=== 全体（含盈利）===")
    print(f"  总 USD={sum(r['usd'] for r in rows):+.2f} n={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
