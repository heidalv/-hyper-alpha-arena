"""H7 · 分段时间稳定性检验（H6 的"逆失衡更差"是结构性还是单段行情？）

H6 发现（12h 窗口，9 币）：
  论文说「逆失衡挂单」是唯一出路，但实测每侧最差档就是逆失衡：
    bid@imb<-0.3 = -0.385bp（逆失衡挂买） vs bid@imb>+0.3 = -0.252bp（顺失衡）
    ask@imb>+0.3 = +0.196bp（逆失衡挂卖） vs ask@imb<-0.3 = +0.238bp（顺失衡）
  9 币中 8 币一致。且 bid 恒差于 ask ⇒ 疑似窗口内单边上涨造成的 beta。

H7 的问题：把 3.9 天切成 N 段，逐段算
    (a) 该段的"顺失衡减逆失衡"markout 差（论文预测应为负；若为负=支持论文）
    (b) 该段的实际行情方向（用 mid 首末变化）
  看 (a) 的符号是否随 (b) 翻转：
    - 若随行情翻转 ⇒ 是 beta/regime，必须加 regime 判定，不能当稳定 alpha
    - 若恒为正   ⇒ 结构性，论文机制在本场不成立，设计必须走"顺失衡/顺势"

输出：research_l1/out/h7_regime_stability.json
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
GRID_MS = 1000
HOLD_MS = 10_000
HORIZON_MS = 5000
N_SEG = int(os.environ.get("H7_NSEG", "4"))


def pg():
    url = os.environ["DATABASE_URL"]
    for d in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(d, "")
    head, _, _ = url.rpartition("/")
    cn = psycopg2.connect(head + "/alpha_market")
    cn.autocommit = True
    return cn


def stats(vals):
    v = np.asarray([x for x in vals if x is not None and np.isfinite(x)], dtype=np.float64)
    if v.size == 0:
        return None
    o = {"n": int(v.size), "mean": float(v.mean())}
    if v.size > 1:
        se = float(v.std(ddof=1)) / float(np.sqrt(v.size))
        o["t"] = (float(v.mean() / se) if se > 0 else None)
    return o


def main():
    cn = pg()
    cur = cn.cursor()
    cur.execute("select min(event_ts_ms), max(event_ts_ms) from asterdex_book_ticker")
    lo, hi = cur.fetchone()
    cn.close()
    T0, T1 = int(lo), int(hi)
    span = T1 - T0
    print(f"full span {span/3600000:.2f}h  ({N_SEG} segments)")

    rep = {"generated_at": datetime.now(timezone.utc).isoformat(),
           "window_ms": [T0, T1], "span_h": span / 3600000.0, "n_seg": N_SEG,
           "horizon_ms": HORIZON_MS, "grid_ms": GRID_MS,
           "segments": []}

    # 每段累积：seg -> side -> {"trend_bucket": [..], "contrarian": [..]}  以及 mid 首末
    seg_acc = [{"bid": {"trend": [], "contra": []},
                "ask": {"trend": [], "contra": []},
                "mid_first": {}, "mid_last": {}} for _ in range(N_SEG)]

    for sym in SYMBOLS:
        cn = pg()
        cur = cn.cursor()
        cur.execute(
            "select event_ts_ms, bid_px, bid_qty, ask_px, ask_qty from asterdex_book_ticker "
            "where symbol=%s order by event_ts_ms", (sym,))
        book = cur.fetchall()
        cur.execute(
            "select event_ts_ms, price, qty, is_buyer_maker from asterdex_trades "
            "where symbol=%s order by event_ts_ms", (sym,))
        trades = cur.fetchall()
        cn.close()
        if not book or not trades:
            print(f"[{sym}] no data")
            continue

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

        bh, sh = defaultdict(list), defaultdict(list)
        for k in range(len(tts)):
            (bh if bmaker[k] else sh)[tpx[k]].append((int(tts[k]), float(tqt[k])))
        tidx = {}
        for tag, dd in (("bid", bh), ("ask", sh)):
            built = {}
            for px, arr in dd.items():
                arr.sort()
                built[round(px, 12)] = ([a[0] for a in arr], np.cumsum([a[1] for a in arr]))
            tidx[tag] = built

        gts = np.arange(int(bts[0]), int(bts[-1]), GRID_MS, dtype=np.int64)
        gidx = np.clip(np.searchsorted(bts, gts, side="right") - 1, 0, len(bts) - 1)

        for i in range(len(gts)):
            t0 = int(gts[i])
            sg = min(int((t0 - T0) / span * N_SEG), N_SEG - 1)
            bi = int(gidx[i])
            m0 = float(mid[bi])
            bid_px, ask_px = float(bid[bi]), float(ask[bi])
            qb, qa = float(bq[bi]), float(aq[bi])
            if not (np.isfinite(m0) and m0 > 0 and bid_px > 0 and ask_px > bid_px):
                continue
            tot = qb + qa
            if tot <= 0:
                continue
            if sym not in seg_acc[sg]["mid_first"]:
                seg_acc[sg]["mid_first"][sym] = m0
            seg_acc[sg]["mid_last"][sym] = m0

            imb = (qb - qa) / tot
            for side in ("bid", "ask"):
                limit = bid_px if side == "bid" else ask_px
                shown = qb if side == "bid" else qa
                ts_arr, cum_arr = tidx[side].get(round(limit, 12), (None, None))
                if ts_arr is None:
                    continue
                k = bisect.bisect_right(ts_arr, t0)
                j = bisect.bisect_left(ts_arr, t0 - 10_000)
                recent = (float(cum_arr[k - 1]) - (float(cum_arr[j - 1]) if j > 0 else 0.0)) if k > 0 else 0.0
                q_ahead = min(shown, recent) if recent > 0 else shown
                if not (q_ahead >= 0):
                    q_ahead = shown
                if k >= len(ts_arr):
                    continue
                base = float(cum_arr[k - 1]) if k > 0 else 0.0
                kk = bisect.bisect_right(cum_arr, base + q_ahead, lo=k)
                if kk >= len(ts_arr) or ts_arr[kk] - t0 > HOLD_MS:
                    continue
                tf = int(ts_arr[kk])
                fi = min(int(np.searchsorted(bts, tf, side="left")), len(bts) - 1)
                mf = float(mid[fi])
                if not (mf > 0):
                    continue
                mi = min(int(np.searchsorted(bts, tf + HORIZON_MS, side="left")), len(bts) - 1)
                mk = float((mid[mi] / mf - 1.0) * 1e4)

                # "顺失衡"= 挂在长队列侧（bid 且 imb>0 / ask 且 imb<0）
                # "逆失衡"= 挂在短队列侧（bid 且 imb<0 / ask 且 imb>0）
                if abs(imb) < 0.1:
                    continue
                trend_side = (side == "bid" and imb > 0) or (side == "ask" and imb < 0)
                seg_acc[sg][side]["trend" if trend_side else "contra"].append(mk)

        print(f"  [{sym}] done")

    for sg in range(N_SEG):
        rec = {"seg": sg, "symbols": {}}
        rec["mid_move_pct"] = {}
        for sym in SYMBOLS:
            f = seg_acc[sg]["mid_first"].get(sym)
            l = seg_acc[sg]["mid_last"].get(sym)
            if f and l:
                rec["mid_move_pct"][sym] = (l / f - 1.0) * 100.0
        moves = list(rec["mid_move_pct"].values())
        rec["mean_mid_move_pct"] = float(np.mean(moves)) if moves else None

        tot = {"bid": {"trend": [], "contra": []}, "ask": {"trend": [], "contra": []}}
        for side in ("bid", "ask"):
            for kind in ("trend", "contra"):
                tot[side][kind] = seg_acc[sg][side][kind]
        rec["bid_trend"] = stats(tot["bid"]["trend"])
        rec["bid_contra"] = stats(tot["bid"]["contra"])
        rec["ask_trend"] = stats(tot["ask"]["trend"])
        rec["ask_contra"] = stats(tot["ask"]["contra"])
        # 论文预测：顺失衡应当更差 ⇒ (trend - contra) 应为负
        diffs = []
        for side in ("bid", "ask"):
            t, c = rec[f"{side}_trend"], rec[f"{side}_contra"]
            if t and c:
                diffs.append(t["mean"] - c["mean"])
        rec["trend_minus_contra_bp"] = float(np.mean(diffs)) if diffs else None
        rep["segments"].append(rec)

        print(f"\nseg{sg}: mean mid move = {rec['mean_mid_move_pct']:+.3f}%")
        for side in ("bid", "ask"):
            t = rec[f"{side}_trend"]; c = rec[f"{side}_contra"]
            f = lambda x: (f"{x['mean']:+.3f}(n{x['n']})" if x else "NA")
            print(f"   {side}: 顺失衡={f(t)}  逆失衡={f(c)}  差={rec['trend_minus_contra_bp']:+.3f}bp")

    ds = [r["trend_minus_contra_bp"] for r in rep["segments"] if r["trend_minus_contra_bp"] is not None]
    ms = [r["mean_mid_move_pct"] for r in rep["segments"] if r["mean_mid_move_pct"] is not None]
    print(f"\n=== 稳定性检验 ===")
    print(f"各段 (顺-逆) bp: {[round(x,3) for x in ds]}")
    print(f"各段 行情涨幅 %: {[round(x,3) for x in ms]}")
    if len(ds) > 1 and len(ms) > 1:
        cc = float(np.corrcoef(ds, ms)[0, 1])
        print(f"相关系数 corr(顺-逆 markout, 行情涨幅) = {cc:+.3f}")
        print(f"符号一致性: 顺-逆 为正的段数 = {sum(1 for x in ds if x > 0)}/{len(ds)}")
        rep["corr_diff_vs_move"] = cc
        rep["n_seg_trend_better"] = sum(1 for x in ds if x > 0)
        rep["n_seg"] = len(ds)

    p = OUT / "h7_regime_stability.json"
    p.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwrote {p}")


if __name__ == "__main__":
    main()
