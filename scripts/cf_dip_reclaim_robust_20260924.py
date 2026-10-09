# -*- coding: utf-8 -*-
"""[R1 v4] L 变体（回踩不破做多）稳健性攻坚：参数网格 / 单币集中 / 按币自助 / 频率。

v3 结论：L（近4根内曾跌回 EMA50 下 → 当前收盘重回 EMA50 上 且 破前一根高）在
  池A/池B × binance/okx 四个组合下：n=21~24、均 +4.7~6.0%/笔、对同向基准超额
  +1.95~+3.05pp、p=0.000~0.023、**8/8 半样本同号**。
但 n 小，且需排除：①只有这一组参数有效（过拟合）②单币主导 ③逐笔自助高估显著性。

本脚本：
  1) 参数网格：dip 回看 {2,3,4,6} × EMA {20,50,100} × 最小间隔 {4,6,12}h
     × 是否要求破前高；报告每格 n/均/超额/p，统计"正超额占比"
  2) 单币集中度：最大单币占合计绝对值的比例
  3) **按币聚类自助**：以币为单位重采样（保留币内相关），得到更保守的 p
  4) 频率：每币每天信号数（落地后开仓频率）
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
SL_CAP, TRAIL_ACT, TRAIL_CB, MAX_HOLD_H = 3.0, 5.0, 2.5, 48.0
BOUND = -(SL_CAP + PEN_PP + FEE_PP + 0.3)
SECS = {"5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400}
random.seed(20260924)


def ema(v, p):
    k = 2.0 / (p + 1.0)
    e = v[0]
    out = []
    for x in v:
        e = x * k + e * (1 - k)
        out.append(e)
    return out


def sim(entry, bars, is_short=False):
    sign = 1.0
    sl = entry * (1 - SL_CAP / 100.0)
    peak = 0.0
    dead = bars[0][0] + int(MAX_HOLD_H * 3600)
    exit_px = None
    for ts, hi, lo, cl in bars:
        if ts > dead:
            exit_px = cl
            break
        if peak >= TRAIL_ACT:
            cand = entry * (1 + (peak - TRAIL_CB) / 100.0)
            sl = max(sl, cand)
        if lo <= sl:
            exit_px = sl * (1 + PEN_PP / 100.0)
            break
        r = (max(hi, cl) - entry) / entry * 100.0
        peak = max(peak, r)
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


def sig_dip(bars, i, ema_key, dip_lb, need_high):
    b, p1 = bars[i], bars[i - 1]
    e = b[ema_key]
    if e <= 0:
        return False
    broke = any(bars[i - k][ema_key] > 0 and bars[i - k]["c"] < bars[i - k][ema_key]
                for k in range(1, dip_lb + 1) if i - k >= 0)
    if not broke or b["c"] <= e:
        return False
    return b["c"] > p1["h"] if need_high else True


def main() -> int:
    pool_defs = []
    with psycopg.connect(MARKET, autocommit=True) as mc:
        cur = mc.cursor()
        cur.execute(
            """select symbol from crypto_klines where exchange='binance' and period='4h'
                 and environment='mainnet' and timestamp >= %s and timestamp < %s
               group by symbol having count(*)>=50
               order by sum(volume*close_price) desc nulls last limit 25""", (PRE0, SINCE))
        pool_defs = [("A 生产10主流", MAJORS), ("B 事前25名", [r[0] for r in cur.fetchall()])]
        for pool_name, syms in pool_defs:
            for ex in ("binance", "okx"):
                book = {}
                now = int(dt.datetime.now(CST).timestamp())
                for s in syms:
                    cur.execute(
                        """select timestamp, open_price, high_price, low_price, close_price, volume
                           from crypto_klines where symbol=%s and exchange='binance' and period='4h'
                             and environment='mainnet' order by timestamp""", (s,))
                    bars = [{"ts": int(r[0]), "o": float(r[1]), "h": float(r[2]), "l": float(r[3]),
                             "c": float(r[4]), "v": float(r[5] or 0)} for r in cur.fetchall()]
                    if len(bars) < 120 or now - bars[-1]["ts"] > 6 * 3600:
                        continue
                    for per in (20, 50, 100):
                        col = "ema%d" % per
                        vals = ema([b["c"] for b in bars], per)
                        for i, b in enumerate(bars):
                            b[col] = vals[i]
                    idx = [i for i, b in enumerate(bars) if b["ts"] >= SINCE]
                    if not idx:
                        continue
                    t0 = bars[idx[0]]["ts"]
                    t1 = min(bars[idx[-1]]["ts"] + int(MAX_HOLD_H * 3600) + 7200, now)
                    per_name, path = pick_interval(cur, ex, s, t0, t1)
                    if not path:
                        continue
                    book[s] = (bars, path, [x[0] for x in path], per_name)
                if not book:
                    print("%s 源=%s 无数据" % (pool_name, ex))
                    continue

                def ret_at(sym, ts):
                    bars, p, tss, per = book[sym]
                    if tss[-1] < ts + int(MAX_HOLD_H * 3600):
                        return None
                    j = bisect.bisect_left(tss, ts)
                    k = bisect.bisect_left(tss, ts + int(MAX_HOLD_H * 3600))
                    exp = (p[k][0] - p[j][0]) / SECS[per]
                    if k - j < 8 or (exp > 0 and (k - j) / exp < 0.85):
                        return None
                    r = sim(p[j][3], p[j:k + 1])
                    return None if r < BOUND else r

                base_pool = []
                for s in book:
                    for b in book[s][0]:
                        if b["ts"] >= SINCE:
                            r = ret_at(s, b["ts"] + 4 * 3600)
                            if r is not None:
                                base_pool.append(r)
                base = st.mean(base_pool)
                print("\n" + "=" * 100)
                print("%s | 源=%s | 币 %d | 基准多头 %+.3f%%/笔(n=%d)"
                      % (pool_name, ex, len(book), base, len(base_pool)))
                print("  %-34s %4s %9s %9s %8s %6s %6s" %
                      ("配置(dip,EMA,间隔h,破前高)", "n", "均/笔%", "超额%", "p_逐笔", "p_按币", "集中"))
                grid_pos = grid_tot = 0
                for dip_lb in (2, 3, 4, 6):
                    for per in (20, 50, 100):
                        for gap_h in (4, 6, 12):
                            for need_high in (True, False):
                                col = "ema%d" % per
                                tr = []
                                for s, (bars, p, tss, pn) in book.items():
                                    last = -1e18
                                    for i, b in enumerate(bars):
                                        if b["ts"] < SINCE or i < max(per, 25):
                                            continue
                                        if b["ts"] - last < gap_h * 3600:
                                            continue
                                        if not sig_dip(bars, i, col, dip_lb, need_high):
                                            continue
                                        r = ret_at(s, b["ts"] + 4 * 3600)
                                        if r is None:
                                            continue
                                        last = b["ts"]
                                        tr.append((s, r))
                                if len(tr) < 8:
                                    continue
                                rets = [r for _, r in tr]
                                n, obs = len(rets), sum(rets)
                                cnt = sum(1 for _ in range(600) if sum(random.sample(base_pool, n)) >= obs)
                                p1 = (cnt + 1) / 601.0
                                # 按币聚类自助：以币为单位有放回重采样
                                by = {}
                                for s, r in tr:
                                    by.setdefault(s, []).append(r)
                                keys = list(by)
                                c2 = 0
                                for _ in range(600):
                                    pick = [random.choice(keys) for _ in range(len(keys))]
                                    tot = sum(sum(by[k]) for k in pick) / max(1, len(pick)) * len(keys)
                                    if tot >= obs:
                                        c2 += 1
                                p2 = (c2 + 1) / 601.0
                                agg = {s: sum(v) for s, v in by.items()}
                                topsym, topv = max(agg.items(), key=lambda kv: abs(kv[1]))
                                conc = abs(topv) / abs(obs) * 100 if obs else 0
                                grid_tot += 1
                                if st.mean(rets) - base > 0:
                                    grid_pos += 1
                                flag = "*" if (st.mean(rets) - base > 0 and p2 < 0.10 and conc < 50) else " "
                                print("  %-34s %4d %+9.2f %+9.3f %8.3f %6.3f %5.0f%%%s"
                                      % ("dip%d EMA%d gap%d 破前高=%s" % (dip_lb, per, gap_h, need_high),
                                         n, st.mean(rets), st.mean(rets) - base, p1, p2, conc, flag))
                print("  → 网格正超额格数 %d/%d（%.0f%%）" % (grid_pos, grid_tot, 100.0 * grid_pos / max(1, grid_tot)))
                # 频率
                col = "ema50"
                tot_sig = 0
                for s, (bars, p, tss, pn) in book.items():
                    last = -1e18
                    for i, b in enumerate(bars):
                        if b["ts"] < SINCE or i < 50 or b["ts"] - last < 6 * 3600:
                            continue
                        if sig_dip(bars, i, col, 4, True):
                            last = b["ts"]
                            tot_sig += 1
                days = (now - SINCE) / 86400.0
                print("  频率（dip4/EMA50/gap6/破前高）：%d 个信号 / %d 币 / %.1f 天 = %.2f 信号/币/天"
                      % (tot_sig, len(book), days, tot_sig / max(1, len(book)) / days))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
