"""H11 · 最后一个杠杆：被动成交后，什么能预测"价格会回来"？

H10 的结果把所有其他假设都排除了：
  · 12 组执行配置（3 种平仓政策 × 4 档 HOLD）**全部亏损**
  · 完成往返确实赚到点差：ASTER +1.383bp / DOGE +1.259 / XRP +1.092 / SOL +0.942
  · 但完成率只有 6~18%，其余按 mid 强平，每笔亏 -0.10 ~ -0.90bp
  · 最好的配置（pinned_k1_h30s）也只 -0.041bp/次进场

⇒ 剩下的唯一杠杆是：**提高完成率**，即判断"建仓后价格会不会回到我的平仓价（入场价±点差）"。
   ASTER 的算术：完成率 18.4% → 若提到 100%，每笔进场从 -0.047bp 变为 +0.276bp。
   **"能否避免强平"这件事值 ~0.32bp/笔。**

H11 检验的候选预测因子（全部只用建仓时刻可得信息）：
  1. imb        = (B-A)/(B+A)          建仓时 top-of-book 失衡
  2. ofi        = Δ(B-A)/(B+A) 最近 1s/5s/30s 的 OFI（论文与 H1/H2 的主特征）
  3. osc        = 最近 3 个 5s 窗口的收益自协方差符号（论文 permutation 第一名：
                  "ret autocorr sum 30s w0"，reversal 需要**振荡**而非趋势）
  4. tint       = 最近 30s 平均成交间隔（论文："长间隔 ⇒ reversal 概率显著下降"）
  5. totb       = top-of-book 存续时长（论文："短存续 ⇒ reversal 概率高"）
  6. ret100     = 最近 100ms 的收益（论文："急剧下跌到新低点 ⇒ reversal 概率高"）
  7. bought_at  = 建仓侧相对失衡的关系（顺/逆失衡，H6/H7 已测）

标签（这是关键，与 H10 的口径严格对齐）：
  y = 1  若建仓后 HOLD 内，**平仓腿在钉定价位上成交**（= H10 的"完成"）
  y = 0  若超时未成交（= H10 的"强平"）
  并且同时记录完成时的收益与强平时的损失，用于算"该信号值多少钱"。

输出：research_l1/out/h11_exit_predictability.json
"""
from __future__ import annotations

import bisect
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env", override=False)

import numpy as np  # noqa: E402
import psycopg2  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "out"
OUT.mkdir(parents=True, exist_ok=True)

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
           "BNBUSDT", "ASTERUSDT", "ZECUSDT", "HYPEUSDT"]
HOURS = float(os.environ.get("H11_HOURS", "12"))
HOLD_MS = 30_000
GRID_MS = 1000
NOTIONAL = 150.0


def pg():
    url = os.environ["DATABASE_URL"]
    for d in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(d, "")
    head, _, _ = url.rpartition("/")
    cn = psycopg2.connect(head + "/alpha_market")
    cn.autocommit = True
    return cn


def stats(v):
    v = np.asarray([x for x in v if x is not None and np.isfinite(x)], dtype=np.float64)
    if v.size == 0:
        return None
    o = {"n": int(v.size), "mean": float(v.mean())}
    if v.size > 1:
        se = float(v.std(ddof=1)) / float(np.sqrt(v.size))
        o["t"] = (float(v.mean() / se) if se > 0 else None)
    return o


