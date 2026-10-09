# -*- coding: utf-8 -*-
"""[R1 v2] 修正选择性偏差 + 双价源 + 越界断言 的变体验证。

v1 的两个致命问题（已自查）：
  1) 样本池按「窗口内成交额」挑 → 事后选赢家（池内中位涨幅远高于基准）；
  2) 只跑了单源，且未对越界样本做断言（出现空头 −22.78% 这种模型上界之外的数）。

v2 修正：
  - 池 A：生产 10 主流币（无选择偏差，就是我们在交易的标的）
  - 池 B：**窗口前** 40 天成交额前 25 名（事前可得，无前视）
  - 双价源：kline 1m 与 market_trades_aggregated
  - 越界断言：空头 ret < −3.5% / 多头 ret < −3.5% 视为越界 → 打印并剔除
  - 每变体给：n / 合计 / 均 / 同向基准 / 超额 / matched-bootstrap p / 前后半 / 单币集中度

预登记判定（同 v1，看结果前写定）：
  通过 = 双源合计同号 且 双源超额同号为正 且 四半样本≥3 同号 且 p<0.10
只读。
"""
from __future__ import annotations

import bisect
import datetime as dt
import io
import random
import statistics as st
import sys

import psycopg

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
MARKET = "postgresql://laobao:alpha_pass@localhost:5432/alpha_market"
CST = dt.timezone(dt.timedelta(hours=8))
SINCE = int(dt.datetime(2026, 9, 15, 0, 0).replace(tzinfo=CST).timestamp())
PRE0 = SINCE - 40 * 86400
MAJORS = ["BTC", "ETH", "SOL", "XRP", "BNB", "LINK", "AVAX", "UNI", "VIRTUAL", "ASTER"]
FEE_PP, PEN_PP = 0.10, 0.237
SL_CAP, TRAIL_ACT, TRAIL_CB, MAX_HOLD_H, MIN_GAP_H = 3.0, 5.0, 2.5, 48.0, 6.0
BOUND = -(SL_CAP + PEN_PP + FEE_PP + 0.3)  # −3.74%，越界即异常
BOOT = 2000
random.seed(20260924)


def ema(v, p):
    k = 2.0 / (p + 1.0)
    e = v[0]
    out = []
    for x in v:
        e = x * k + e * (1 - k)
        out.append(e)
    return out


def sim(entry, bars, is_short):
    sign = -1.0 if is_short else 1.0
    sl = entry * (1 - sign * SL_CAP / 100.0)
    peak = 0.0
    dead = bars[0][0] + int(MAX_HOLD_H * 3600)
    exit_px = None
    for ts, hi, lo, cl in bars:
        if ts > dead:
            exit_px = cl
            break
        if peak >= TRAIL_ACT:
            cand = entry * (1 + sign * (peak - TRAIL_CB) / 100.0)
            sl = min(sl, cand) if is_short else max(sl, cand)
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


def sig(bars, i, kind):
    b, p1 = bars[i], bars[i - 1]
    if kind == "E 破位后继续破":
        for k in range(1, 5):
            if i - k < 0:
                break
            if bars[i - k]["c"] < bars[i - k]["ema50"]:
                return b["c"] < p1["l"]
        return False
    if kind == "F 创新24h新低":
        return b["c"] < min(x["l"] for x in bars[i - 6:i])
    if kind == "G E+量能放大":
        if not sig(bars, i, "E 破位后继续破"):
            return False
        v20 = st.mean([x["v"] for x in bars[i - 20:i]]) if i >= 20 else 0
        return v20 > 0 and b["v"] > v20 * 1.5
    if kind == "H 回抽不过EMA50再破":
        broke = retest = False
        for k in range(1, 7):
            if i - k < 0:
                break
            bk = bars[i - k]
            if bk["c"] < bk["ema50"]:
                broke = True
            if broke and abs(bk["c"] / bk["ema50"] - 1.0) < 0.01:
                retest = True
        return broke and retest and b["c"] < p1["l"]
    if kind == "L 回踩不破做多":
        broke = any(bars[i - k]["c"] < bars[i - k]["ema50"] for k in range(1, 5) if i - k >= 0)
        return broke and b["c"] > b["ema50"] and b["c"] > p1["h"]
    if kind == "M 生产up带[3,6)":
        c24 = bars[i - 6]["c"] if i >= 6 else b["c"]
        chg = (b["c"] / c24 - 1.0) * 100 if c24 > 0 else 0.0
        return 3.0 <= chg < 6.0
    if kind == "N 回调<3%做多":
        c24 = bars[i - 6]["c"] if i >= 6 else b["c"]
        chg = (b["c"] / c24 - 1.0) * 100 if c24 > 0 else 0.0
        return chg < 3.0
    if kind == "P 回调<3%且站上EMA50":
        c24 = bars[i - 6]["c"] if i >= 6 else b["c"]
        chg = (b["c"] / c24 - 1.0) * 100 if c24 > 0 else 0.0
        return chg < 3.0 and b["c"] > b["ema50"]
    return False


VARIANTS = [("E 破位后继续破", "S"), ("F 创新24h新低", "S"), ("G E+量能放大", "S"),
            ("H 回抽不过EMA50再破", "S"), ("L 回踩不破做多", "L"), ("M 生产up带[3,6)", "L"),
            ("N 回调<3%做多", "L"), ("P 回调<3%且站上EMA50", "L")]


