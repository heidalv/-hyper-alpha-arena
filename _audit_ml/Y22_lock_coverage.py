# -*- coding: utf-8 -*-
"""浮盈锁覆盖缺口核查（Y22）：哪些持仓跳过 ExitPolicy（v2 接管）？

`paper_trading_engine` 的 ExitPolicy 层对 `_v2_long_managed` 直接跳过：
  long_v2_enabled and (nature ∈ {trend_follow, position} or tier == 'long')
→ mid 层但 nature=trend_follow/position 的仓位也会跳过浮盈锁。
本脚本统计近 30 天 mid/long 成交按 (tier, nature) 分布，以及「浮盈→亏损」模式
在这两组中的占比。
"""
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
            select id, symbol, side, timeframe_tier, trade_nature, entry_price, close_price,
                   original_size, size, peak_pnl_pct, unrealized_pnl, partial_realized_pnl,
                   partial_fee_paid, close_reason, opened_at, closed_at
            from paper_positions
            where timeframe_tier in ('mid','long') and status='closed'
              and closed_at >= now() - interval '30 days'
            order by opened_at
        """)).fetchall()]

    def is_v2_managed(tier, nature):
        return str(nature or "").lower() in ("trend_follow", "position") or str(tier or "").lower() == "long"

    groups = defaultdict(list)
    for r in rows:
        entry = float(r["entry_price"] or 0)
        sz0 = float(r["original_size"] or r["size"] or 0)
        if entry <= 0 or sz0 <= 0:
            continue
        usd = (float(r["unrealized_pnl"] or 0) + float(r["partial_realized_pnl"] or 0)
               - float(r["partial_fee_paid"] or 0))
        peak = float(r["peak_pnl_pct"] or 0) * 100
        key = (str(r["timeframe_tier"]), str(r["trade_nature"] or "?"),
               "v2接管" if is_v2_managed(r["timeframe_tier"], r["trade_nature"]) else "ExitPolicy")
        groups[key].append({"usd": usd, "peak": peak,
                            "pat": peak >= 0.5 and usd < 0})

    print(f"{'tier':<6}{'nature':<14}{'锁层':<12}{'n':>4}{'总USD':>10}{'模式笔数':>9}{'模式USD':>10}")
    tot_pat = {"v2接管": [0, 0.0], "ExitPolicy": [0, 0.0]}
    for key in sorted(groups):
        v = groups[key]
        pat = [x for x in v if x["pat"]]
        print(f"{key[0]:<6}{key[1]:<14}{key[2]:<12}{len(v):>4}"
              f"{sum(x['usd'] for x in v):>+10.2f}{len(pat):>9}"
              f"{sum(x['usd'] for x in pat):>+10.2f}")
        tot_pat[key[2]][0] += len(pat)
        tot_pat[key[2]][1] += sum(x["usd"] for x in pat)

    print("\n=== 汇总 ===")
    for k, v in tot_pat.items():
        print(f"  {k:<12} 模式笔数={v[0]:>3} 模式 USD={v[1]:>+8.2f}")

    print("\n=== 被 v2 接管的 mid 层「模式」交易明细 ===")
    for key in sorted(groups):
        if key[2] != "v2接管" or key[0] != "mid":
            continue
        for r in groups[key]:
            pass
    for r in rows:
        if str(r["timeframe_tier"]) != "mid":
            continue
        if str(r["trade_nature"] or "").lower() not in ("trend_follow", "position"):
            continue
        entry = float(r["entry_price"] or 0)
        sz0 = float(r["original_size"] or r["size"] or 0)
        usd = (float(r["unrealized_pnl"] or 0) + float(r["partial_realized_pnl"] or 0)
               - float(r["partial_fee_paid"] or 0))
        peak = float(r["peak_pnl_pct"] or 0) * 100
        if peak >= 0.5 and usd < 0:
            print(f"  {str(r['opened_at'])[5:16]} {r['symbol']:<8} nature={r['trade_nature']:<12} "
                  f"峰值={peak:>+5.2f}% USD={usd:>+8.2f} {str(r['close_reason'])[:28]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
