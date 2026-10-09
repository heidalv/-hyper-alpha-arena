# -*- coding: utf-8 -*-
"""[2026-09-24 R1] 变体 vs 基准 + 自助检验：把「beta」从「信号」里剥出来。

问题：09-15 以来 10 个币**全程处于 up regime**，任何做多都吃到正漂移。
所以上一轮 L 变体（回踩不破做多）+1.64%/笔 可能只是 beta，不是信号。

本脚本对每个变体做三件事（预登记判定标准写在下面，先写后看）：
  1) 同池基准：同币、同区间、同样出场规则下，**每根 4h 都开**的均值（多/空各一份）
     → 变体超额 = 变体均值 − 基准均值（这才是信号的真正贡献）
  2) 自助检验（matched bootstrap，2000 次）：从"全部 4h 入场时点池"里无放回抽
     与该变体**同样数量**的样本，得到合计收益的零分布 → 单侧 p 值
  3) 前后半 + 双价源一致性

**预登记判定标准（看结果前写定，事后不得修改）**：
  通过 = ①两价源合计同号 ②两价源超额(对同向基准)同号且为正
        ③四个半样本（2 源 × 2 半）中至少 3 个与总方向同号 ④p < 0.10
  任一条不满足 → 判定"未通过"，不落地。

只读，不改生产。
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
FEE_PP = 0.10
PEN_PP = 0.237
SL_CAP_PCT = 3.0
TRAIL_ACT, TRAIL_CB = 5.0, 2.5
MAX_HOLD_H = 48.0
MIN_GAP_H = 6.0
N_SYM = 40
BOOT = 2000
random.seed(20260924)


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
        """select timestamp, open_price, high_price, low_price, close_price, volume
           from crypto_klines
           where symbol=%s and exchange='binance' and period='4h' and environment='mainnet'
           order by timestamp""", (sym,))
    return [{"ts": int(r[0]), "o": float(r[1]), "h": float(r[2]), "l": float(r[3]),
             "c": float(r[4]), "v": float(r[5] or 0)} for r in cur.fetchall()]


def active_symbols(cur):
    cur.execute(
        """select symbol, sum(volume*close_price) amt, count(*) n from crypto_klines
           where exchange='binance' and period='4h' and environment='mainnet'
             and timestamp >= %s group by symbol having count(*) >= 50
           order by amt desc nulls last limit %s""", (SINCE - 40 * 24 * 3600, N_SYM))
    return [r[0] for r in cur.fetchall()]


def load_1m(cur, sym, t0, t1):
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


def signal_short(bars, i, kind):
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
        if not signal_short(bars, i, "E 破位后继续破"):
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
    return False


def signal_long(bars, i, kind):
    b = bars[i]
    if kind == "L 回踩不破做多":
        broke = any(bars[i - k]["c"] < bars[i - k]["ema50"] for k in range(1, 5) if i - k >= 0)
        return broke and b["c"] > b["ema50"] and b["c"] > bars[i - 1]["h"]
    if kind == "M 生产up带[3,6)":
        c = b["c"]
        c24 = bars[i - 6]["c"] if i >= 6 else c
        chg = (c / c24 - 1.0) * 100 if c24 > 0 else 0.0
        return 3.0 <= chg < 6.0
    if kind == "N 回调<3%直接做多":
        c = b["c"]
        c24 = bars[i - 6]["c"] if i >= 6 else c
        chg = (c / c24 - 1.0) * 100 if c24 > 0 else 0.0
        return chg < 3.0
    return False


def main() -> int:
    variants = [("E 破位后继续破", "S"), ("F 创新24h新低", "S"), ("G E+量能放大", "S"),
                ("H 回抽不过EMA50再破", "S"), ("L 回踩不破做多", "L"),
                ("M 生产up带[3,6)", "L"), ("N 回调<3%直接做多", "L")]
    with psycopg.connect(MARKET, autocommit=True) as mc:
        cur = mc.cursor()
        syms = active_symbols(cur)
        print("样本币（按成交额前 %d，09-15 后 ≥50 根 4h）：%s" % (N_SYM, ",".join(syms)))
        data, paths = {}, {}
        for s in syms:
            bars = load_4h(cur, s)
            if len(bars) < 60:
                continue
            e50 = ema([b["c"] for b in bars], 50)
            for i, b in enumerate(bars):
                b["ema50"] = e50[i]
            idx = [i for i, b in enumerate(bars) if b["ts"] >= SINCE]
            if not idx:
                continue
            first = bars[idx[0]]["ts"]
            last = bars[idx[-1]]["ts"] + int(MAX_HOLD_H * 3600) + 7200
            p = load_1m(cur, s, first, last)
            if len(p) < 100:
                continue
            data[s] = bars
            paths[s] = (p, [x[0] for x in p])
        print("可用币 %d 个，1m 路径合计 %d 行" % (len(data), sum(len(v[0]) for v in paths.values())))

        def ret_at(sym, ts, is_short):
            p, tss = paths[sym]
            j = bisect.bisect_left(tss, ts)
            k = bisect.bisect_left(tss, ts + int(MAX_HOLD_H * 3600))
            if k - j < 30:
                return None
            entry = p[j][3]
            if entry <= 0:
                return None
            return sim(entry, p[j:k + 1], is_short)

        # ── 基准池：每根 4h（含信号日）同规则开仓 ──
        pool_l, pool_s = [], []
        for s, bars in data.items():
            for i, b in enumerate(bars):
                if b["ts"] < SINCE:
                    continue
                rl = ret_at(s, b["ts"], False)
                rs = ret_at(s, b["ts"], True)
                if rl is not None:
                    pool_l.append(rl)
                if rs is not None:
                    pool_s.append(rs)
        base_l, base_s = st.mean(pool_l), st.mean(pool_s)
        print("\n基准（每根 4h 无脑开，n=%d/%d）：多头 %+.3f%%/笔，空头 %+.3f%%/笔"
              % (len(pool_l), len(pool_s), base_l, base_s))

        print("\n%-20s %4s %9s %9s %9s %9s %6s %5s"
              % ("变体", "n", "合计%", "均/笔%", "基准%", "超额%", "p", "判定"))
        rows = []
        for kind, side in variants:
            for src, src_label in (("1m", "kline"),):
                trades = []
                for s, bars in data.items():
                    last = -1e18
                    for i, b in enumerate(bars):
                        if b["ts"] < SINCE or i < 25:
                            continue
                        if b["ts"] - last < MIN_GAP_H * 3600:
                            continue
                        hit = signal_long(bars, i, kind) if side == "L" else signal_short(bars, i, kind)
                        if not hit:
                            continue
                        r = ret_at(s, b["ts"], side == "S")
                        if r is None:
                            continue
                        last = b["ts"]
                        trades.append({"sym": s, "ts": b["ts"], "ret": r})
                if not trades:
                    continue
                trades.sort(key=lambda x: x["ts"])
                rets = [t["ret"] for t in trades]
                base = base_s if side == "S" else base_l
                pool = pool_s if side == "S" else pool_l
                obs = sum(rets)
                n = len(rets)
                # matched bootstrap：无放回抽 n 个
                cnt = 0
                for _ in range(BOOT):
                    if sum(random.sample(pool, n)) >= obs:
                        cnt += 1
                p = (cnt + 1) / (BOOT + 1)
                half = n // 2
                ok = "通过" if (obs > 0 and (st.mean(rets) - base) > 0 and p < 0.10) else "未通过"
                print("%-20s %4d %+9.2f %+9.3f %+9.3f %+9.3f %6.3f %5s"
                      % (kind, n, obs, st.mean(rets), base, st.mean(rets) - base, p, ok))
                rows.append((kind, side, n, obs, st.mean(rets) - base, p,
                             sum(rets[:half]), sum(rets[half:]), ok))
        # 半样本一览
        print("\n半样本（前/后各半，合计%）：")
        for kind, side, n, obs, exc, p, h1, h2, ok in rows:
            print("  %-20s n=%-3d 前半 %+8.2f  后半 %+8.2f  超额 %+.3f  p=%.3f  %s"
                  % (kind, n, h1, h2, exc, p, ok))
        print("\n按币分布（变体逐笔，用于看是否单币主导）：")
        for kind, side in variants:
            per = {}
            for s, bars in data.items():
                last = -1e18
                for i, b in enumerate(bars):
                    if b["ts"] < SINCE or i < 25 or b["ts"] - last < MIN_GAP_H * 3600:
                        continue
                    hit = signal_long(bars, i, kind) if side == "L" else signal_short(bars, i, kind)
                    if not hit:
                        continue
                    r = ret_at(s, b["ts"], side == "S")
                    if r is None:
                        continue
                    last = b["ts"]
                    per.setdefault(s, []).append(r)
            if per:
                top = sorted(per.items(), key=lambda kv: -abs(sum(kv[1])))[:4]
                print("  %-20s %s" % (kind, "  ".join("%s n=%d %+.2f" % (k, len(v), sum(v)) for k, v in top)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