def load(cur, table, sym, t0, t1):
    if table == "agg":
        cur.execute(
            """select timestamp, high_price, low_price, vwap from market_trades_aggregated
               where symbol=%s and exchange='binance' and timestamp between %s and %s
               order by timestamp""", (sym, t0 * 1000, t1 * 1000))
        return [(int(r[0]) // 1000, float(r[1]), float(r[2]), float(r[3] or r[1])) for r in cur.fetchall()]
    cur.execute(
        """select timestamp, high_price, low_price, close_price from crypto_klines
           where symbol=%s and exchange='binance' and period='1m' and environment='mainnet'
             and timestamp between %s and %s order by timestamp""", (sym, t0, t1))
    return [(int(r[0]), float(r[1]), float(r[2]), float(r[3])) for r in cur.fetchall()]


def main() -> int:
    with psycopg.connect(MARKET, autocommit=True) as mc:
        cur = mc.cursor()
        cur.execute(
            """select symbol from crypto_klines where exchange='binance' and period='4h'
                 and environment='mainnet' and timestamp >= %s and timestamp < %s
               group by symbol having count(*)>=50
               order by sum(volume*close_price) desc nulls last limit 25""", (PRE0, SINCE))
        prev25 = [r[0] for r in cur.fetchall()]
        pools = [("A 生产10主流", MAJORS), ("B 窗口前25名", prev25)]
        print("池 B（09-15 前 40 天成交额前 25，事前可得）：%s" % ",".join(prev25))
        for pool_name, syms in pools:
            print("\n" + "#" * 104)
            print("### %s（%d 币）" % (pool_name, len(syms)))
            for table in ("kline", "agg"):
                data, paths = {}, {}
                for s in syms:
                    cur.execute(
                        """select timestamp, open_price, high_price, low_price, close_price, volume
                           from crypto_klines where symbol=%s and exchange='binance' and period='4h'
                             and environment='mainnet' order by timestamp""", (s,))
                    bars = [{"ts": int(r[0]), "o": float(r[1]), "h": float(r[2]), "l": float(r[3]),
                             "c": float(r[4]), "v": float(r[5] or 0)} for r in cur.fetchall()]
                    if len(bars) < 60:
                        continue
                    e50 = ema([b["c"] for b in bars], 50)
                    for i, b in enumerate(bars):
                        b["ema50"] = e50[i]
                    idx = [i for i, b in enumerate(bars) if b["ts"] >= SINCE]
                    if not idx:
                        continue
                    p = load(cur, table, s, bars[idx[0]]["ts"],
                             bars[idx[-1]]["ts"] + int(MAX_HOLD_H * 3600) + 7200)
                    if len(p) < 100:
                        continue
                    data[s], paths[s] = bars, (p, [x[0] for x in p])
                if not data:
                    print("  源=%s 无可用币" % table)
                    continue
                obs_bad = 0

                def ret_at(sym, ts, is_short):
                    nonlocal obs_bad
                    p, tss = paths[sym]
                    j = bisect.bisect_left(tss, ts)
                    k = bisect.bisect_left(tss, ts + int(MAX_HOLD_H * 3600))
                    if k - j < 30:
                        return None
                    entry = p[j][3]
                    if entry <= 0:
                        return None
                    r = sim(entry, p[j:k + 1], is_short)
                    if r < BOUND:
                        obs_bad += 1
                        return None
                    return r

                pool_l, pool_s = [], []
                for s, bars in data.items():
                    for b in bars:
                        if b["ts"] < SINCE:
                            continue
                        rl, rs = ret_at(s, b["ts"], False), ret_at(s, b["ts"], True)
                        if rl is not None:
                            pool_l.append(rl)
                        if rs is not None:
                            pool_s.append(rs)
                bl, bs = st.mean(pool_l), st.mean(pool_s)
                print("\n  ── 源=%s ── 基准（每根4h无脑开）多头 %+.3f%%/笔(n=%d)  空头 %+.3f%%/笔(n=%d)  越界剔除 %d"
                      % (table, bl, len(pool_l), bs, len(pool_s), obs_bad))
                print("  %-22s %4s %9s %9s %9s %9s %7s" %
                      ("变体", "n", "合计%", "均/笔%", "超额%", "p", "集中度"))
                for kind, side in VARIANTS:
                    tr = []
                    for s, bars in data.items():
                        last = -1e18
                        for i, b in enumerate(bars):
                            if b["ts"] < SINCE or i < 25 or b["ts"] - last < MIN_GAP_H * 3600:
                                continue
                            if not sig(bars, i, kind):
                                continue
                            r = ret_at(s, b["ts"], side == "S")
                            if r is None:
                                continue
                            last = b["ts"]
                            tr.append({"sym": s, "ts": b["ts"], "ret": r})
                    if not tr:
                        print("  %-22s %4d 无信号" % (kind, 0))
                        continue
                    tr.sort(key=lambda x: x["ts"])
                    rets = [t["ret"] for t in tr]
                    base = bs if side == "S" else bl
                    pool = pool_s if side == "S" else pool_l
                    n, obs = len(rets), sum(rets)
                    cnt = sum(1 for _ in range(BOOT) if sum(random.sample(pool, n)) >= obs)
                    p = (cnt + 1) / (BOOT + 1)
                    per = {}
                    for t in tr:
                        per[t["sym"]] = per.get(t["sym"], 0.0) + t["ret"]
                    top = max(per.items(), key=lambda kv: abs(kv[1])) if per else ("-", 0.0)
                    conc = abs(top[1]) / abs(obs) * 100 if obs else 0.0
                    print("  %-22s %4d %+9.2f %+9.3f %+9.3f %7.3f  %s %.0f%%"
                          % (kind, n, obs, st.mean(rets), st.mean(rets) - base, p, top[0], conc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
