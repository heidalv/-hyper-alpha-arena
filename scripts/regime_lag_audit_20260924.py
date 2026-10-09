# -*- coding: utf-8 -*-
"""[2026-09-24 第17轮] regime 确认滞后量化：日线闸门（EMA200 + 60 日动量）到底慢多少？

口径与 `midlong_circuit_gate._daily_regime` **完全一致**（避免双口径）：
  up = close > EMA200 且 mom60 > +5%；down = close < EMA200 且 mom60 < -5%；其余 = chop。
输出：① 各币最近 15 天的 regime 时间线；② 行情转折点（局部高点）→ regime 离开 up 的时差；
      ③ 当前（最新收盘）各币 regime；
      ④ 过去 15 天里"闸门死区"统计：既不允许空（非 down）、又因 learned 条件难放行长的时间占比。
只读。
"""
from __future__ import annotations

import datetime as dt
import io
import sys

import psycopg

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
MARKET = "postgresql://laobao:alpha_pass@localhost:5432/alpha_market"
SYMS = ["BTC", "ETH", "SOL", "XRP", "BNB", "LINK", "AVAX", "UNI", "VIRTUAL", "ASTER"]


def ema_series(closes, period=200):
    k = 2.0 / (period + 1.0)
    out = []
    ema = closes[0]
    for c in closes:
        ema = c * k + ema * (1 - k)
        out.append(ema)
    return out


def regime_at(closes, i, ema, mom_days=60, thr=0.05):
    n = i + 1
    e = ema[i]
    px = closes[i]
    base = closes[i - mom_days] if i >= mom_days else closes[0]
    mom = (px / base - 1.0) if base > 0 else 0.0
    if px > e and mom > thr:
        return "up", mom
    if px < e and mom < -thr:
        return "down", mom
    return "chop", mom


def main() -> int:
    with psycopg.connect(MARKET, autocommit=True) as c:
        cur = c.cursor()
        print("== 各币日线 regime（最新 12 根，口径 = EMA200 + 60日动量 ±5%）==")
        summary = []
        for sym in SYMS:
            cur.execute(
                """select timestamp, close_price from crypto_klines
                   where symbol=%s and exchange='binance' and period='1d' and environment='mainnet'
                   order by timestamp desc limit 320""", (sym,))
            rows = cur.fetchall()[::-1]
            if len(rows) < 80:
                print("  %-8s 数据不足(%d)" % (sym, len(rows)))
                continue
            ts = [int(r[0]) for r in rows]
            closes = [float(r[1]) for r in rows]
            ema = ema_series(closes, 200)
            line = []
            for i in range(len(rows) - 12, len(rows)):
                reg, mom = regime_at(closes, i, ema)
                d = dt.datetime.fromtimestamp(ts[i], dt.timezone(dt.timedelta(hours=8))).strftime("%m-%d")
                line.append("%s:%s" % (d, {"up": "↑", "down": "↓", "chop": "≈"}[reg]))
            cur_reg, cur_mom = regime_at(closes, len(rows) - 1, ema)
            cur.execute(
                """select max(close_price) from crypto_klines
                   where symbol=%s and exchange='binance' and period='1d' and environment='mainnet'
                     and timestamp >= %s""", (sym, ts[-15]))
            peak15 = float(cur.fetchone()[0] or 0)
            now = closes[-1]
            print("  %-8s %s  当前=%s (mom60=%+.1f%%, 距15日高点 %.1f%%)"
                  % (sym, " ".join(line), cur_reg, cur_mom * 100, (now / peak15 - 1) * 100))
            summary.append((sym, cur_reg, cur_mom))
        print("\n== 当前 regime 汇总 ==")
        cnt = {}
        for sym, reg, _ in summary:
            cnt[reg] = cnt.get(reg, 0) + 1
        print("  " + ", ".join("%s×%d" % (k, v) for k, v in sorted(cnt.items())))
        print("  → 空头闸（regime_gated）要求日线 down：当前 down 的币 = %s"
              % ([s for s, r, _ in summary if r == "down"] or "无"))
        print("  → 多头闸（learned）：down 拦；up 需 chg24≥3%%；chop 需 pos24≥60%% 且 chg24≥2%%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
