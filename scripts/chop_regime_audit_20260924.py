# -*- coding: utf-8 -*-
"""[2026-09-24 第17轮] 震荡行情专项：中线/长线在"震荡 vs 趋势"入场环境下的真实表现（09-15 后样本）。

分类口径（入场时点，模型面最小）：
  · 用 4h K 线：`mom24` = 近 6 根 4h 收益；`dist50` = 收盘距 EMA50(4h) 的百分比
  · chop（震荡）：|mom24| < 2.0% 且 |dist50| < 1.5%
  · trend（趋势）：其余（再细分 up/down：mom24 符号）
输出：分车道 × 分环境的 笔数 / 合计 / 均 / 胜率 / 平均峰值；以及"震荡里空仓"的对照算术。
只读。
"""
from __future__ import annotations

import datetime as dt
import io
import sys

import psycopg

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ARENA = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"
MARKET = "postgresql://laobao:alpha_pass@localhost:5432/alpha_market"
CST = dt.timezone(dt.timedelta(hours=8))
SINCE = "2026-09-15 00:00:00"


def ema(vals, period):
    k = 2.0 / (period + 1.0)
    e = vals[0]
    out = []
    for v in vals:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def main() -> int:
    with psycopg.connect(ARENA, autocommit=True) as ac:
        cur = ac.cursor()
        cur.execute("SET app.is_admin='on'")
        cur.execute(
            """select id, symbol, timeframe_tier, side, entry_price, opened_at, status,
                      unrealized_pnl, peak_pnl_pct, coalesce(partial_realized_pnl,0) pr
               from paper_positions
               where account_id=14 and opened_at > timestamp '2026-09-15 00:00:00'
               order by opened_at""")
        cols = [d[0] for d in cur.description]
        pos = [dict(zip(cols, r)) for r in cur.fetchall()]

    env = {}
    with psycopg.connect(MARKET, autocommit=True) as mc:
        cur = mc.cursor()
        syms = sorted({p["symbol"] for p in pos})
        for sym in syms:
            cur.execute(
                """select timestamp, close_price from crypto_klines
                   where symbol=%s and exchange='binance' and period='4h' and environment='mainnet'
                   order by timestamp""", (sym,))
            rows = [(int(r[0]), float(r[1])) for r in cur.fetchall()]
            if len(rows) < 60:
                continue
            ts = [r[0] for r in rows]
            closes = [r[1] for r in rows]
            e50 = ema(closes, 50)
            env[sym] = (ts, closes, e50)

    buckets = {}
    rows_out = []
    for p in pos:
        sym = p["symbol"]
        if sym not in env:
            continue
        ts, closes, e50 = env[sym]
        o = int(p["opened_at"].replace(tzinfo=CST).timestamp())
        i = max((k for k, t in enumerate(ts) if t <= o), default=None)
        if i is None or i < 6:
            continue
        mom24 = (closes[i] / closes[i - 6] - 1.0) * 100.0
        dist50 = (closes[i] / e50[i] - 1.0) * 100.0
        if abs(mom24) < 2.0 and abs(dist50) < 1.5:
            kind = "chop(震荡)"
        elif mom24 > 0:
            kind = "trend_up"
        else:
            kind = "trend_down"
        pnl = float(p["unrealized_pnl"] or 0) + float(p["pr"] or 0)
        lane = str(p["timeframe_tier"])
        b = buckets.setdefault((lane, kind), {"n": 0, "sum": 0.0, "win": 0, "peak": 0.0})
        b["n"] += 1
        b["sum"] += pnl
        b["win"] += 1 if pnl > 0 else 0
        b["peak"] += float(p["peak_pnl_pct"] or 0) * 100
        rows_out.append((lane, kind, sym, str(p["opened_at"])[5:16], pnl, mom24, dist50))

    print("== 09-15 后：分车道 × 入场环境（4h 口径）==")
    print("  %-6s %-12s %4s %10s %9s %7s %9s" % ("车道", "环境", "笔数", "合计USD", "均/笔", "胜率", "均峰值%"))
    for lane in ("mid", "long"):
        for kind in ("chop(震荡)", "trend_up", "trend_down"):
            b = buckets.get((lane, kind))
            if not b:
                continue
            print("  %-6s %-12s %4d %+10.2f %+9.2f %6.0f%% %9.2f"
                  % (lane, kind, b["n"], b["sum"], b["sum"] / b["n"], 100.0 * b["win"] / b["n"],
                     b["peak"] / b["n"]))
    # 对照算术：若"震荡里空仓"会怎样
    for lane in ("mid", "long"):
        b = buckets.get((lane, "chop(震荡)"))
        allb = [v for (l, k), v in buckets.items() if l == lane]
        tot_n = sum(v["n"] for v in allb)
        tot_s = sum(v["sum"] for v in allb)
        if b:
            print("  → %s：震荡桶 %d 笔 / %+.2f；剔除后 总 %+.2f（%d 笔）"
                  % (lane, b["n"], b["sum"], tot_s - b["sum"], tot_n - b["n"]))
    print("\n== 震荡桶明细（最多 20 条）==")
    for r in [x for x in rows_out if x[1] == "chop(震荡)"][:20]:
        print("  %-6s %-7s %s 盈亏 %+7.2f (mom24 %+5.2f%%, 距EMA50 %+5.2f%%)" % (r[0], r[2], r[3], r[4], r[5], r[6]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
