"""H12 · 多档深度特征（补 H11 的最大盲区）

H11 的结论：10 个**仅用 top-of-book + 逐笔**的特征，对 P(revert) 的 IC 全部 ≈ 0（最高 0.0194），
                    需要 ≥ 0.05 才有用。

H11 的盲区（本脚本要补的）：
  全程只用了 asterdex_book_ticker（**仅一档**）。而
    · asterdex_depth_snapshots 有 **20 档完整深度**，2678 万行，p50 105ms，94 小时；
    · 论文 permutation importance 第 3 名 `ob bid liq` **必须**多档深度才能算：
      "top bid 价与执行 $500K 的 VWAP 之间的价差(bp)；越低=盘口附近流动性越厚"。
  论文据此得到的结论是："模型偏好**盘口薄但近盘深处厚**的形态"。

H12 新增特征（全部只用建仓时刻可得信息）：
  ---- 多档深度（论文对应）----
  ob_bid_half / ob_ask_half  执行 $500K 的 VWAP 距 top 的 bp（论文 ob bid/ask half）
  ob_half                    两者平均（论文 ob half）
  ob_asym                    (ask_half - bid_half)/平均 —— 哪一侧更"厚"
  depth_imb_5/10/20          前 5/10/20 档量失衡 (B-A)/(B+A)
  slope_bid / slope_ask      累计量对价格距离的斜率（流动性陡峭度）
  q_ratio_*                  第 k 档量 / 前 k 档量（近盘集中度）
  gap_1                      第 1 档到第 2 档的价差 / 点差（档位空洞）
  tot_depth_usd              前 20 档名义总额
  ---- 逐笔流（比 H11 更细）----
  tvol_1s/5s/30s/300s        带符号的主动成交量（$）
  tcnt_*                     成交笔数
  big_share_30s              大单（> 30s 中位 5 倍）占比
  sweep_30s                  单笔 > $20k 的笔数
  ---- 波动 ----
  vol_5s/60s/600s            中价收益标准差(bp)
  ---- 基础（H11 已有，作为对照）----
  imb, ofi_1s/5s/30s, ret_1s/5s/30s

标签：y = 1 ⟺ 建仓后 30s 内，平仓腿在「入场价 ± 入场时点差」成交（H11 口径）。
训练/测试：时间序 60/40 分割。**先看样本外 IC，再看双侧符号一致性。**

输出：research_l1/out/h12_depth_features.json
"""
from __future__ import annotations

import bisect
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env", override=False)

import numpy as np  # noqa: E402
import psycopg2  # noqa: E402


