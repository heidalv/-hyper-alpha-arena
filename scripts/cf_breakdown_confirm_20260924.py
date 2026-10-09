# -*- coding: utf-8 -*-
"""[2026-09-24 新目标 R1] 「真破位 vs 假回调」过滤器研究：从"首次跌破就进"改为"确认后跟进"。

上一轮的否证：4h 首次跌破 EMA50 就做空，四个变体全亏（−1.2~−2.6%/笔）——
说明**首次跌破多为假信号**。本轮测"确认型"信号（都在 09-15 后样本，出场统一 SL 3%/追踪5-2.5/48h）：

  空侧
   E 破位后继续破：跌破 EMA50 后的 1~4 根内，出现"收盘 < 前一根最低"→ 开空
   F 创新 24h 新低：收盘 < 前 6 根(zero-based)最低 → 开空
   G E + 量能放大：成交量 > 近 20 根均值×1.5
   H 回抽不过 EMA50 再破：跌破后曾反弹到 EMA50 附近（差 <1%），随后收盘再破前一低 → 开空
  多侧（对称：回调后确认再进）
   L 回踩 EMA50 不破：4h 收盘曾跌破 EMA50，随后一根收盘重新站上 EMA50 且高于前一根最高 → 做多

输出：逐变体 n / 合计 / 均 / 胜率 / 前后半；双价源。只读。
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
FEE_PP = 0.10
PEN_PP = 0.237
SL_CAP_PCT = 3.0
TRAIL_ACT, TRAIL_CB = 5.0, 2.5
MAX_HOLD_H = 48.0
MIN_GAP_H = 6.0


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
        """select timestamp, open_price, high_price, low_price, close_price, volume from crypto_klines
           where symbol=%s and exchange='binance' and period='4h' and environment='mainnet'
           order by timestamp""", (sym,))
    return [{"ts": int(r[0]), "o": float(r[1]), "h": float(r[2]), "l": float(r[3]),
             "c": float(r[4]), "v": float(r[5] or 0)} for r in cur.fetchall()]


def load_path(cur, sym, t0, t1, source):
    if source == "agg":
        cur.execute(
            """select timestamp, high_price, low_price, vwap from market_trades_aggregated
               where symbol=%s and exchange='binance' and timestamp between %s and %s order by timestamp""",
            (sym, t0 * 1000, t1 * 1000))
        return [(int(r[0]) // 1000, float(r[1]), float(r[2]), float(r[3] or r[1])) for r in cur.fetchall()]
    cur.execute(
        """select timestamp, high_price, low_price, close_price from crypto_klines
           where symbol=%s and exchange='binance' and period='1m' and environment='mainnet'
             and timestamp between %s and %s order by timestamp""", (sym, t0, t1))
    return [(int(r[0]), float(r[1]), float(r[2]), float(r[3])) for r in cur.fetchall()]


def sim(entry, bars, is_short):
    sign = -1.0 if is_short else 1.0
    sl = entry * (1 - sign * SL_CAP_PCT / 100.0)
    peak = 0.0
    dead = bars[0][0] + int(MAX_HOLD_H * 3600)
    exit_px = None
    for ts, hi, lo, cl in bars:
        if ts > dead:
            exit_px = cl
            break
        if peak >= TRAIL_ACT:
            cand = entry * (1 + sign * (peak - TRAIL_CB) / 100.0)
            sl = max(sl, cand) if not is_short else min(sl, cand)
        adverse = hi if is_short else lo
        if (adverse >= sl) if is_short else (adverse <= sl):
            exit_px = sl * (1 + sign * PEN_PP / 100.0)
            break
        ext = min(lo, cl) if is_short else max(hi, cl)
        r = ((entry - ext) / entry * 100.0) if is_short else ((ext - entry) / entry * 100.0)
        peak = max(peak, r)
    if exit_px is None:
        exit_px = bars[-1][3]
    ret = ((entry - exit_px) / entry * 100.0) if is_short else ((exit_px - entry) / entry * 100.0)
    return ret - FEE_PP


def signals_south(bars, i, kind):
    """空侧确认型信号（用 4h 结构，索引 i 为当前根）。"""
    b, p1, p2 = bars[i], bars[i - 1], bars[i - 2]
    if kind == "E 破位后继续破":
        # 近 1~4 根内曾收盘 < EMA50，且当前收盘 < 前一根最低
        for k in range(1, 5):
            if i - k < 0:
                break
            bk = bars[i - k]
            if bk["c"] < bk["ema50"]:
                return b["c"] < p1["l"]
        return False
    if kind == "F 创新24h新低":
        low6 = min(x["l"] for x in bars[i - 6:i])
        return b["c"] < low6
    if kind == "G E+量能放大":
        ok_e = signals_south(bars, i, "E 破位后继续破")
        v20 = st.mean([x["v"] for x in bars[i - 20:i]]) if i >= 20 else 0
        return ok_e and v20 > 0 and b["v"] > v20 * 1.5
    if kind == "H 回抽不过EMA50再破":
        # 近 6 根内跌破过 EMA50，且有某根收盘接近 EMA50（差<1%），当前收盘再破前低
        broke = False
        retest = False
        for k in range(1, 7):
            if i - k < 0:
                break
            bk = bars[i - k]
            if bk["c"] < bk["ema50"]:
                broke = True
            if broke and abs(bk["c"] / bk["ema50"] - 1.0) < 0.01:
                retest = True
        return broke and retest and b["c"] < p1["l"]
    return False


def main() -> int:
    variants = ["E 破位后继续破", "F 创新24h新低", "G E+量能放大", "H 回抽不过EMA50再破", "L 回踩不破做多"]
    with psycopg.connect(MARKET, autocommit=True) as mc:
        cur = mc.cursor()
        data = {s: load_4h(cur, s) for s in SYMS}
        for s, bars in data.items():
            e50 = ema([b["c"] for b in bars], 50)
            for i, b in enumerate(bars):
                b["ema50"] = e50[i]
        for source in ("kline", "agg"):
            print("=" * 100)
            print("源=%s（出场统一 SL 3%% / 追踪 5.0-2.5 / 48h / 费 0.10pp）" % source)
            print("  %-22s %5s %10s %9s %8s %9s %9s" % ("变体", "n", "合计%", "均/笔%", "胜率", "前半%", "后半%"))
            for kind in variants:
                trades = []
                for sym, bars in data.items():
                    last_ts = -10 ** 9
                    for i in range(25, len(bars)):
                        b = bars[i]
                        if b["ts"] < SINCE:
                            continue
                        if b["ts"] - last_ts < MIN_GAP_H * 3600:
                            continue
                        if kind.startswith("L"):
                            # 多侧：曾跌破 EMA50，当前收盘重回 EMA50 上方且高于前一根最高
                            broke = any(bars[i - k]["c"] < bars[i - k]["ema50"] for k in range(1, 5) if i - k >= 0)
                            sig = broke and b["c"] > b["ema50"] and b["c"] > bars[i - 1]["h"]
                        else:
                            sig = signals_south(bars, i, kind)
                        if not sig:
                            continue
                        last_ts = b["ts"]
                        path = load_path(cur, sym, b["ts"], b["ts"] + int(MAX_HOLD_H * 3600) + 3600, source)
                        if len(path) < 10:
                            continue
                        ret = sim(b["c"], path, is_short=not kind.startswith("L"))
                        trades.append({"sym": sym, "ts": b["ts"], "ret": ret})
                if not trades:
                    print("  %-22s %5d %10s" % (kind, 0, "无信号"))
                    continue
                trades.sort(key=lambda x: x["ts"])
                rets = [t["ret"] for t in trades]
                half = len(rets) // 2
                print("  %-22s %5d %+10.2f %+9.3f %7.0f%% %+9.2f %+9.2f"
                      % (kind, len(rets), sum(rets), st.mean(rets),
                         100.0 * sum(1 for x in rets if x > 0) / len(rets),
                         sum(rets[:half]), sum(rets[half:])))
            # 明细：各变体最差/最好 3 笔
            print("  细分（按币）省略；如需逐笔请加 --detail")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