def run_symbol(sym, bts, bid, ask, bq, aq, tts, tpx, tqt, bm, hold_ms):
    mid = (bid + ask) / 2.0
    n = len(bts)

    def px_at(arr, ts):
        i = bisect.bisect_right(bts, ts) - 1
        return float(arr[i]) if i >= 0 else None

    def mid_at(ts):
        i = bisect.bisect_right(bts, ts) - 1
        return float(mid[i]) if i >= 0 else None

    # 成交索引
    bh, sh = defaultdict(list), defaultdict(list)
    for k in range(len(tts)):
        (bh if bm[k] else sh)[tpx[k]].append((int(tts[k]), float(tqt[k])))
    tidx = {}
    for tag, dd in (("bid", bh), ("ask", sh)):
        b = {}
        for px, arr in dd.items():
            arr.sort()
            b[round(px, 12)] = ([x[0] for x in arr], np.cumsum([x[1] for x in arr]))
        tidx[tag] = b

    def try_fill(side, limit, t0, t1):
        ts_arr, cum = tidx[side].get(round(limit, 12), (None, None))
        if ts_arr is None:
            return None
        k = bisect.bisect_right(ts_arr, t0)
        if k >= len(ts_arr) or ts_arr[k] > t1:
            return None
        return int(ts_arr[k])

    # 为特征预计算：每 1s 网格上的 imb / ofi / ret
    gts = np.arange(int(bts[0]), int(bts[-1]), GRID_MS, dtype=np.int64)
    gidx = np.clip(np.searchsorted(bts, gts, side="right") - 1, 0, len(bts) - 1)
    g_mid = mid[gidx]
    gq_b = bq[gidx]
    gq_a = aq[gidx]
    n_grid = len(gts)

    trades_out = []
    pick = 0
    gi = 0
    while gi < n_grid - 2:
        t = int(gts[gi])
        m0 = float(g_mid[gi])
        bp0, ap0 = float(bid[gidx[gi]]), float(ask[gidx[gi]])
        qb0, qa0 = float(gq_b[gi]), float(gq_a[gi])
        if not (np.isfinite(m0) and m0 > 0 and bp0 > 0 and ap0 > bp0):
            gi += 1
            continue
        tot = qb0 + qa0
        if tot <= 0:
            gi += 1
            continue
        imb = (qb0 - qa0) / tot

        # 特征
        def win_ret(back_s):
            j = gi - back_s
            if j < 0:
                return None
            return (m0 / float(g_mid[j]) - 1.0) * 1e4
        def win_imb_delta(back_s):
            j = gi - back_s
            if j < 0:
                return None
            tot_j = float(gq_b[j] + gq_a[j])
            if tot_j <= 0:
                return None
            return imb - (float(gq_b[j]) - float(gq_a[j])) / tot_j
        def win_autocov(back_windows=3, win_s=5):
            """最近 3 个 5s 窗口的收益自协方差（负值=振荡 ⇒ 论文说 reversal 概率高）"""
            rs = []
            for w in range(back_windows):
                j1 = gi - w * win_s
                j0 = gi - (w + 1) * win_s
                if j0 < 0:
                    return None
                rs.append(float(g_mid[j1]) / float(g_mid[j0]) - 1.0)
            if len(rs) < 3:
                return None
            return float(np.cov(rs[:-1], rs[1:])[0, 1]) * 1e8
        def win_trade_gap(back_s):
            lo = t - back_s * 1000
            k0 = bisect.bisect_left(tts, lo)
            k1 = bisect.bisect_right(tts, t)
            m = k1 - k0
            return (back_s * 1000.0 / m) if m > 1 else None

        feats = {
            "imb": imb,
            "ofi_1s": win_imb_delta(1),
            "ofi_5s": win_imb_delta(5),
            "ofi_30s": win_imb_delta(30),
            "ret_1s": win_ret(1),
            "ret_5s": win_ret(5),
            "ret_30s": win_ret(30),
            "ret_100ms": (m0 / float(g_mid[max(0, gi - 1)]) - 1.0) * 1e4 if gi >= 1 else None,
            "autocov_5s": win_autocov(),
            "trade_gap_30s": win_trade_gap(30),
        }

        # 交替选侧（确定性、可复现）
        side = "bid" if (pick % 2 == 0) else "ask"
        pick += 1
        limit = bp0 if side == "bid" else ap0
        shown = qb0 if side == "bid" else qa0
        ts_arr, cum = tidx[side].get(round(limit, 12), (None, None))
        if ts_arr is None:
            gi += 1
            continue
        k = bisect.bisect_right(ts_arr, t)
        j = bisect.bisect_left(ts_arr, t - 10_000)
        recent = (float(cum[k - 1]) - (float(cum[j - 1]) if j > 0 else 0.0)) if k > 0 else 0.0
        q_ahead = min(shown, recent) if recent > 0 else shown
        if k >= len(ts_arr):
            gi += 1
            continue
        base = float(cum[k - 1]) if k > 0 else 0.0
        kk = bisect.bisect_right(cum, base + q_ahead, lo=k)
        if kk >= len(ts_arr):
            gi += 1
            continue
        tf = int(ts_arr[kk])
        entry_px = limit
        sp_abs = ap0 - bp0
        exit_limit = entry_px + sp_abs if side == "bid" else entry_px - sp_abs
        exit_side = "ask" if side == "bid" else "bid"
        # 平仓腿成交？
        ef = try_fill(exit_side, exit_limit, tf, tf + hold_ms)
        completed = ef is not None
        if completed:
            bp = ((exit_limit - entry_px) if side == "bid" else (entry_px - exit_limit)) / entry_px * 1e4
        else:
            mm = mid_at(tf + hold_ms)
            if mm is None:
                gi += 1
                continue
            bp = ((mm - entry_px) if side == "bid" else (entry_px - mm)) / entry_px * 1e4

        trades_out.append({**feats, "side": side, "completed": completed, "bp": bp,
                           "spread_bp": sp_abs / m0 * 1e4,
                           "hold_ms": (ef - tf) if completed else hold_ms})
        gi += 1

    return trades_out