class _Enc(json.JSONEncoder):
    """numpy 标量 / bool_ 安全序列化。"""
    def default(self, o):
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            v = float(o)
            return v if np.isfinite(v) else None
        if isinstance(o, (np.bool_, bool)):
            return bool(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
        return super().default(o)

OUT = Path(__file__).resolve().parents[1] / "out"
OUT.mkdir(parents=True, exist_ok=True)

SYMBOLS = os.environ.get("H12_SYMBOLS", "ASTERUSDT,DOGEUSDT,XRPUSDT,SOLUSDT,ETHUSDT,BTCUSDT").split(",")
HOURS = float(os.environ.get("H12_HOURS", "12"))
GRID_MS = 1000
HOLD_MS = 30_000
TARGET_USD = 500_000.0     # 论文的 $500K 口径（对 ASTER 这类小币会全部吃穿，故同时给归一化版本）


def pg():
    url = os.environ["DATABASE_URL"]
    for d in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(d, "")
    head, _, _ = url.rpartition("/")
    cn = psycopg2.connect(head + "/alpha_market")
    cn.autocommit = True
    return cn


def vwap_gap_bp(levels, target_usd, ref_px, side):
    """执行 target_usd 的 VWAP 距 top 的 bp（论文 ob bid/ask half 口径）。

    levels: [[price, qty], ...] 按价格优劣排序（bids 降序 / asks 升序）
    只吃前 20 档；不足则用最后一档价格兜底（=吃穿）。
    """
    if not levels or ref_px <= 0:
        return None
    got_usd = 0.0
    got_qty = 0.0
    last_px = levels[0][0]
    for px, q in levels:
        px = float(px); q = float(q)
        if px <= 0 or q <= 0:
            continue
        last_px = px
        notional = px * q
        need = target_usd - got_usd
        if notional >= need:
            got_qty += need / px
            got_usd = target_usd
            break
        got_qty += q
        got_usd += notional
    if got_qty <= 0:
        return None
    vwap = got_usd / got_qty
    # 距离按"更差的方向"计：bid 侧 VWAP 更低 ⇒ 距离为正
    if side == "bid":
        return (ref_px - vwap) / ref_px * 1e4
    return (vwap - ref_px) / ref_px * 1e4


def depth_stats(levels):
    """前 20 档的辅助量。"""
    if not levels:
        return None
    px = np.array([float(x[0]) for x in levels], dtype=np.float64)
    q = np.array([float(x[1]) for x in levels], dtype=np.float64)
    if px.size < 2:
        return None
    return {"px": px, "q": q, "notional": px * q}


def load_symbol(sym, t0, t1):
    cn = pg()
    try:
        cur = cn.cursor()
        cur.execute(
            "select event_ts_ms, bid_px, bid_qty, ask_px, ask_qty from asterdex_book_ticker "
            "where symbol=%s and event_ts_ms between %s and %s order by event_ts_ms",
            (sym, t0, t1))
        book = cur.fetchall()
        cur.execute(
            "select event_ts_ms, price, qty, is_buyer_maker from asterdex_trades "
            "where symbol=%s and event_ts_ms between %s and %s order by event_ts_ms",
            (sym, t0, t1))
        tr = cur.fetchall()
        cur.execute(
            "select event_ts_ms, bids, asks from asterdex_depth_snapshots "
            "where symbol=%s and event_ts_ms between %s and %s order by event_ts_ms",
            (sym, t0, t1))
        dep = cur.fetchall()
        return book, tr, dep
    finally:
        cn.close()


def build(sym, book, tr, dep, t0, t1):
    bts = np.array([r[0] for r in book], dtype=np.int64)
    bid = np.array([r[1] for r in book], dtype=np.float64)
    bq = np.array([r[2] for r in book], dtype=np.float64)
    ask = np.array([r[3] for r in book], dtype=np.float64)
    aq = np.array([r[4] for r in book], dtype=np.float64)
    mid = (bid + ask) / 2.0

    # 深度快照索引（event_ts_ms -> 解析后的 levels）
    dts = np.array([r[0] for r in dep], dtype=np.int64)
    d_bids = [r[1] for r in dep]
    d_asks = [r[2] for r in dep]

    tts = np.array([r[0] for r in tr], dtype=np.int64)
    tpx = np.array([r[1] for r in tr], dtype=np.float64)
    tqt = np.array([r[2] for r in tr], dtype=np.float64)
    bm = np.array([bool(r[3]) for r in tr])
    signed_usd = tpx * tqt * np.where(bm, -1.0, 1.0)   # 主动买为正
    tnotional = tpx * tqt
    csum_usd = np.concatenate([[0.0], np.cumsum(signed_usd)])
    csum_n = np.arange(len(tts) + 1, dtype=np.float64)

    # 按价格分桶（进场成交判定）
    from collections import defaultdict
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

    grid = np.arange(int(bts[0]), int(bts[-1]), GRID_MS, dtype=np.int64)
    gidx = np.clip(np.searchsorted(bts, grid, side="right") - 1, 0, len(bts) - 1)
    gmid = mid[gidx]
    n = len(grid)

    def tr_slice(lo_ms, hi_ms):
        i0 = bisect.bisect_left(tts, lo_ms)
        i1 = bisect.bisect_right(tts, hi_ms)
        return i0, i1

    rows = []
    pick = 0
    for gi in range(n):
        t = int(grid[gi])
        m0 = float(gmid[gi])
        bp0, ap0 = float(bid[gidx[gi]]), float(ask[gidx[gi]])
        if not (np.isfinite(m0) and m0 > 0 and bp0 > 0 and ap0 > bp0):
            continue
        sp = ap0 - bp0
        # 深度快照（用 <= t 的最近一条）
        di = bisect.bisect_right(dts, t) - 1
        f = {}
        if di >= 0:
            bl = d_bids[di]; al = d_asks[di]
            sb = depth_stats(bl); sa = depth_stats(al)
            if sb is not None and sa is not None:
                bh_bp = vwap_gap_bp(bl, TARGET_USD, bp0, "bid")
                ah_bp = vwap_gap_bp(al, TARGET_USD, ap0, "ask")
                f["ob_bid_half"] = bh_bp
                f["ob_ask_half"] = ah_bp
                f["ob_half"] = ((bh_bp + ah_bp) / 2.0) if (bh_bp is not None and ah_bp is not None) else None
                f["ob_asym"] = ((ah_bp - bh_bp) / (ah_bp + bh_bp) if (bh_bp and ah_bp and (ah_bp + bh_bp) > 0) else None)
                # 归一化版本：占满 N 档所需美元（消除不同标的绝对量差异）
                f["depth_usd_bid"] = float(np.sum(sb["notional"]))
                f["depth_usd_ask"] = float(np.sum(sa["notional"]))
                # 多档失衡
                for k in (5, 10, 20):
                    qb = float(np.sum(sb["q"][:k])); qa = float(np.sum(sa["q"][:k]))
                    f[f"depth_imb_{k}"] = ((qb - qa) / (qb + qa)) if (qb + qa) > 0 else None
                # 斜率：累计名义的对数 vs 距 top 的 bp
                def slope(s):
                    d = np.abs(s["px"] - s["px"][0]) / s["px"][0] * 1e4
                    c = np.cumsum(s["notional"])
                    if d[-1] <= 0 or c[-1] <= 0:
                        return None
                    return float(np.polyfit(d, np.log(c + 1.0), 1)[0])
                f["slope_bid"] = slope(sb); f["slope_ask"] = slope(sa)
                # 近盘集中度
                for k in (3, 5):
                    tb = float(np.sum(sb["notional"][:k])); ta = float(np.sum(sa["notional"][:k]))
                    f[f"near_share_{k}"] = ((tb / sb["notional"].sum()) + (ta / sa["notional"].sum())) / 2.0 \
                        if (sb["notional"].sum() > 0 and sa["notional"].sum() > 0) else None
                # 档位空洞：第2档距 top 的距离 / 点差
                f["gap_1"] = ((abs(sb["px"][1] - sb["px"][0]) / sp) + (abs(sa["px"][1] - sa["px"][0]) / sp)) / 2.0
        # top-of-book
        tot = float(bq[gidx[gi]] + aq[gidx[gi]])
        f["imb"] = ((float(bq[gidx[gi]]) - float(aq[gidx[gi]])) / tot) if tot > 0 else None
        f["spread_bp"] = sp / m0 * 1e4
        # 逐笔流
        for tag, back in (("1s", 1000), ("5s", 5000), ("30s", 30000), ("300s", 300000)):
            i0, i1 = tr_slice(t - back, t)
            v = float(csum_usd[i1] - csum_usd[i0])
            f[f"tvol_{tag}"] = v / 1000.0            # 千美元
            f[f"tcnt_{tag}"] = float(i1 - i0)
        i0, i1 = tr_slice(t - 30000, t)
        f["sweep_30s"] = float(np.sum(tnotional[i0:i1] > 20000.0)) if i1 > i0 else 0.0
        f["big_share_30s"] = (float(np.sum(tnotional[i0:i1][tnotional[i0:i1] > 3000.0]) / np.sum(tnotional[i0:i1]))
                              if i1 > i0 and np.sum(tnotional[i0:i1]) > 0 else None)
        # OFI（top-of-book 失衡的变化）
        for tag, back in (("1s", 1), ("5s", 5), ("30s", 30)):
            j = gi - back
            if j >= 0:
                tj = float(bq[gidx[j]] + aq[gidx[j]])
                if tj > 0 and tot > 0:
                    f[f"ofi_{tag}"] = f["imb"] - (float(bq[gidx[j]]) - float(aq[gidx[j]])) / tj
                else:
                    f[f"ofi_{tag}"] = None
            else:
                f[f"ofi_{tag}"] = None
        # 收益与波动
        for tag, back in (("1s", 1), ("5s", 5), ("30s", 30)):
            j = gi - back
            f[f"ret_{tag}"] = ((m0 / float(gmid[j]) - 1.0) * 1e4) if j >= 0 else None
        for tag, back in (("5s", 5), ("60s", 60)):
            if gi - back >= 0:
                seg = gmid[max(0, gi - back):gi + 1]
                if seg.size > 2:
                    f[f"vol_{tag}"] = float(np.std(np.diff(seg) / seg[:-1]) * 1e4)
                else:
                    f[f"vol_{tag}"] = None
            else:
                f[f"vol_{tag}"] = None

        # ---- 进场 + 标签 ----
        side = "bid" if (pick % 2 == 0) else "ask"
        pick += 1
        limit = bp0 if side == "bid" else ap0
        shown = float(bq[gidx[gi]]) if side == "bid" else float(aq[gidx[gi]])
        ts_arr, cum = tidx[side].get(round(limit, 12), (None, None))
        if ts_arr is None:
            continue
        k = bisect.bisect_right(ts_arr, t)
        j0 = bisect.bisect_left(ts_arr, t - 10_000)
        recent = (float(cum[k - 1]) - (float(cum[j0 - 1]) if j0 > 0 else 0.0)) if k > 0 else 0.0
        qa = min(shown, recent) if recent > 0 else shown
        if k >= len(ts_arr):
            continue
        base = float(cum[k - 1]) if k > 0 else 0.0
        kk = bisect.bisect_right(cum, base + qa, lo=k)
        if kk >= len(ts_arr):
            continue
        tf = int(ts_arr[kk])
        entry_px = limit
        exit_limit = entry_px + sp if side == "bid" else entry_px - sp
        exit_side = "ask" if side == "bid" else "bid"
        xs, xc = tidx[exit_side].get(round(exit_limit, 12), (None, None))
        completed = False
        if xs is not None:
            z = bisect.bisect_right(xs, tf)
            if z < len(xs) and xs[z] <= tf + HOLD_MS:
                completed = True
        rows.append({**f, "side": side, "y": 1.0 if completed else 0.0, "t": t})
    return rows


FEATS = ["ob_bid_half", "ob_ask_half", "ob_half", "ob_asym", "depth_usd_bid", "depth_usd_ask",
         "depth_imb_5", "depth_imb_10", "depth_imb_20", "slope_bid", "slope_ask",
         "near_share_3", "near_share_5", "gap_1",
         "imb", "ofi_1s", "ofi_5s", "ofi_30s",
         "tvol_1s", "tvol_5s", "tvol_30s", "tvol_300s", "tcnt_1s", "tcnt_5s", "tcnt_30s", "tcnt_300s",
         "sweep_30s", "big_share_30s",
         "ret_1s", "ret_5s", "ret_30s", "vol_5s", "vol_60s", "spread_bp"]


def ic_eval(rows):
    """时间序 60/40 分割 + 4 折 walk-forward（对付 train/test IC 不一致的漂移警报）。"""
    rows = sorted(rows, key=lambda r: r["t"])
    cut = int(len(rows) * 0.6)
    tr, te = rows[:cut], rows[cut:]
    # 4 折：把全集按时间切成 4 段，每段独立算 IC
    folds = []
    fn = len(rows) // 4
    for i in range(4):
        folds.append(rows[i * fn:(i + 1) * fn] if i < 3 else rows[3 * fn:])
    out = {}
    for f in FEATS:
        def series(rs, side=None):
            xs, ys = [], []
            for r in rs:
                if side is not None and r["side"] != side:
                    continue
                v = r.get(f)
                if v is None or not np.isfinite(v):
                    continue
                xs.append(v); ys.append(r["y"])
            return np.asarray(xs), np.asarray(ys)

        def ic_of(rs):
            x, y = series(rs)
            if x.size < 200 or x.std() == 0 or y.std() == 0:
                return None
            return float(np.corrcoef(x, y)[0, 1])

        xa, ya = series(tr)
        xb, yb = series(te)
        if xa.size < 200 or xb.size < 200 or xa.std() == 0 or xb.std() == 0 or ya.std() == 0 or yb.std() == 0:
            out[f] = None
            continue
        ic_tr = float(np.corrcoef(xa, ya)[0, 1])
        ic_te = float(np.corrcoef(xb, yb)[0, 1])
        fold_ics = [ic_of(fd) for fd in folds]
        fold_ics = [v for v in fold_ics if v is not None]
        sides = {}
        for s in ("bid", "ask"):
            xs, ys = series(te, s)
            sides[s] = (float(np.corrcoef(xs, ys)[0, 1])
                        if xs.size > 200 and xs.std() > 0 and ys.std() > 0 else None)
        out[f] = {
            "ic_train": ic_tr, "ic_test": ic_te,
            "fold_ics": fold_ics,
            "fold_mean": float(np.mean(fold_ics)) if fold_ics else None,
            "fold_same_sign": (len(set(np.sign(fold_ics))) == 1) if len(fold_ics) >= 3 else None,
            "ic_test_bid": sides["bid"], "ic_test_ask": sides["ask"],
            "same_sign": (sides["bid"] is not None and sides["ask"] is not None
                          and np.sign(sides["bid"]) == np.sign(sides["ask"])),
            "n_train": int(xa.size), "n_test": int(xb.size),
        }
    return out, {"n_train": len(tr), "n_test": len(te)}


def main():
    cn = pg()
    cur = cn.cursor()
    cur.execute("select max(event_ts_ms) from asterdex_depth_snapshots")
    T1 = int(cur.fetchone()[0])
    cur.execute("select min(event_ts_ms) from asterdex_depth_snapshots")
    T0 = max(int(cur.fetchone()[0]), T1 - int(HOURS * 3600 * 1000))
    cn.close()
    print(f"window {datetime.fromtimestamp(T0/1000, timezone.utc):%m-%d %H:%M} "
          f"-> {datetime.fromtimestamp(T1/1000, timezone.utc):%m-%d %H:%M} UTC")

    rep = {"generated_at": datetime.now(timezone.utc).isoformat(),
           "window_ms": [T0, T1], "hours": HOURS, "grid_ms": GRID_MS, "hold_ms": HOLD_MS,
           "label": "y=1 若平仓腿在 入场价±入场点差 于 30s 内成交",
           "features": FEATS, "symbols": {}}

    pooled = {}
    for sym in SYMBOLS:
        try:
            book, tr, dep = load_symbol(sym, T0, T1)
        except Exception as e:  # noqa: BLE001
            print(f"[{sym}] load err {e}")
            continue
        if not book or not tr or not dep:
            print(f"[{sym}] no data book={len(book)} tr={len(tr)} dep={len(dep)}")
            continue
        rows = build(sym, book, tr, dep, T0, T1)
        if len(rows) < 500:
            print(f"[{sym}] too few rows {len(rows)}")
            continue
        ics, splits = ic_eval(rows)
        rep["symbols"][sym] = {"n": len(rows), "splits": splits, "ic": ics,
                               "base_rate": float(np.mean([r["y"] for r in rows]))}
        print(f"[{sym:<10}] n={len(rows):>6} base={np.mean([r['y'] for r in rows]):.3f} "
              f"train={splits['n_train']} test={splits['n_test']}")
        for f in FEATS:
            v = ics.get(f)
            if v:
                pooled.setdefault(f, []).append(v["ic_test"])

    print("\n=== 样本外 IC（跨币平均），按 |IC_test| 排序 ===")
    print(f"{'feature':<18}{'IC_test':>10}{'IC_train':>10}{'fold均值':>10}{'折同号':>8}{'#币':>5}{'#|IC|>0.05':>11}")
    rank = []
    for f, vs in pooled.items():
        m = float(np.mean(vs))
        det = [rep['symbols'][s]['ic'][f] for s in rep['symbols'] if rep['symbols'][s]['ic'].get(f)]
        mtr = float(np.mean([d['ic_train'] for d in det]))
        fmean = float(np.mean([d['fold_mean'] for d in det if d['fold_mean'] is not None]))
        nsame = sum(1 for d in det if d['fold_same_sign'])
        nbig = sum(1 for x in vs if abs(x) > 0.05)
        rank.append((f, m, mtr, fmean, nsame, len(vs), nbig))
    for f, m, mtr, fmean, nsame, n, nbig in sorted(rank, key=lambda x: -abs(x[1])):
        flag = "  <<<" if (abs(m) >= 0.05 and abs(fmean) >= 0.05) else ""
        print(f"{f:<18}{m:>10.4f}{mtr:>10.4f}{fmean:>10.4f}{nsame:>4}/{n:<3}{n:>5}{nbig:>11}{flag}")
    rep["ranked"] = [{"feature": f, "ic_test_mean": m, "ic_train_mean": mtr,
                      "fold_mean": fmean, "n_fold_same_sign": nsame,
                      "n_sym": n, "n_sym_abs_gt_005": nbig}
                     for f, m, mtr, fmean, nsame, n, nbig in sorted(rank, key=lambda x: -abs(x[1]))]

    p = OUT / "h12_depth_features.json"
    p.write_text(json.dumps(rep, ensure_ascii=False, indent=2, cls=_Enc), encoding="utf-8")
    print(f"\nwrote {p}")


if __name__ == "__main__":
    main()
