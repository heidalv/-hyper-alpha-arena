# -*- coding: utf-8 -*-
"""仓位与杠杆口径审计（Y2）：杠杆是"放大显示"还是"放大真实盈亏"？

关键问题：mid/long 名义 10x 杠杆下，
  1. 单笔名义/保证金/风险敞口分布；
  2. 若按保证金固定比例下单 → 杠杆直接放大 USD 盈亏（用户看到的"大亏"）；
     若按 SL 风险固定比例下单 → 杠杆只影响保证金占用，USD 风险不变。
  3. 「浮盈→亏损」模式交易的 USD 亏损分布 vs 权益。
"""
from __future__ import annotations

import os
import statistics as st
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text  # noqa: E402

ARENA_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")


def main() -> int:
    eng = create_engine(ARENA_URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        rows = [dict(r._mapping) for r in c.execute(text("""
            select id, account_id, symbol, side, timeframe_tier, entry_price, close_price, sl_price,
                   leverage, size, original_size, margin, original_margin,
                   peak_pnl_pct, unrealized_pnl, partial_realized_pnl, partial_fee_paid,
                   close_reason, opened_at, closed_at
            from paper_positions
            where timeframe_tier in ('mid','long') and status='closed'
              and closed_at >= now() - interval '30 days'
            order by opened_at
        """)).fetchall()]
        # 账户权益
        accts = {}
        try:
            for r in c.execute(text("""
                select id, balance, equity, initial_balance from paper_accounts
                where id in (select distinct account_id from paper_positions
                             where timeframe_tier in ('mid','long') and status='closed'
                               and closed_at >= now() - interval '30 days')
            """)).fetchall():
                accts[r[0]] = dict(r._mapping)
        except Exception as exc:
            print("账户表读取失败:", exc)

    print(f"近 30 天 mid/long 已平仓: {len(rows)} 笔")
    print(f"账户: {accts}")

    notional = []
    risk = []
    margin = []
    levs = []
    risk_pct = []
    for r in rows:
        entry = float(r["entry_price"] or 0)
        sz = float(r["original_size"] or r["size"] or 0)
        lev = float(r["leverage"] or 0)
        mg = float(r["original_margin"] or r["margin"] or 0)
        sl = float(r["sl_price"] or 0)
        if entry <= 0 or sz <= 0:
            continue
        ntl = entry * sz
        notional.append(ntl)
        if mg > 0:
            margin.append(mg)
        if lev > 0:
            levs.append(lev)
        if sl > 0:
            rk = abs(entry - sl) * sz
            risk.append(rk)
            eq = float((accts.get(r["account_id"]) or {}).get("equity")
                       or (accts.get(r["account_id"]) or {}).get("balance") or 0)
            if eq > 0:
                risk_pct.append(rk / eq * 100)

    def desc(name, xs):
        if not xs:
            print(f"  {name}: 无数据")
            return
        xs_sorted = sorted(xs)
        print(f"  {name}: n={len(xs)} 中位={st.median(xs):.2f} 均值={st.mean(xs):.2f} "
              f"p10={xs_sorted[len(xs)//10]:.2f} p90={xs_sorted[min(len(xs)-1, 9*len(xs)//10)]:.2f} "
              f"max={max(xs):.2f}")

    print("\n=== 分布 ===")
    desc("名义敞口 USD", notional)
    desc("保证金 USD", margin)
    desc("SL 风险 USD", risk)
    desc("杠杆", levs)
    desc("SL 风险占权益 %", risk_pct)

    print("\n=== 杠杆 × 保证金 × 名义（前 12 笔）===")
    print(f"{'时间':<12}{'币':<8}{'杠杆':>5}{'名义USD':>11}{'保证金USD':>11}{'SL%':>7}{'风险USD':>10}")
    for r in rows[:12]:
        entry = float(r["entry_price"] or 0)
        sz = float(r["original_size"] or r["size"] or 0)
        sl = float(r["sl_price"] or 0)
        ntl = entry * sz
        mg = float(r["original_margin"] or r["margin"] or 0)
        slp = abs(entry - sl) / entry * 100 if (sl and entry) else 0
        print(f"{str(r['opened_at'])[5:16]:<12}{r['symbol']:<8}{float(r['leverage'] or 0):>5.1f}"
              f"{ntl:>11.2f}{mg:>11.2f}{slp:>7.2f}{(slp/100*ntl):>10.2f}")

    print("\n=== 「浮盈→亏损」模式的 USD 亏损（峰值≥0.5% 且净亏）===")
    pat = []
    for r in rows:
        peak = float(r["peak_pnl_pct"] or 0) * 100
        usd = (float(r["unrealized_pnl"] or 0) + float(r["partial_realized_pnl"] or 0)
               - float(r["partial_fee_paid"] or 0))
        if peak >= 0.5 and usd < 0:
            pat.append((r, peak, usd))
    print(f"  n={len(pat)} 合计 USD={sum(x[2] for x in pat):+.2f} "
          f"均值={sum(x[2] for x in pat)/max(1,len(pat)):+.2f}")
    for r, peak, usd in sorted(pat, key=lambda x: x[2])[:10]:
        lev = float(r["leverage"] or 0)
        entry = float(r["entry_price"] or 0)
        sz = float(r["original_size"] or r["size"] or 0)
        mg = float(r["original_margin"] or r["margin"] or 0)
        print(f"  {str(r['opened_at'])[5:16]} {r['symbol']:<8} 峰值={peak:>+5.2f}%(保证金{peak*lev:>+6.1f}%) "
              f"USD={usd:>+8.2f} 保证金={mg:>8.2f} USD/保证金={usd/mg*100 if mg else 0:>+7.1f}% "
              f"{str(r['close_reason'] or '')[:22]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
