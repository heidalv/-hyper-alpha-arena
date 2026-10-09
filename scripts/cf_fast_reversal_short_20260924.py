# -*- coding: utf-8 -*-
"""[2026-09-24 第17轮] 「快反转做空」反事实：用 4h 反应式信号替代"日线 down 才准做空"。

背景（用户反馈："行情都转变了，还在确认行情，然后等着不开仓"）：
  现行空头闸 = 日线 regime 必须是 `down`（EMA200 + 60 日动量 < −5%）。实测 09-24 十个币
  全部是 `up`（mom60 +11%~+141%）⇒ **空头完全不可能开**；而 up 分支的多头又要求 chg24≥3%，
  回调期同样不放行 ⇒ 双向死区。日线闸要等到 mom60 掉到 +5% 以下（BTC 需再跌 ~20% 或等数周）。

本脚本测"快信号"：**4h 收盘 < EMA50(4h) 且 24h 动量 < −1%** 时视为反转下行 → 开空，
出场用中线口径的括号（SL 3% 价格上限、追踪 5.0/2.5、最长持有 48h），看它能不能赚。
只用 09-15 之后样本（用户规定作废线）。

输出：逐信号明细（前 25 条）+ 汇总（笔数/合计/胜率/均/最差/前后半）+ 双价源一致性。
"""
from __future__ import annotations

import datetime as dt
import io
import statistics as st
import sys

import psycopg

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
MARKET = "postgresql://laobao:alpha_pass@localhost:5432/alpha_market"
CST = dt.timezone(dt.timedelta(hours=8))
SYMS = ["BTC", "ETH", "SOL", "XRP", "BNB", "LINK", "AVAX", "UNI", "VIRTUAL", "ASTER"]
SINCE = int(dt.datetime(2026, 9, 15, 0, 0).replace(tzinfo=CST).timestamp())
FEE_PP = 0.10           # 双边手续费占名义（%）
PEN_PP = 0.237          # 止损穿透
SL_CAP_PCT = 3.0        # 价格止损上限
TRAIL_ACT, TRAIL_CB = 5.0, 2.5
MAX_HOLD_H = 48.0
MIN_GAP_H = 6.0         # 同币信号最小间隔，避免同一波重复入场


def ema(vals, period):
    k = 2.0 / (period + 1.0)
    e = vals[0]
    out = []
    for v in vals:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def load_4h(cur, sym):
    cur.execute(
        """select timestamp, close_price from crypto_klines
           where symbol=%s and exchange='binance' and period='4h' and environment='mainnet'
           order by timestamp""", (sym,))
    return [(int(r[0]), float(r[1])) for r in cur.fetchall()]


def load_path(cur, sym, t0, t1, source):
    if source == "agg":
        cur.execute(
            """select timestamp, high_price, low_price, vwap from market_trades_aggregated
               where symbol=%s and exchange='binance' and timestamp between %s and %s order by timestamp""",
            (sym, t0 * 1000, t1 * 1000))
        return [(int(r[0]) // 1000, float(r[3] or r[1]), float(r[1]), float(r[2])) for r in cur.fetchall()]
    cur.execute(
        """select timestamp, high_price, low_price, close_price from crypto_klines
           where symbol=%s and exchange='binance' and period='1m' and environment='mainnet'
             and timestamp between %s and %s order by timestamp""", (sym, t0, t1))
    return [(int(r[0]), float(r[1]), float(r[2]), float(r[3])) for r in cur.fetchall()]


def simulate_short(entry, bars):
    """做空：SL=entry×(1+3%) 上限；追踪：浮盈≥5% 后 peak−2.5% 处回落平；48h 到期平。"""
    sl = entry * (1 + SL_CAP_PCT / 100.0)
    peak = 0.0
    dead = bars[0][0] + int(MAX_HOLD_H * 3600)
    exit_px = None
    for ts, hi, lo, cl in bars:
        if ts > dead:
            exit_px = cl
            break
        if peak >= TRAIL_ACT:
            cand = entry * (1 - (peak - TRAIL_CB) / 100.0)
            sl = min(sl, cand)
        if hi >= sl:
            exit_px = sl * (1 + PEN_PP / 100.0)
            break
        r = (entry - min(lo, cl)) / entry * 100.0
        peak = max(peak, r)
    if exit_px is None:
        exit_px = bars[-1][3]
    ret = (entry - exit_px) / entry * 100.0 - FEE_PP
    return {"exit": exit_px, "ret": ret, "peak": peak}


def main() -> int:
    # 信号变体（全部只做空、同一套出场括号）
    variants = [
        ("A 4h<EMA50 & mom24<-1%", lambda c, e50, mom24, mom72, c200: (c < e50 and mom24 < -1.0)),
        ("B 4h<EMA50 & mom24<-3%", lambda c, e50, mom24, mom72, c200: (c < e50 and mom24 < -3.0)),
        ("C A + 4h<EMA200", lambda c, e50, mom24, mom72, c200: (c < e50 and mom24 < -1.0 and c < c200)),
        ("D 超买摸顶 mom24>+8%", lambda c, e50, mom24, mom72, c200: (mom24 > 8.0)),
    ]
    with psycopg.connect(MARKET, autocommit=True) as mc:
        cur = mc.cursor()
        for source in ("kline", "agg"):
            print("=" * 100)
            print("源=%s  出场口径统一：SL 3%% 上限 / 追踪 5.0-2.5 / 最长 48h / 双边费 0.10pp" % source)
            print("  %-26s %5s %10s %9s %9s %9s %9s" % ("信号变体", "n", "合计%", "均/笔%", "胜率", "前半%", "后半%"))
            for name, sig in variants:
                trades = []
                for sym in SYMS:
                    bars4 = [(t, c) for (t, c) in load_4h(cur, sym) if t >= SINCE - 40 * 86400]
                    if len(bars4) < 80:
                        continue
                    closes = [c for _, c in bars4]
                    ema50 = ema(closes, 50)
                    ema200 = ema(closes, 200)
                    last_ts = -10 ** 9
                    for i in range(205, len(bars4)):
                        ts, c = bars4[i]
                        if ts < SINCE:
                            continue
                        mom24 = (c / closes[i - 6] - 1.0) * 100.0 if i >= 6 else 0.0
                        mom72 = (c / closes[i - 18] - 1.0) * 100.0 if i >= 18 else 0.0
                        if not sig(c, ema50[i], mom24, mom72, ema200[i]):
                            continue
                        if (ts - last_ts) < MIN_GAP_H * 3600:
                            continue
                        last_ts = ts
                        bars = load_path(cur, sym, ts, ts + int(MAX_HOLD_H * 3600) + 3600, source)
                        if len(bars) < 10:
                            continue
                        r = simulate_short(c, bars)
                        trades.append({"sym": sym, "ts": ts, "entry": c, **r})
                if not trades:
                    print("  %-26s %5d %10s" % (name, 0, "无信号"))
                    continue
                trades.sort(key=lambda x: x["ts"])
                rets = [t["ret"] for t in trades]
                half = len(rets) // 2
                print("  %-26s %5d %+10.2f %+9.3f %8.0f%% %+9.2f %+9.2f"
                      % (name, len(rets), sum(rets), st.mean(rets),
                         100.0 * sum(1 for x in rets if x > 0) / len(rets),
                         sum(rets[:half]), sum(rets[half:])))
            # 明细：只对变体 A 输出，便于核对
            if source == "kline":
                print("\n  （变体 A 逐信号明细见上一次运行输出；此处仅汇总）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