def main():
    cn = pg()
    cur = cn.cursor()
    cur.execute("select min(event_ts_ms), max(event_ts_ms) from asterdex_book_ticker")
    lo, hi = cur.fetchone()
    cn.close()
    T1 = int(hi)
    T0 = T1 - int(HOURS * 3600 * 1000)

    rep = {"generated_at": datetime.now(timezone.utc).isoformat(),
           "window_ms": [T0, T1], "hours": HOURS, "hold_ms": HOLD_MS,
           "notional_usd": NOTIONAL, "label": "completed = 平仓腿在 入场价±入场点差 于 HOLD 内成交",
           "symbols": {}}

    for sym in SYMBOLS:
        cn = pg()
        cur = cn.cursor()
        cur.execute("select event_ts_ms,bid_px,bid_qty,ask_px,ask_qty from asterdex_book_ticker "
                    "where symbol=%s and event_ts_ms between %s and %s order by event_ts_ms",
                    (sym, T0, T1))
        book = cur.fetchall()
        cur.execute("select event_ts_ms,price,qty,is_buyer_maker from asterdex_trades "
                    "where symbol=%s and event_ts_ms between %s and %s order by event_ts_ms",
                    (sym, T0, T1))
        tr = cur.fetchall()
        cn.close()
        if not book or not tr:
            continue
        bts = np.array([r[0] for r in book], dtype=np.int64)
        bid = np.array([r[1] for r in book], dtype=np.float64)
        bq = np.array([r[2] for r in book], dtype=np.float64)
        ask = np.array([r[3] for r in book], dtype=np.float64)
        aq = np.array([r[4] for r in book], dtype=np.float64)
        tts = np.array([r[0] for r in tr], dtype=np.int64)
        tpx = np.array([r[1] for r in tr], dtype=np.float64)
        tqt = np.array([r[2] for r in tr], dtype=np.float64)
        bm = np.array([bool(r[3]) for r in tr])

        rows = run_symbol(sym, bts, bid, ask, bq, aq, tts, tpx, tqt, bm, HOLD_MS)
        if not rows:
            rep["symbols"][sym] = {"error": "no rows"}
            continue
        comp = [r for r in rows if r["completed"]]
        forc = [r for r in rows if not r["completed"]]
        out = {
            "symbol": sym, "n": len(rows), "n_completed": len(comp), "n_forced": len(forc),
            "completion_rate": len(comp) / len(rows),
            "bp_completed": stats([r["bp"] for r in comp]),
            "bp_forced": stats([r["bp"] for r in forc]),
            "bp_all": stats([r["bp"] for r in rows]),
            "bp_per_decision": float(np.mean([r["bp"] for r in rows])),
            "spread_bp": stats([r["spread_bp"] for r in rows]),
            "feature_ic": {},
        }
        # 每个特征与 "completed" 的 IC（点二列相关）以及与 bp 的相关
        for f in ("imb", "ofi_1s", "ofi_5s", "ofi_30s", "ret_1s", "ret_5s", "ret_30s",
                  "ret_100ms", "autocov_5s", "trade_gap_30s"):
            xs, ys, bs = [], [], []
            for r in rows:
                v = r.get(f)
                if v is None or not np.isfinite(v):
                    continue
                xs.append(v); ys.append(1.0 if r["completed"] else 0.0); bs.append(r["bp"])
            if len(xs) < 100:
                out["feature_ic"][f] = None
                continue
            x = np.asarray(xs); y = np.asarray(ys); b = np.asarray(bs)
            if x.std() == 0:
                out["feature_ic"][f] = None
                continue
            ic_comp = float(np.corrcoef(x, y)[0, 1])
            ic_bp = float(np.corrcoef(x, b)[0, 1])
            # 分五档看完成率与 bp（判断单调性）
            qs = np.quantile(x, [0.2, 0.4, 0.6, 0.8])
            buckets = np.digitize(x, qs)
            rate_by_bucket, bp_by_bucket = [], []
            for bi in range(5):
                m = buckets == bi
                if m.sum() == 0:
                    rate_by_bucket.append(None); bp_by_bucket.append(None)
                else:
                    rate_by_bucket.append(float(y[m].mean()))
                    bp_by_bucket.append(float(b[m].mean()))
            out["feature_ic"][f] = {
                "n": len(xs), "ic_completion": ic_comp, "ic_bp": ic_bp,
                "completion_by_quintile": rate_by_bucket,
                "bp_by_quintile": bp_by_bucket,
            }
        rep["symbols"][sym] = out
        print(f"[{sym:<10}] n={len(rows):>5} 完成率={out['completion_rate']:.3f} "
              f"完成={out['bp_completed']['mean'] if out['bp_completed'] else float('nan'):+.3f}bp "
              f"强平={out['bp_forced']['mean'] if out['bp_forced'] else float('nan'):+.3f}bp "
              f"每决策={out['bp_per_decision']:+.3f}bp")

    # 池化：每个特征的 ic_completion 跨币平均
    print("\n=== 特征对「完成率」的预测力（跨币平均 IC）===")
    print(f"{'feature':<16}{'mean IC(完成)':>15}{'mean IC(bp)':>14}{'#币':>6}")
    pooled = {}
    for f in ("imb", "ofi_1s", "ofi_5s", "ofi_30s", "ret_1s", "ret_5s", "ret_30s",
              "ret_100ms", "autocov_5s", "trade_gap_30s"):
        ics, icb = [], []
        for s, o in rep["symbols"].items():
            fi = (o.get("feature_ic") or {}).get(f)
            if fi:
                ics.append(fi["ic_completion"]); icb.append(fi["ic_bp"])
        if ics:
            pooled[f] = {"mean_ic_completion": float(np.mean(ics)),
                         "mean_ic_bp": float(np.mean(icb)), "n_sym": len(ics),
                         "n_positive_completion": int(sum(1 for x in ics if x > 0))}
            print(f"{f:<16}{np.mean(ics):>15.4f}{np.mean(icb):>14.4f}{len(ics):>6}"
                  f"   (完成率IC>0: {sum(1 for x in ics if x>0)}/{len(ics)})")
    rep["pooled_feature_ic"] = pooled

    p = OUT / "h11_exit_predictability.json"
    p.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwrote {p}")


if __name__ == "__main__":
    main()
