# -*- coding: utf-8 -*-
"""仓位规模跳变核查（Y20）：8 月 vs 9 月的名义/保证金/风险占比。"""
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
        rows = [dict(r._mapping) for r in c.execute(text("""
            select id, account_id, symbol, timeframe_tier, entry_price, sl_price,
                   original_size, size, original_margin, margin, leverage,
                   peak_pnl_pct, unrealized_pnl, partial_realized_pnl, partial_fee_paid,
                   opened_at
            from paper_positions
            where timeframe_tier in ('mid','long') and status='closed'
              and closed_at >= now() - interval '35 days'
            order by opened_at
        """)).fetchall()]
        # 权益来源
        eq = {}
        try:
            for r in c.execute(text("""
                select account_id, total_equity from paper_balances
            """)).fetchall():
                eq[r[0]] = dict(r._mapping)
        except Exception as exc:
            print("权益表读取失败:", exc)
    print("权益表:", {k: {kk: round(float(vv or 0), 2) for kk, vv in v.items()} for k, v in eq.items()})

    buckets = defaultdict(list)
    for r in rows:
        entry = float(r["entry_price"] or 0)
        sz0 = float(r["original_size"] or r["size"] or 0)
        sl = float(r["sl_price"] or 0)
        mg0 = float(r["original_margin"] or r["margin"] or 0)
        if entry <= 0 or sz0 <= 0:
            continue
        ntl = entry * sz0
        risk = abs(entry - sl) * sz0 if sl > 0 else 0
        day = str(r["opened_at"])[:7]
        buckets[day].append({
            "ntl": ntl, "mg": mg0, "risk": risk,
            "risk_pct": risk / ntl * 100 if ntl else 0,
            "lev": float(r["leverage"] or 0),
            "usd": (float(r["unrealized_pnl"] or 0) + float(r["partial_realized_pnl"] or 0)
                    - float(r["partial_fee_paid"] or 0)),
            "peak": float(r["peak_pnl_pct"] or 0) * 100,
        })

    print(f"\n{'月份':<9}{'n':>4}{'名义中位':>10}{'保证金中位':>11}{'风险中位':>10}"
          f"{'SL%中位':>9}{'杠杆中位':>9}{'总USD':>10}")
    for day in sorted(buckets):
        v = buckets[day]
        print(f"{day:<9}{len(v):>4}{st.median([x['ntl'] for x in v]):>10.0f}"
              f"{st.median([x['mg'] for x in v]):>11.1f}"
              f"{st.median([x['risk'] for x in v]):>10.1f}"
              f"{st.median([x['risk_pct'] for x in v]):>9.2f}"
              f"{st.median([x['lev'] for x in v]):>9.1f}"
              f"{sum(x['usd'] for x in v):>+10.2f}")

    # 近 3 天逐笔
    print("\n=== 近 3 天逐笔 ===")
    recent = [r for r in rows if str(r["opened_at"]) >= "2026-09-07"]
    for r in recent:
        entry = float(r["entry_price"] or 0)
        sz0 = float(r["original_size"] or r["size"] or 0)
        ntl = entry * sz0
        mg = float(r["original_margin"] or r["margin"] or 0)
        usd = (float(r["unrealized_pnl"] or 0) + float(r["partial_realized_pnl"] or 0)
               - float(r["partial_fee_paid"] or 0))
        print(f"  {str(r['opened_at'])[5:16]} {r['symbol']:<8} {r['timeframe_tier']:<5} "
              f"名义=${ntl:>8.0f} 保证金=${mg:>7.1f} 杠杆={float(r['leverage'] or 0):>4.1f}x "
              f"峰值={float(r['peak_pnl_pct'] or 0)*100:>+6.2f}% USD={usd:>+8.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
