# -*- coding: utf-8 -*-
"""[R1 v3] 变体验证（修正版：全覆盖周期 + 覆盖闸 + 越界断言 + 双源 + 事前池）。

修正历史（自查记录，保留以便复核）：
  v1 错：样本池按「窗口内成交额」挑（事后选赢家）；单源；无越界断言。
  v2 诊断：发现 1m 表极度稀疏（ACE 206h 仅 420 根 ≈3%），未触发的止损被跳过，
          空头出现 −22.78% 这种模型上界（−2.76%）之外的数。
  v3 修正：路径改用**覆盖率≥90% 的最细周期**（binance 5m=97%、okx 自动降级），
          每笔再做 ≥85% 覆盖闸；越界即丢弃并计数；双源；池子事前可得。

预登记判定（看结果前写定，事后不得改）：
  通过 = ①双源合计同号 ②双源超额(对同向基准)同号且为正
        ③四半样本(2源×2半)≥3 与总方向同号 ④p<0.10 ⑤单币集中度<50%
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
BOUND = -(SL_CAP + PEN_PP + FEE_PP + 0.3)
BOOT = 2000
SECS = {"5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400}
random.seed(20260924)
BAD_DATA: list = []


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
    if kind.startswith("M") or kind.startswith("N") or kind.startswith("P") or kind.startswith("Q"):
        c24 = bars[i - 6]["c"] if i >= 6 else b["c"]
        chg = (b["c"] / c24 - 1.0) * 100 if c24 > 0 else 0.0
        if kind.startswith("M"):
            return 3.0 <= chg < 6.0
        if kind.startswith("N"):
            return chg < 3.0
        if kind.startswith("P"):
            return chg < 3.0 and b["c"] > b["ema50"]
        return chg < 3.0 and b["c"] > b["ema50"] and b["c"] > p1["h"]
    return False


VARIANTS = [("E 破位后继续破", "S"), ("F 创新24h新低", "S"), ("G E+量能放大", "S"),
            ("H 回抽不过EMA50再破", "S"), ("L 回踩不破做多", "L"), ("M 生产up带[3,6)", "L"),
            ("N 回调<3%做多", "L"), ("P 回调<3%且站上EMA50", "L"), ("Q 回调<3%+收复前高", "L")]


def pick_interval(cur, ex, sym, t0, t1):
    """返回该 (所,币) 覆盖率 ≥90% 的最细周期名与数据。"""
    for per in ("5m", "15m", "30m", "1h", "4h"):
        exp = max(1.0, (t1 - t0) / SECS[per])
        cur.execute(
            """select timestamp, high_price, low_price, close_price from crypto_klines
               where symbol=%s and exchange=%s and period=%s and environment='mainnet'
                 and timestamp between %s and %s order by timestamp""", (sym, ex, per, t0, t1))
        rows = [(int(r[0]), float(r[1]), float(r[2]), float(r[3])) for r in cur.fetchall()]
        cov = len(rows) / exp
        if cov >= 0.90:
            return per, rows, cov
    return None, [], 0.0


def run_source(cur, ex, syms, pool_name):
    """返回 {sym: (bars4h, path, tss, per, cov)}。"""
    out = {}
    for s in syms:
        cur.execute(
            """select timestamp, open_price, high_price, low_price, close_price, volume
               from crypto_klines where symbol=%s and exchange='binance' and period='4h'
                 and environment='mainnet' order by timestamp""", (s,))
        bars = [{"ts": int(r[0]), "o": float(r[1]), "h": float(r[2]), "l": float(r[3]),
                 "c": float(r[4]), "v": float(r[5] or 0)} for r in cur.fetchall()]
        if len(bars) < 60:
            continue
        # [v3.1] 只留"仍在交易"的币（最后一根 4h 距今 ≤6h），排除已停更的僵尸标的。
        if int(dt.datetime.now(CST).timestamp()) - bars[-1]["ts"] > 6 * 3600:
            continue
        e50 = ema([b["c"] for b in bars], 50)
        for i, b in enumerate(bars):
            b["ema50"] = e50[i]
        idx = [i for i, b in enumerate(bars) if b["ts"] >= SINCE]
        if not idx:
            continue
        # [v3.1 修复] t1 不能超过"现在"，否则覆盖率是拿未来 bar 当分母算的，
        # 会把所有活跃币判为覆盖不足、只留下早已停更的僵尸币（方向完全反了）。
        t0 = bars[idx[0]]["ts"]
        t1 = min(bars[idx[-1]]["ts"] + int(MAX_HOLD_H * 3600) + 7200, int(dt.datetime.now(CST).timestamp()))
        if t1 <= t0:
            continue
        per, path, cov = pick_interval(cur, ex, s, t0, t1)
        if not path:
            continue
        out[s] = (bars, path, [x[0] for x in path], per, cov)
    return out


def main() -> int:
    with psycopg.connect(MARKET, autocommit=True) as mc:
        cur = mc.cursor()
        cur.execute(
            """select symbol from crypto_klines where exchange='binance' and period='4h'
                 and environment='mainnet' and timestamp >= %s and timestamp < %s
               group by symbol having count(*)>=50
               order by sum(volume*close_price) desc nulls last limit 25""", (PRE0, SINCE))
        prev25 = [r[0] for r in cur.fetchall()]
        pools = [("A 生产10主流（无选择偏差）", MAJORS), ("B 窗口前40天成交额前25（事前可得）", prev25)]
        print("池 B：%s" % ",".join(prev25))
        for pool_name, syms in pools:
            for ex in ("binance", "okx"):
                book = run_source(cur, ex, syms, pool_name)
                if not book:
                    print("\n%s 源=%s 无数据" % (pool_name, ex))
                    continue
                pers = {}
                for s, v in book.items():
                    pers[v[3]] = pers.get(v[3], 0) + 1
                print("\n" + "=" * 106)
                print("%s | 源=%s | 可用币 %d %s" % (pool_name, ex, len(book), dict(sorted(pers.items()))))
                for per_name in sorted(pers):
                    who = [s for s in book if book[s][3] == per_name]
                    print("    %-4s (%d): %s" % (per_name, len(who), ",".join(sorted(who))))

                def ret_at(sym, ts, is_short):
                    bars, p, tss, per, cov = book[sym]
                    # [v3.1 修复] 必须有完整的 48h 前向窗口，否则末尾样本会"持有不足 48h"
                    # 就按最长持有平仓，系统性低估亏损、抬高均值。
                    if tss[-1] < ts + int(MAX_HOLD_H * 3600):
                        return None
                    j = bisect.bisect_left(tss, ts)
                    k = bisect.bisect_left(tss, ts + int(MAX_HOLD_H * 3600))
                    if k - j < 8:
                        return None
                    exp = (p[k][0] - p[j][0]) / SECS[per]
                    if exp > 0 and (k - j) / exp < 0.85:
                        BAD_DATA.append((sym, "low_cov", round((k - j) / exp, 2)))
                        return None
                    entry = p[j][3]
                    if entry <= 0:
                        return None
                    r = sim(entry, p[j:k + 1], is_short)
                    if r < BOUND:
                        BAD_DATA.append((sym, "oob", round(r, 2)))
                        return None
                    return r

                pl, ps = [], []
                for s, (bars, p, tss, per, cov) in book.items():
                    for b in bars:
                        if b["ts"] < SINCE:
                            continue
                        rl, rs = ret_at(s, b["ts"] + 4 * 3600, False), ret_at(s, b["ts"] + 4 * 3600, True)
                        if rl is not None:
                            pl.append(rl)
                        if rs is not None:
                            ps.append(rs)
                bl, bs = st.mean(pl), st.mean(ps)
                print("  基准（每根4h无脑开）多头 %+.3f%%/笔(n=%d)  空头 %+.3f%%/笔(n=%d)  被丢弃样本 %d"
                      % (bl, len(pl), bs, len(ps), len(BAD_DATA)))
                print("  %-24s %4s %9s %9s %9s %7s %5s %5s" %
                      ("变体", "n", "合计%", "均/笔%", "超额%", "p", "前半", "后半"))
                for kind, side in VARIANTS:
                    tr = []
                    for s in book:
                        bars = book[s][0]
                        last = -1e18
                        for i, b in enumerate(bars):
                            if b["ts"] < SINCE or i < 25 or b["ts"] - last < MIN_GAP_H * 3600:
                                continue
                            if not sig(bars, i, kind):
                                continue
                            r = ret_at(s, b["ts"] + 4 * 3600, side == "S")
                            if r is None:
                                continue
                            last = b["ts"]
                            tr.append({"sym": s, "ts": b["ts"], "ret": r})
                    if not tr:
                        print("  %-24s %4d 无信号" % (kind, 0))
                        continue
                    tr.sort(key=lambda x: x["ts"])
                    rets = [t["ret"] for t in tr]
                    base = bs if side == "S" else bl
                    pool = ps if side == "S" else pl
                    n, obs = len(rets), sum(rets)
                    if n > len(pool):
                        n_eff = len(pool)
                    else:
                        n_eff = n
                    cnt = sum(1 for _ in range(BOOT) if sum(random.sample(pool, n_eff)) >= obs)
                    p = (cnt + 1) / (BOOT + 1)
                    half = n // 2
                    print("  %-24s %4d %+9.2f %+9.3f %+9.3f %7.3f %+7.1f %+7.1f"
                          % (kind, n, obs, st.mean(rets), st.mean(rets) - base, p,
                             sum(rets[:half]), sum(rets[half:])))
        if BAD_DATA:
            agg = {}
            for s, why, v in BAD_DATA:
                agg[why] = agg.get(why, 0) + 1
            print("\n丢弃统计：%s（low_cov=路径覆盖不足, oob=越界）" % agg)
            print("  样例：%s" % BAD_DATA[:8])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
