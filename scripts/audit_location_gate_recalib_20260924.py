# -*- coding: utf-8 -*-
"""[R1] 位置闸重标定（用已落地的新出场栈，09-15+ 样本）+ 一个数据异常复核。

背景：日志里 mid 车道最常见的缩仓原因是位置闸——
  `位置闸 paper 缩仓×0.25（location_gate_veto: 24h区间分位71%≥40% 高位追多（实测该带 24h 胜率 0.15-0.23））`
即「24h 区间分位 ≥40%」就把 mid 仓位砍到 1/4，而上行趋势里绝大多数时点分位都 >40%。
该标定（胜率 0.15-0.23）早于本轮落地的动态止盈止损栈（追踪 5/2.5 + SL 3% + 分批止盈）。

本脚本回答：
  1) 在 09-15+ 样本、用**已落地出场规则**下，pos24 分档的前向 48h 收益是否仍然单调变差？
     → 若各档无差异，则"高位追多"这条标定对新出场栈已失效（应放宽缩仓）。
  2) 日志里 `UNI 24h已跌-75.7%` 这种值是否真实（数据异常会让闸误判）。
只读。
"""
from __future__ import annotations

import bisect
import datetime as dt
import io
import statistics as st
import sys

import psycopg

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
MARKET = "postgresql://laobao:alpha_pass@localhost:5432/alpha_market"
CST = dt.timezone(dt.timedelta(hours=8))
SINCE = int(dt.datetime(2026, 9, 15, 0, 0).replace(tzinfo=CST).timestamp())
MAJORS = ["BTC", "ETH", "SOL", "XRP", "BNB", "LINK", "AVAX", "UNI", "VIRTUAL", "ASTER"]
FEE_PP, PEN_PP, SL_CAP, TRAIL_ACT, TRAIL_CB, MAX_HOLD_H = 0.10, 0.237, 3.0, 5.0, 2.5, 48.0
SECS = {"5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400}


def sim(entry, bars):
    sl = entry * (1 - SL_CAP / 100.0)
    peak = 0.0
    dead = bars[0][0] + int(MAX_HOLD_H * 3600)
    exit_px = None
    for ts, hi, lo, cl in bars:
        if ts > dead:
            exit_px = cl
            break
        if peak >= TRAIL_ACT:
            sl = max(sl, entry * (1 + (peak - TRAIL_CB) / 100.0))
        if lo <= sl:
            exit_px = sl * (1 + PEN_PP / 100.0)
            break
        peak = max(peak, (max(hi, cl) - entry) / entry * 100.0)
    if exit_px is None:
        exit_px = bars[-1][3]
    return (exit_px - entry) / entry * 100.0 - FEE_PP


def pick_interval(cur, ex, sym, t0, t1):
    for per in ("5m", "15m", "30m", "1h", "4h"):
        exp = max(1.0, (t1 - t0) / SECS[per])
        cur.execute(
            """select timestamp, high_price, low_price, close_price from crypto_klines
               where symbol=%s and exchange=%s and period=%s and environment='mainnet'
                 and timestamp between %s and %s order by timestamp""", (sym, ex, per, t0, t1))
        rows = [(int(r[0]), float(r[1]), float(r[2]), float(r[3])) for r in cur.fetchall()]
        if len(rows) / exp >= 0.90:
            return per, rows
    return None, []


def main() -> int:
    now = int(dt.datetime.now(CST).timestamp())
    with psycopg.connect(MARKET, autocommit=True) as mc:
        cur = mc.cursor()
        # ── 2) UNI 数据异常复核 ──
        print("[2] 复核日志中 UNI「24h已跌-75.7%」是否真实：")
        cur.execute(
            """select timestamp, close_price from crypto_klines where symbol='UNI' and exchange='binance'
               and period='1h' and environment='mainnet' and timestamp between %s and %s
               order by timestamp""", (SINCE, now))
        rows = cur.fetchall()
        if rows:
            vals = [float(r[1]) for r in rows]
            mx, mn = max(vals), min(vals)
            print("    UNI 1h 收盘 09-15 起：min=%.4f max=%.4f 最新=%.4f；最大单小时跌幅 %.2f%%"
                  % (mn, mx, vals[-1], min((vals[i] / vals[i - 1] - 1) * 100 for i in range(1, len(vals)))))
            big = [(dt.datetime.fromtimestamp(int(rows[i][0]), CST).strftime("%m-%d %H:%M"),
                    vals[i - 1], vals[i]) for i in range(1, len(vals))
                   if abs(vals[i] / vals[i - 1] - 1) > 0.20]
            print("    单小时波动 >20%% 的点：%s" % (big[:5] if big else "无 → 说明「-75.7%」不是 1h 级真实跌幅"))
        # ── 1) pos24 分档审计 ──
        print("\n[1] pos24 分档 × 已落地出场（SL3%%/追踪5-2.5/48h）× 09-15+ × 池A10主流：")
        for ex in ("binance",):
            book = {}
            for s in MAJORS:
                cur.execute(
                    """select timestamp, open_price, high_price, low_price, close_price, volume
                       from crypto_klines where symbol=%s and exchange='binance' and period='4h'
                         and environment='mainnet' order by timestamp""", (s,))
                bars = [{"ts": int(r[0]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4])}
                        for r in cur.fetchall()]
                if len(bars) < 60 or now - bars[-1]["ts"] > 6 * 3600:
                    continue
                idx = [i for i, b in enumerate(bars) if b["ts"] >= SINCE]
                if not idx:
                    continue
                t0 = bars[idx[0]]["ts"]
                t1 = min(bars[idx[-1]]["ts"] + int(MAX_HOLD_H * 3600) + 7200, now)
                per, path = pick_interval(cur, ex, s, t0, t1)
                if not path:
                    continue
                book[s] = (bars, path, [x[0] for x in path], per)
            buckets = {k: [] for k in ("<20%", "20-40%", "40-60%", "60-80%", "≥80%")}
            allr = []
            for s, (bars, p, tss, per) in book.items():
                for i, b in enumerate(bars):
                    if b["ts"] < SINCE or i < 24:
                        continue
                    if tss[-1] < b["ts"] + int(MAX_HOLD_H * 3600):
                        continue
                    win = bars[i - 23:i + 1]
                    hi = max(x["h"] for x in win)
                    lo = min(x["l"] for x in win)
                    if hi <= lo:
                        continue
                    pos = (b["c"] - lo) / (hi - lo) * 100
                    j = bisect.bisect_left(tss, b["ts"])
                    k = bisect.bisect_left(tss, b["ts"] + int(MAX_HOLD_H * 3600))
                    exp = (p[k][0] - p[j][0]) / SECS[per]
                    if k - j < 8 or (exp > 0 and (k - j) / exp < 0.85):
                        continue
                    r = sim(p[j][3], p[j:k + 1])
                    if r < -3.8:
                        continue
                    key = ("<20%" if pos < 20 else "20-40%" if pos < 40 else "40-60%" if pos < 60
                           else "60-80%" if pos < 80 else "≥80%")
                    buckets[key].append(r)
                    allr.append(r)
            print("    全样本基准：%+.3f%%/笔（n=%d）" % (st.mean(allr), len(allr)))
            print("    %-8s %5s %10s %9s %8s" % ("pos24档", "n", "均/笔%", "对基准差", "胜率"))
            for k, v in buckets.items():
                if not v:
                    continue
                print("    %-8s %5d %+10.3f %+9.3f %7.0f%%"
                      % (k, len(v), st.mean(v), st.mean(v) - st.mean(allr),
                         100.0 * sum(1 for x in v if x > 0) / len(v)))
            print("    → 若各档均值与基准差接近 0 且无单调性，说明「≥40% 高位追多」这条"
                  "在新出场栈下已无区分度（该标定早于追踪/SL3% 落地）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
