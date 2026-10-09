"""H6 · 逆失衡挂单检验（论文机制的直接落地检验）

H5 的教训（必须先读）：
  H5 在 9 个币上测「被动挂单成交后 markout」，全部呈现"卖优买劣"的对称反号模式
  （BTC 卖 +0.179 / 买 -0.126；ASTER 卖 +0.533 / 买 -0.391），且成交率 bid 恒高于 ask。
  这是窗口内**市场整体上行**造成的方向性 beta，不是选择性成交。
  ⇒ 绝不能把「某侧 markout 为正」当 edge；必须有侧向感知的市场中性信号。

H6 回答的问题：
  论文 §1.1.3 / §6 称「想靠失衡赚钱，必须先逆着失衡挂单，然后失衡再转向」，
  且「挂在短队列（逆失衡侧）才有高成交概率」。
  那么：**按失衡条件选择挂单侧，能否把每笔成交的 markout 从负拉到正？**

关键测量口径（与 H5 不同，这里修掉了一个偏置）：
  markout 以**成交时刻 tf 的 mid** 为基准（论文 Table 1 口径），而不是挂单时刻。
  原因：以挂单时刻为基准会把「限价相对 mid 的点差优势」(半价差 ~0.74bp) 混进来，
  那是入场价格优势、不是成交后价格漂移，两者必须分开看。

  同时给出**对照基准**：同一符号、同一侧、同一 horizon 上的无条件成交 markout，
  用于判断某个失衡桶是否真的更好，而不是被市场漂移主导。

输出：research_l1/out/h6_imbalance_conditional.json
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
HOURS = float(os.environ.get("H6_HOURS", "12"))
GRID_MS = int(os.environ.get("H6_GRID_MS", "1000"))
HOLD_MS = 10_000
HORIZONS_MS = [1000, 5000, 30000]
IMB_EDGES = [-1.0, -0.6, -0.3, -0.1, 0.1, 0.3, 0.6, 1.0]


def pg():
    url = os.environ["DATABASE_URL"]
    for d in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(d, "")
    head, _, _ = url.rpartition("/")
    cn = psycopg2.connect(head + "/alpha_market")
    cn.autocommit = True
    return cn


def load_symbol(sym, t0, t1):
    cn = pg()
    try:
        cur = cn.cursor()
        cur.execute(
            "select event_ts_ms, bid_px, bid_qty, ask_px, ask_qty from asterdex_book_ticker "
            "where symbol=%s and event_ts_ms between %s and %s order by event_ts_ms",
            (sym, t0, t1),
        )
        book = cur.fetchall()
        cur.execute(
            "select event_ts_ms, price, qty, is_buyer_maker from asterdex_trades "
            "where symbol=%s and event_ts_ms between %s and %s order by event_ts_ms",
            (sym, t0, t1),
        )
        trades = cur.fetchall()
        return book, trades
    finally:
        cn.close()


def stats(vals):
    v = np.asarray([x for x in vals if x is not None and np.isfinite(x)], dtype=np.float64)
    if v.size == 0:
        return None
    o = {"n": int(v.size), "mean": float(v.mean()), "median": float(np.median(v))}
    if v.size > 1:
        sd = float(v.std(ddof=1))
        se = sd / float(np.sqrt(v.size))
        o.update({"std": sd, "t": (float(v.mean() / se) if se > 0 else None)})
    return o


def analyse(sym, book, trades):
    bts = np.array([r[0] for r in book], dtype=np.int64)
    bid = np.array([r[1] for r in book], dtype=np.float64)
    bq = np.array([r[2] for r in book], dtype=np.float64)
    ask = np.array([r[3] for r in book], dtype=np.float64)
    aq = np.array([r[4] for r in book], dtype=np.float64)
    mid = (bid + ask) / 2.0

    tts = np.array([r[0] for r in trades], dtype=np.int64)
    tpx = np.array([r[1] for r in trades], dtype=np.float64)
    tqt = np.array([r[2] for r in trades], dtype=np.float64)
    bmaker = np.array([bool(r[3]) for r in trades])

    buy_hits, sell_hits = defaultdict(list), defaultdict(list)
    for k in range(len(tts)):
        (buy_hits if bmaker[k] else sell_hits)[tpx[k]].append((int(tts[k]), float(tqt[k])))
    tidx = {}
    for tag, dd in (("bid", buy_hits), ("ask", sell_hits)):
        built = {}
        for px, arr in dd.items():
            arr.sort()
            built[round(px, 12)] = ([a[0] for a in arr], np.cumsum([a[1] for a in arr]))
        tidx[tag] = built

    grid_ts = np.arange(int(bts[0]), int(bts[-1]), GRID_MS, dtype=np.int64)
    gidx = np.clip(np.searchsorted(bts, grid_ts, side="right") - 1, 0, len(bts) - 1)

    nB = len(IMB_EDGES) - 1
    # bucket[side][e] = {"all": n, "fill": n, "mk": {h: [vals from tf]}}
    bucket = {s: {e: {"all": 0, "fill": 0, "mk": {h: [] for h in HORIZONS_MS},
                     "mk_t0": {h: [] for h in HORIZONS_MS}} for e in range(nB)}
              for s in ("bid", "ask")}

    for i in range(len(grid_ts)):
        bi = int(gidx[i])
        t0 = int(grid_ts[i])
        m0 = float(mid[bi])
        bid_px, ask_px = float(bid[bi]), float(ask[bi])
        qb, qa = float(bq[bi]), float(aq[bi])
        if not (np.isfinite(m0) and m0 > 0 and bid_px > 0 and ask_px > bid_px):
            continue
        tot = qb + qa
        if tot <= 0:
            continue
        imb = (qb - qa) / tot
        e = int(np.searchsorted(IMB_EDGES, imb, side="right") - 1)
        e = max(0, min(e, nB - 1))

        for side in ("bid", "ask"):
            limit = bid_px if side == "bid" else ask_px
            shown = qb if side == "bid" else qa
            ts_arr, cum_arr = tidx[side].get(round(limit, 12), (None, None))
            if ts_arr is not None:
                k = bisect.bisect_right(ts_arr, t0)
                j = bisect.bisect_left(ts_arr, t0 - 10_000)
                recent = (float(cum_arr[k - 1]) - (float(cum_arr[j - 1]) if j > 0 else 0.0)) if k > 0 else 0.0
                q_ahead = min(shown, recent) if recent > 0 else shown
            else:
                q_ahead = shown
            if not (q_ahead >= 0):
                q_ahead = shown

            B = bucket[side][e]
            B["all"] += 1
            tf = None
            if ts_arr is not None:
                k = bisect.bisect_right(ts_arr, t0)
                if k < len(ts_arr):
                    base = float(cum_arr[k - 1]) if k > 0 else 0.0
                    kk = bisect.bisect_right(cum_arr, base + q_ahead, lo=k)
                    if kk < len(ts_arr) and ts_arr[kk] - t0 <= HOLD_MS:
                        tf = int(ts_arr[kk])
            if tf is None:
                continue
            B["fill"] += 1
            fi = min(int(np.searchsorted(bts, tf, side="left")), len(bts) - 1)
            mf = float(mid[fi])
            for h in HORIZONS_MS:
                mi = min(int(np.searchsorted(bts, tf + h, side="left")), len(bts) - 1)
                B["mk"][h].append(float((mid[mi] / mf - 1.0) * 1e4) if mf > 0 else None)
                # 备用：t0 基准
                mi0 = min(int(np.searchsorted(bts, t0 + h, side="left")), len(bts) - 1)
                B["mk_t0"][h].append(float((mid[mi0] / m0 - 1.0) * 1e4))

    out = {"symbol": sym, "candidates": int(len(grid_ts)), "buckets": {"bid": {}, "ask": {}},
           "unconditional": {}}
    for side in ("bid", "ask"):
        allmk = {h: [] for h in HORIZONS_MS}
        allmk0 = {h: [] for h in HORIZONS_MS}
        nf = 0
        for e in range(nB):
            B = bucket[side][e]
            lbl = f"[{IMB_EDGES[e]:+.1f},{IMB_EDGES[e+1]:+.1f})"
            rec = {"n_cand": B["all"], "n_fill": B["fill"],
                   "fill_rate": (B["fill"] / B["all"]) if B["all"] else None}
            for h in HORIZONS_MS:
                rec[f"mk{h}_from_tf"] = stats(B["mk"][h])
                rec[f"mk{h}_from_t0"] = stats(B["mk_t0"][h])
                allmk[h].extend(B["mk"][h])
                allmk0[h].extend(B["mk_t0"][h])
            nf += B["fill"]
            out["buckets"][side][lbl] = rec
        u = {"n_fill": nf}
        for h in HORIZONS_MS:
            u[f"mk{h}_from_tf"] = stats(allmk[h])
            u[f"mk{h}_from_t0"] = stats(allmk0[h])
        out["unconditional"][side] = u
    return out


def main():
    cn = pg()
    cur = cn.cursor()
    cur.execute("select min(event_ts_ms), max(event_ts_ms) from asterdex_book_ticker")
    lo, hi = cur.fetchone()
    cn.close()
    T0 = int(lo)
    T1 = min(int(hi), T0 + int(HOURS * 3600 * 1000))

    rep = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "design": {
            "grid_ms": GRID_MS, "hold_ms": HOLD_MS, "horizons_ms": HORIZONS_MS,
            "imb_edges": IMB_EDGES,
            "imb": "(bid_qty - ask_qty)/(bid_qty + ask_qty) at order time",
            "markout_ref": "成交时刻 tf 的 mid（论文 Table 1 口径）；另存 t0 基准供对照",
            "paper_prediction": "买单挂在 imb<0（逆失衡/短队列）、卖单挂在 imb>0 应最好",
            "trend_prediction": "买单挂在 imb>0、卖单挂在 imb<0（顺失衡）应最好",
        },
        "window_ms": [T0, T1], "window_h": HOURS,
        "symbols": {},
    }

    pool = {s: {e: {h: [] for h in HORIZONS_MS} for e in range(len(IMB_EDGES) - 1)}
            for s in ("bid", "ask")}

    for sym in SYMBOLS:
        try:
            book, trades = load_symbol(sym, T0, T1)
        except Exception as e:  # noqa: BLE001
            rep["symbols"][sym] = {"error": f"load: {e}"}
            print(f"[{sym}] LOAD ERROR {e}")
            continue
        if not book or not trades:
            rep["symbols"][sym] = {"error": "no data"}
            continue
        r = analyse(sym, book, trades)
        rep["symbols"][sym] = r
        for side in ("bid", "ask"):
            for e in range(len(IMB_EDGES) - 1):
                lbl = f"[{IMB_EDGES[e]:+.1f},{IMB_EDGES[e+1]:+.1f})"
                st = r["buckets"][side][lbl]["mk5000_from_tf"]
                if st:
                    pool[side][e][5000].extend([st["mean"]] * min(st["n"], 200))

        def g(side, lo_, hi_, h=5000):
            """聚合 [lo_,hi_) 覆盖的所有桶（桶边界来自 IMB_EDGES）。"""
            vals = []
            for e in range(len(IMB_EDGES) - 1):
                if IMB_EDGES[e] >= lo_ and IMB_EDGES[e + 1] <= hi_:
                    st = r["buckets"][side][f"[{IMB_EDGES[e]:+.1f},{IMB_EDGES[e+1]:+.1f})"][f"mk{h}_from_tf"]
                    if st:
                        vals.extend([st["mean"]] * st["n"])
            return stats(vals)
        fmt = lambda x: (f"{x['mean']:+.3f}(t{x['t']:+.1f},n{x['n']})" if x else "NA")
        ub = r["unconditional"]["bid"].get("mk5000_from_tf")
        ua = r["unconditional"]["ask"].get("mk5000_from_tf")
        print(f"[{sym:<10}] 无条件 mk5s(tf): bid={fmt(ub)}  ask={fmt(ua)}")
        print(f"{'':<12} 逆失衡: bid@imb<-0.3={fmt(g('bid',-1.0,-0.3))}  ask@imb>+0.3={fmt(g('ask',0.3,1.0))}")
        print(f"{'':<12} 顺失衡: bid@imb>+0.3={fmt(g('bid',0.3,1.0))}  ask@imb<-0.3={fmt(g('ask',-1.0,-0.3))}")

    rep["pooled_mean_by_bucket"] = {}
    print("\n=== 池化（逐币逐桶均值再平均，成交后 5s markout，tf 基准）===")
    print(f"{'imb bucket':<16}{'#sym bid':>9}{'bid mean':>10}{'#sym ask':>9}{'ask mean':>10}{'bid-ask':>10}")
    for e in range(len(IMB_EDGES) - 1):
        lbl = f"[{IMB_EDGES[e]:+.1f},{IMB_EDGES[e+1]:+.1f})"
        b, a = pool["bid"][e][5000], pool["ask"][e][5000]
        bm = float(np.mean(b)) if b else float("nan")
        am = float(np.mean(a)) if a else float("nan")
        rep["pooled_mean_by_bucket"][lbl] = {
            "bid_n_sym": len(b), "bid_mean": (bm if b else None),
            "ask_n_sym": len(a), "ask_mean": (am if a else None),
        }
        print(f"{lbl:<16}{len(b):>9}{bm:>10.3f}{len(a):>9}{am:>10.3f}{bm-am:>10.3f}")

    p = OUT / "h6_imbalance_conditional.json"
    p.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwrote {p}")


if __name__ == "__main__":
    main()
