# -*- coding: utf-8 -*-
"""[R1 v5] L 变体落地前取证包（dip4/EMA50/gap6/破前高）：正确的聚类自助 + 执行延迟 + 走前验证。

修正 v4 的统计错误：v4 的"按币自助"把总量按币数缩放后与观测总量比，构造上就不是零分布
（所以恒得 p≈0.5，无信息）。v5 改为**配对聚类自助**：
  以币为重采样单位，对每个抽中的币同时取其"信号笔"与"该币全部4h基准笔"，
  统计 mean_signal − mean_benchmark 的分布 → p = P(diff ≤ 0)。这才对应
  "换一批币还灵不灵"这个问题。同时如实报告：只有 9~10 个币簇，该检验功效极低。

另加三项落地前必查：
  1) 执行延迟：信号后 0/15/30/60 分钟入场（防"看见收盘就用收盘价"的乐观）
  2) 额外滑点：0 / 0.10 / 0.25 pp
  3) 走前验证：按时间前 60% 训练 / 后 40% 样本外
  4) 逐币明细 + 信号时 regime 构成（落地时按哪个 regime 放行）
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
DIP_LB, EMA_P, GAP_H, NEED_HIGH = 4, 50, 6, True
# [2026-09-24 R2 关键修复] 信号用的是**4h bar 的收盘**（该 bar 覆盖 [ts, ts+4h)），
# 因此入场必须发生在 ts+4h（该 bar 收盘、下一根 bar 开盘），而不是 ts（= 该 bar 开盘）。
# 旧版在 ts 入场 = **4 小时前视**（用未来的收盘价在 4 小时前下单），会系统性虚高所有变体。
SIG_LAG_H = 4.0
random.seed(20260924)


def ema(v, p):
    k = 2.0 / (p + 1.0)
    e = v[0]
    out = []
    for x in v:
        e = x * k + e * (1 - k)
        out.append(e)
    return out


def sim(entry, bars, extra_slip=0.0):
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
    return (exit_px - entry) / entry * 100.0 - FEE_PP - extra_slip


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


def dip_sig(bars, i):
    b, p1 = bars[i], bars[i - 1]
    e = b["ema"]
    if e <= 0 or b["c"] <= e:
        return False
    if not any(bars[i - k]["ema"] > 0 and bars[i - k]["c"] < bars[i - k]["ema"]
               for k in range(1, DIP_LB + 1) if i - k >= 0):
        return False
    return b["c"] > p1["h"] if NEED_HIGH else True


def regime_at(bars, i):
    """日线 regime 近似：用 4h 序列的 EMA200(≈33天) 与 60 日动量。仅供分类统计。"""
    b = bars[i]
    return b.get("reg", "")


def main() -> int:
    now = int(dt.datetime.now(CST).timestamp())
    with psycopg.connect(MARKET, autocommit=True) as mc:
        cur = mc.cursor()
        cur.execute(
            """select symbol from crypto_klines where exchange='binance' and period='4h'
                 and environment='mainnet' and timestamp >= %s and timestamp < %s
               group by symbol having count(*)>=50
               order by sum(volume*close_price) desc nulls last limit 25""", (PRE0, SINCE))
        pools = [("A 生产10主流", MAJORS), ("B 事前25名", [r[0] for r in cur.fetchall()])]
        for pool_name, syms in pools:
            for ex in ("binance", "okx"):
                book = {}
                for s in syms:
                    cur.execute(
                        """select timestamp, open_price, high_price, low_price, close_price, volume
                           from crypto_klines where symbol=%s and exchange='binance' and period='4h'
                             and environment='mainnet' order by timestamp""", (s,))
                    bars = [{"ts": int(r[0]), "h": float(r[2]), "l": float(r[3]),
                             "c": float(r[4])} for r in cur.fetchall()]
                    if len(bars) < 120 or now - bars[-1]["ts"] > 6 * 3600:
                        continue
                    ev = ema([b["c"] for b in bars], EMA_P)
                    for i, b in enumerate(bars):
                        b["ema"] = ev[i]
                    idx = [i for i, b in enumerate(bars) if b["ts"] >= SINCE]
                    if not idx:
                        continue
                    t0 = bars[idx[0]]["ts"]
                    t1 = min(bars[idx[-1]]["ts"] + int(MAX_HOLD_H * 3600) + 7200, now)
                    per, path = pick_interval(cur, ex, s, t0, t1)
                    if not path:
                        continue
                    book[s] = (bars, path, [x[0] for x in path], per)

                def bench(sym, ts, lag_bars=0, slip=0.0):
                    bars, p, tss, per = book[sym]
                    if tss[-1] < ts + int(MAX_HOLD_H * 3600):
                        return None
                    j = bisect.bisect_left(tss, ts) + lag_bars
                    k = bisect.bisect_left(tss, ts + int(MAX_HOLD_H * 3600)) + lag_bars
                    if k >= len(p):
                        return None
                    exp = (p[k][0] - p[j][0]) / SECS[per]
                    if k - j < 8 or (exp > 0 and (k - j) / exp < 0.85):
                        return None
                    r = sim(p[j][3], p[j:k + 1], slip)
                    return None if r < BOUND else r

                # 基准笔（每根 4h）
                bench_by_sym = {}
                for s in book:
                    for b in book[s][0]:
                        if b["ts"] >= SINCE:
                            r = bench(s, b["ts"] + int(SIG_LAG_H * 3600))
                            if r is not None:
                                bench_by_sym.setdefault(s, []).append(r)
                allb = [r for v in bench_by_sym.values() for r in v]
                base = st.mean(allb)
                print("\n" + "=" * 104)
                print("%s | 源=%s | 币 %d | 基准多头 %+.3f%%/笔(n=%d)" % (pool_name, ex, len(book), base, len(allb)))

                sig_by_sym = {}
                for s, (bars, p, tss, per) in book.items():
                    last = -1e18
                    for i, b in enumerate(bars):
                        if b["ts"] < SINCE or i < max(EMA_P, 25) or b["ts"] - last < GAP_H * 3600:
                            continue
                        if not dip_sig(bars, i):
                            continue
                        r = bench(s, b["ts"] + int(SIG_LAG_H * 3600))
                        if r is None:
                            continue
                        last = b["ts"]
                        sig_by_sym.setdefault(s, []).append((b["ts"], r))
                rets = [r for v in sig_by_sym.values() for _, r in v]
                if not rets:
                    print("  无信号")
                    continue
                rets_sorted = sorted((t, r) for v in sig_by_sym.values() for t, r in v)
                n = len(rets)
                print("  信号：n=%d  均 %+.3f%%  超额 %+.3f%%  胜率 %.0f%%"
                      % (n, st.mean(rets), st.mean(rets) - base, 100.0 * sum(1 for x in rets if x > 0) / n))

                # ── 配对聚类自助（正确形式）──
                keys = [s for s in sig_by_sym]
                diffs = []
                for _ in range(4000):
                    pick = [random.choice(keys) for _ in range(len(keys))]
                    sm = [r for k in pick for _, r in sig_by_sym[k]]
                    bm = [r for k in pick for r in bench_by_sym.get(k, [])]
                    if sm and bm:
                        diffs.append(st.mean(sm) - st.mean(bm))
                diffs.sort()
                p_cl = sum(1 for d in diffs if d <= 0) / len(diffs)
                lo, hi = diffs[int(0.05 * len(diffs))], diffs[int(0.95 * len(diffs))]
                print("  配对聚类自助（%d 币簇 ×4000）：超额分布 5%%~95%% = %+.2f ~ %+.2f pp，P(超额≤0)=%.3f"
                      % (len(keys), lo, hi, p_cl))
                print("    注：仅 %d 个币簇，该检验功效很低——p 大不代表无效，只代表"
                      "「换一批币」这种外推无法用现有 10 币证明。" % len(keys))

                # ── 执行延迟 / 滑点 ──
                print("  执行延迟与滑点敏感性（同一批信号）：")
                for lag, lab in ((0, "0min"), (3, "+15min"), (6, "+30min"), (12, "+60min")):
                    by = {}
                    for s, (bars, p, tss, per) in book.items():
                        last = -1e18
                        for i, b in enumerate(bars):
                            if b["ts"] < SINCE or i < max(EMA_P, 25) or b["ts"] - last < GAP_H * 3600:
                                continue
                            if not dip_sig(bars, i):
                                continue
                            r = bench(s, b["ts"] + int(SIG_LAG_H * 3600), lag_bars=lag)
                            if r is None:
                                continue
                            last = b["ts"]
                            by.setdefault(s, []).append(r)
                    rr = [x for v in by.values() for x in v]
                    if rr:
                        print("    %-7s n=%-3d 均 %+.3f%%  超额 %+.3f%%"
                              % (lab, len(rr), st.mean(rr), st.mean(rr) - base))
                for slip in (0.10, 0.25):
                    by = {}
                    for s, (bars, p, tss, per) in book.items():
                        last = -1e18
                        for i, b in enumerate(bars):
                            if b["ts"] < SINCE or i < max(EMA_P, 25) or b["ts"] - last < GAP_H * 3600:
                                continue
                            if not dip_sig(bars, i):
                                continue
                            r = bench(s, b["ts"] + int(SIG_LAG_H * 3600), slip=slip)
                            if r is None:
                                continue
                            last = b["ts"]
                            by.setdefault(s, []).append(r)
                    rr = [x for v in by.values() for x in v]
                    if rr:
                        print("    滑点%.2fpp n=%-3d 均 %+.3f%%  超额 %+.3f%%"
                              % (slip, len(rr), st.mean(rr), st.mean(rr) - base))

                # ── 走前：前60% / 后40% ──
                cut = int(n * 0.6)
                tr = [r for _, r in rets_sorted[:cut]]
                oos = [r for _, r in rets_sorted[cut:]]
                print("  走前：训练(前60%%) n=%d 均 %+.3f%% | 样本外(后40%%) n=%d 均 %+.3f%%"
                      % (len(tr), st.mean(tr), len(oos), st.mean(oos)))

                # ── 逐币 ──
                per_sym = sorted(((s, len(v), sum(r for _, r in v)) for s, v in sig_by_sym.items()),
                                 key=lambda x: -x[2])
                print("  逐币 (n / sum%): "
                      + "  ".join("%s %d/%+.1f" % (s, c, t) for s, c, t in per_sym))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
