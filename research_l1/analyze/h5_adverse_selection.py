"""H5 · 逆向选择测量（Aster 真实 tick 级，队列感知，本地费率）

目的：用自有数据 + 真实费率，复现 Albers et al. (arXiv:2502.18625v2) Table 1 / Figure 11 的
      核心机制——"被动挂单成交后 markout 为负"，并给出我们宇宙的基准线。

为什么必须做：
  论文全套盈利结论建立在 Binance maker -0.5bp 返佣（往返 +1.0bp）之上。
  Aster maker = 0bp ⇒ 论文旗舰策略 +0.71bp/往返 扣掉返佣只剩 -0.29bp。
  照抄之前必须先量出本地真实 markout。

方法要点（无前视）：
  1. 事件源：asterdex_book_ticker（top-of-book，毫秒）+ asterdex_trades（带 is_buyer_maker）。
  2. 候选挂单：每 1s 在 best bid 挂买单 / best ask 挂卖单。
  3. 队列感知成交推断：
     - 我挂单时，同价位已有 Q_ahead 的名义量排在我前面（Q_ahead = min(该档显示量, 最近10s内
       同价成交量)）。这是"我前面有多少"的保守估计。
     - 我成交 ⟺ 挂单后 10s 内，对手方主动打到我这个价位的**累计**成交量 > Q_ahead。
     - 因为只在 trade price 恰好 == 我的限价时才可能是我成交，故按价格分桶即可。
  4. markout：以**挂单时刻 t0 的 mid** 为基准（无泄漏），在 t0+100ms/500ms/1s/5s/30s 采样 mid。
     另给"以成交时刻 mid 为基准"的版本，用于与论文 Table 1 逐格对比。

输出：research_l1/out/h5_adverse_selection.json
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
HOURS = float(os.environ.get("H5_HOURS", "12"))
GRID_MS = int(os.environ.get("H5_GRID_MS", "1000"))   # 候选挂单采样间隔
HOLD_MS = 10_000                                       # 挂单最多挂多久（未成交则撤）
HORIZONS_MS = [100, 500, 1000, 5000, 30000]


def pg():
    url = os.environ["DATABASE_URL"]
    for d in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(d, "")
    head, _, _ = url.rpartition("/")
    cn = psycopg2.connect(head + "/alpha_market")
    cn.autocommit = True
    return cn


def load_symbol(sym, t0, t1):
    """每个币种用独立连接读取，避免长事务被服务端断开。"""
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
    out = {
        "n": int(v.size), "mean": float(v.mean()), "median": float(np.median(v)),
        "p25": float(np.percentile(v, 25)), "p75": float(np.percentile(v, 75)),
        "share_pos": float((v > 0).mean()),
    }
    if v.size > 1:
        sd = float(v.std(ddof=1))
        se = sd / float(np.sqrt(v.size))
        out.update({"std": sd, "se": se, "t": (float(v.mean() / se) if se > 0 else None)})
    return out


def precompute_trade_index(tts, tpx, tqt, buyer_maker):
    """按价格分桶：每桶内按时间排序 + 对手方主动成交量累计。"""
    buy_hits = defaultdict(list)   # 主动卖(打 bid) 的成交，按价格
    sell_hits = defaultdict(list)  # 主动买(打 ask) 的成交，按价格
    for k in range(len(tts)):
        px = tpx[k]
        if buyer_maker[k]:
            buy_hits[px].append((int(tts[k]), float(tqt[k])))
        else:
            sell_hits[px].append((int(tts[k]), float(tqt[k])))
    idx = {}
    for tag, d in (("bid", buy_hits), ("ask", sell_hits)):
        built = {}
        for px, arr in d.items():
            arr.sort()
            ts = [a[0] for a in arr]
            cum = np.cumsum([a[1] for a in arr])
            built[round(px, 12)] = (ts, cum)
        idx[tag] = built
    return idx


def main():
    cn = pg()
    cur = cn.cursor()
    cur.execute("select min(event_ts_ms), max(event_ts_ms) from asterdex_book_ticker")
    lo, hi = cur.fetchone()
    cn.close()
    T0 = int(lo)
    T1 = min(int(hi), T0 + int(HOURS * 3600 * 1000))

    fees = {}
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:8000/api/trading/config/fees", timeout=20) as r:
            d = json.loads(r.read().decode("utf-8"))
        for it in d.get("items", []):
            fees[it["exchange"]] = (float(it["maker_bp"]), float(it["taker_bp"]))
    except Exception as e:  # noqa: BLE001
        print("warn: fee fetch failed:", e)
    fee_maker_bp, fee_taker_bp = fees.get("asterdex", (0.0, 4.0))

    rep = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "design": {
            "grid_ms": GRID_MS, "hold_ms": HOLD_MS, "horizons_ms": HORIZONS_MS,
            "note": ("markout_from_t0_* 以挂单时 mid 为基准，无前视；"
                     "markout_from_tf_* 以成交时 mid 为基准，对齐论文 Table 1。"
                     "成交推断为队列感知（累计对手方成交量 > 排我前面的量），非纯上界。"),
        },
        "window_ms": [T0, T1], "window_h": HOURS,
        "fees_asterdex": {"maker_bp": fee_maker_bp, "taker_bp": fee_taker_bp},
        "fees_all": fees,
        "symbols": {},
    }

    for sym in SYMBOLS:
        try:
            book, trades = load_symbol(sym, T0, T1)
        except Exception as e:  # noqa: BLE001
            rep["symbols"][sym] = {"symbol": sym, "error": f"load: {e}"}
            print(f"[{sym}] LOAD ERROR: {e}")
            continue
        if not book or not trades:
            rep["symbols"][sym] = {"symbol": sym, "error": "no data"}
            print(f"[{sym}] NO DATA book={len(book)} trade={len(trades)}")
            continue

        bts = np.array([r[0] for r in book], dtype=np.int64)
        bid = np.array([r[1] for r in book], dtype=np.float64)
        bq = np.array([r[2] for r in book], dtype=np.float64)
        ask = np.array([r[3] for r in book], dtype=np.float64)
        aq = np.array([r[4] for r in book], dtype=np.float64)
        mid = (bid + ask) / 2.0
        sp_bp = (ask - bid) / mid * 1e4

        tts = np.array([r[0] for r in trades], dtype=np.int64)
        tpx = np.array([r[1] for r in trades], dtype=np.float64)
        tqt = np.array([r[2] for r in trades], dtype=np.float64)
        bmaker = np.array([bool(r[3]) for r in trades])

        tidx = precompute_trade_index(tts, tpx, tqt, bmaker)

        # 候选网格
        grid_ts = list(range(int(bts[0]), int(bts[-1]), GRID_MS))
        gidx = np.searchsorted(bts, np.array(grid_ts, dtype=np.int64), side="right") - 1
        gidx = np.clip(gidx, 0, len(bts) - 1)

        # 各 horizon 的 mid 索引（无前视：严格 >= t0+h）
        hidx = {}
        for h in HORIZONS_MS:
            arr = np.searchsorted(bts, np.array(grid_ts, dtype=np.int64) + h, side="left")
            hidx[h] = np.clip(arr, 0, len(bts) - 1)

        n = len(grid_ts)
        gt = np.array(grid_ts, dtype=np.int64)

        # ---------- 构造候选挂单 ----------
        # 每个 grid 点 2 个候选（挂 bid / 挂 ask）
        cand = []  # (side, t0, limit, m0, q_ahead, spread_bp, ci)
        for i in range(n):
            bi = int(gidx[i])
            t0 = int(gt[i])
            m0 = float(mid[bi])
            if not np.isfinite(m0) or m0 <= 0:
                continue
            bid_px = float(bid[bi]); ask_px = float(ask[bi])
            if not (bid_px > 0 and ask_px > bid_px):
                continue
            sp = float(sp_bp[bi])
            if not np.isfinite(sp):
                continue
            for side in ("bid", "ask"):
                limit = bid_px if side == "bid" else ask_px
                shown = float(bq[bi]) if side == "bid" else float(aq[bi])
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
                cand.append((side, t0, limit, m0, q_ahead, sp, i))

        # ---------- 逐候选求成交与 markout ----------
        rows = []
        for side, t0, limit, m0, q_ahead, sp, ci in cand:
            ts_arr, cum_arr = tidx[side].get(round(limit, 12), (None, None))
            tf = None
            if ts_arr is not None:
                k = bisect.bisect_right(ts_arr, t0)
                if k < len(ts_arr):
                    base = float(cum_arr[k - 1]) if k > 0 else 0.0
                    kk = bisect.bisect_right(cum_arr, base + q_ahead, lo=k)
                    if kk < len(ts_arr) and ts_arr[kk] - t0 <= HOLD_MS:
                        tf = int(ts_arr[kk])
            rec = {"side": side, "t0": t0, "limit": limit, "mid0": m0,
                   "q_ahead": q_ahead, "spread_bp": sp,
                   "filled": tf is not None, "wait_ms": (tf - t0) if tf is not None else None}
            for h in HORIZONS_MS:
                mi = int(hidx[h][ci])
                rec[f"mk_t0_{h}"] = float((mid[mi] / m0 - 1.0) * 1e4)
            if tf is not None:
                fi = min(int(np.searchsorted(bts, tf, side="left")), len(bts) - 1)
                mf = float(mid[fi])
                rec["mid_fill"] = mf
                for h in HORIZONS_MS:
                    mi = min(int(np.searchsorted(bts, tf + h, side="left")), len(bts) - 1)
                    rec[f"mk_tf_{h}"] = float((mid[mi] / mf - 1.0) * 1e4) if mf > 0 else None
            rows.append(rec)

        # ---------- 汇总 ----------
        n_cand = len(rows)
        fill_rows = [r for r in rows if r["filled"]]
        nofill_rows = [r for r in rows if not r["filled"]]
        out = {
            "symbol": sym, "book_n": len(book), "trade_n": len(trades),
            "grid_points": n, "candidates": n_cand, "filled": len(fill_rows),
            "fill_rate": (len(fill_rows) / n_cand) if n_cand else None,
            "spread_bp": stats(sp_bp),
            "wait_ms": stats([r["wait_ms"] for r in fill_rows]),
            "q_ahead": stats([r["q_ahead"] for r in rows]),
        }
        for h in HORIZONS_MS:
            out[f"mk_t0_{h}_all"] = stats([r[f"mk_t0_{h}"] for r in rows])
            out[f"mk_t0_{h}_filled"] = stats([r[f"mk_t0_{h}"] for r in fill_rows])
            out[f"mk_t0_{h}_unfilled"] = stats([r[f"mk_t0_{h}"] for r in nofill_rows])
            out[f"mk_tf_{h}"] = stats([r.get(f"mk_tf_{h}") for r in fill_rows])
        for side in ("bid", "ask"):
            sf = [r for r in fill_rows if r["side"] == side]
            sc = [r for r in rows if r["side"] == side]
            out[f"side_{side}"] = {
                "candidates": len(sc), "filled": len(sf),
                "fill_rate": (len(sf) / len(sc)) if sc else None,
                "mk_t0_1000_filled": stats([r["mk_t0_1000"] for r in sf]),
                "mk_t0_5000_filled": stats([r["mk_t0_5000"] for r in sf]),
            }
        # 按排队量分档看 markout（对应论文 Table 1 的 queue-size 维度）
        if fill_rows:
            qa = np.array([r["q_ahead"] for r in fill_rows])
            q1, q2 = float(np.percentile(qa, 33)), float(np.percentile(qa, 67))
            out["by_q_ahead"] = {}
            for label, lo_, hi_ in (("small", -1, q1), ("medium", q1, q2), ("large", q2, float("inf"))):
                sub = [r for r in fill_rows if lo_ < r["q_ahead"] <= hi_]
                out["by_q_ahead"][label] = {
                    "q_range": [lo_, hi_], "n": len(sub),
                    "mk_t0_1000": stats([r["mk_t0_1000"] for r in sub]),
                    "mk_t0_5000": stats([r["mk_t0_5000"] for r in sub]),
                }
        # 纯挂单往返（论文口径）：每笔 markout 即一次"挂单腿"，
        # 往返 = 买挂 + 卖挂，费率 = 2 * maker
        for h in (1000, 5000, 30000):
            s = out[f"mk_t0_{h}_filled"]
            if s:
                out[f"net_maker_roundtrip_{h}ms"] = {
                    "gross_bp": s["mean"], "fee_bp": 2.0 * fee_maker_bp,
                    "net_bp": s["mean"] - 2.0 * fee_maker_bp,
                }
        rep["symbols"][sym] = out

        spm = out["spread_bp"]["median"]
        m1 = out.get("mk_t0_1000_filled") or {}
        m5 = out.get("mk_t0_5000_filled") or {}
        nf5 = out.get("mk_t0_5000_unfilled") or {}
        print(f"[{sym:<10}] spr={spm:6.3f}bp fill={out['fill_rate']:.4f} "
              f"mk1s_f={m1.get('mean'):+.3f}(t{m1.get('t'):+.0f},n{m1.get('n')}) "
              f"mk5s_f={m5.get('mean'):+.3f}(t{m5.get('t'):+.0f}) "
              f"mk5s_unf={nf5.get('mean'):+.3f}")

    p = OUT / "h5_adverse_selection.json"
    p.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwrote {p}")


if __name__ == "__main__":
    main()
