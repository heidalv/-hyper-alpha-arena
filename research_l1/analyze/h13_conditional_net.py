"""H13 · 决定性检验：spread/gap 条件化能否把「每笔进场净额」拉正？

H12 唯一通过三重检验的两个特征：
  spread_bp  IC_test=-0.061  fold=7/9 同号
  gap_1      IC_test=+0.053  fold=6/9 同号
但它们本质是「我的平仓腿离现价有多远」的机械代理，**不是新信息**。
IC 为正 ≠ 能赚钱。本脚本做真正的判据：**按特征分档后，每笔进场的净额(bp)是否 > 0**。

严格口径（与 H10/H11 一致，便于对照）：
  进场：贴盘口挂单，队列感知成交
  出场：钉死在 入场价 ± 入场时点差（= 赚一个点差）
  超时：HOLD=30s 未成交则按 mid 盯市强平
  每笔进场净额 net_bp = 完成?(+spread) : (mid_{t+HOLD}/entry - 1)（按方向）
  指标：**每笔进场的期望净额**（而不是每笔往返——决策次数才是成本）

输出：research_l1/out/h13_conditional_net.json
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

SYMBOLS = os.environ.get(
    "H13_SYMBOLS",
    "ASTERUSDT,XRPUSDT,SOLUSDT,ETHUSDT,BTCUSDT,HYPEUSDT,ZECUSDT,ARBUSDT,ONDOUSDT,SEIUSDT").split(",")
HOURS = float(os.environ.get("H13_HOURS", "12"))
GRID_MS = 1000
HOLD_MS = 30_000
NOTIONAL = 6.0     # 交易所最小名义附近（Aster min $5）


class _Enc(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, np.integer): return int(o)
        if isinstance(o, np.floating):
            v = float(o); return v if np.isfinite(v) else None
        if isinstance(o, (np.bool_, bool)): return bool(o)
        if isinstance(o, np.ndarray): return o.tolist()
        return super().default(o)


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
    o = {"n": int(v.size), "mean": float(v.mean()),
         "usd_per_decision": float(v.mean() / 1e4 * NOTIONAL)}
    if v.size > 1:
        se = float(v.std(ddof=1)) / float(np.sqrt(v.size))
        o["se"] = se
        o["t"] = (float(v.mean() / se) if se > 0 else None)
    return o


def run_symbol(sym, t0, t1):
    cn = pg()
    cur = cn.cursor()
    cur.execute("select event_ts_ms,bid_px,bid_qty,ask_px,ask_qty from asterdex_book_ticker "
                "where symbol=%s and event_ts_ms between %s and %s order by event_ts_ms",
                (sym, t0, t1))
    book = cur.fetchall()
    cur.execute("select event_ts_ms,price,qty,is_buyer_maker from asterdex_trades "
                "where symbol=%s and event_ts_ms between %s and %s order by event_ts_ms",
                (sym, t0, t1))
    tr = cur.fetchall()
    cn.close()
    if not book or not tr:
        return None

    bts = np.array([r[0] for r in book], dtype=np.int64)
    bid = np.array([r[1] for r in book], dtype=np.float64)
    bq = np.array([r[2] for r in book], dtype=np.float64)
    ask = np.array([r[3] for r in book], dtype=np.float64)
    aq = np.array([r[4] for r in book], dtype=np.float64)
    mid = (bid + ask) / 2.0

    tts = np.array([r[0] for r in tr], dtype=np.int64)
    tpx = np.array([r[1] for r in tr], dtype=np.float64)
    tqt = np.array([r[2] for r in tr], dtype=np.float64)
    bm = np.array([bool(r[3]) for r in tr])

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

    rows = []
    pick = 0
    for gi in range(len(grid)):
        t = int(grid[gi])
        m0 = float(mid[gidx[gi]])
        bp0, ap0 = float(bid[gidx[gi]]), float(ask[gidx[gi]])
        if not (np.isfinite(m0) and m0 > 0 and bp0 > 0 and ap0 > bp0):
            continue
        sp = ap0 - bp0
        sp_bp = sp / m0 * 1e4
        # gap_1：用 top-of-book 两侧的"下一档距离"不可得；改用点差相对近期均值
        # （H12 的 gap_1 来自深度快照，这里用点差/近60s中点差的比值作等价代理，保证只用 ticker）
        j60 = max(0, gi - 60)
        sp_hist = float(np.median(((ask - bid) / mid * 1e4)[j60:gi + 1])) if gi > j60 else sp_bp
        gap_proxy = (sp_bp / sp_hist) if sp_hist > 0 else 1.0

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
        if completed:
            bp = ((exit_limit - entry_px) if side == "bid" else (entry_px - exit_limit)) / entry_px * 1e4
        else:
            mi = min(int(np.searchsorted(bts, tf + HOLD_MS, side="left")), len(bts) - 1)
            mm = float(mid[mi])
            bp = ((mm - entry_px) if side == "bid" else (entry_px - mm)) / entry_px * 1e4
        rows.append({"sp_bp": sp_bp, "gap_proxy": gap_proxy, "completed": completed,
                     "bp": bp, "side": side, "t": t, "tf": tf})
    return rows


def main():
    cn = pg()
    cur = cn.cursor()
    cur.execute("select min(event_ts_ms), max(event_ts_ms) from asterdex_book_ticker")
    lo, hi = cur.fetchone()
    cn.close()
    T1 = int(hi); T0 = max(int(lo), T1 - int(HOURS * 3600 * 1000))

    rep = {"generated_at": datetime.now(timezone.utc).isoformat(),
           "window_ms": [T0, T1], "hours": HOURS, "hold_ms": HOLD_MS,
           "notional_usd": NOTIONAL,
           "policy": "贴盘口进；出场钉死 入场价±入场点差；30s 超时按 mid 强平",
           "metric": "每笔进场期望净额(bp) 与 折算美元",
           "symbols": {}}

    allrows = []
    for sym in SYMBOLS:
        rows = run_symbol(sym, T0, T1)
        if not rows or len(rows) < 300:
            print(f"[{sym}] skip ({0 if not rows else len(rows)} rows)")
            continue
        bps = [r["bp"] for r in rows]
        cr = float(np.mean([r["completed"] for r in rows]))
        st = stats(bps)
        rep["symbols"][sym] = {
            "n_decisions": len(rows), "completion_rate": cr,
            "net_per_decision": st,
            "sp_bp_median": float(np.median([r["sp_bp"] for r in rows])),
        }
        print(f"[{sym:<10}] n={len(rows):>6} 完成率={cr:.3f} "
              f"每笔进场={st['mean']:+.3f}bp (t{st.get('t', float('nan')):+.1f}) "
              f"= ${st['usd_per_decision']:+.5f}/笔")
        for r in rows:
            r["sym"] = sym
        allrows.extend(rows)

    if not allrows:
        print("no data")
        return

    # ---- 无条件基准 ----
    base = stats([r["bp"] for r in allrows])
    print(f"\n=== 无条件基准（{len(allrows)} 笔决策，{len(rep['symbols'])} 币）===")
    print(f"  每笔进场 {base['mean']:+.3f}bp  (t{base.get('t'):+.1f})  = ${base['usd_per_decision']:+.5f}/笔")
    rep["unconditional"] = base

    # ---- 按 spread_bp 分档 ----
    xs = np.array([r["sp_bp"] for r in allrows])
    qs = np.quantile(xs, [0.2, 0.4, 0.6, 0.8])
    print(f"\n=== 按 point差(sp_bp) 五分位 ===")
    print(f"{'档':<22}{'n':>7}{'完成率':>9}{'每笔bp':>10}{'t':>7}{'$/笔':>10}")
    rep["by_spread_quintile"] = []
    for bi in range(5):
        lo_ = -1e18 if bi == 0 else qs[bi - 1]
        hi_ = 1e18 if bi == 4 else qs[bi]
        sub = [r for r in allrows if lo_ < r["sp_bp"] <= hi_]
        if not sub:
            continue
        s = stats([r["bp"] for r in sub])
        c = float(np.mean([r["completed"] for r in sub]))
        lbl = f"Q{bi+1} [{lo_ if bi else 0:.3f},{hi_ if bi<4 else xs.max():.3f}]"
        print(f"{lbl:<22}{len(sub):>7}{c:>9.3f}{s['mean']:>+10.3f}{s.get('t', float('nan')):>+7.1f}"
              f"{s['usd_per_decision']:>+10.5f}")
        rep["by_spread_quintile"].append({"quintile": bi + 1, "range": [lo_, hi_],
                                          "n": len(sub), "completion_rate": c, **s})

    # ---- 按 gap_proxy 分档 ----
    gs = np.array([r["gap_proxy"] for r in allrows])
    gq = np.quantile(gs, [0.2, 0.4, 0.6, 0.8])
    print(f"\n=== 按 gap_proxy（当前点差/近60s中点差）五分位 ===")
    print(f"{'档':<22}{'n':>7}{'完成率':>9}{'每笔bp':>10}{'t':>7}{'$/笔':>10}")
    rep["by_gap_quintile"] = []
    for bi in range(5):
        lo_ = -1e18 if bi == 0 else gq[bi - 1]
        hi_ = 1e18 if bi == 4 else gq[bi]
        sub = [r for r in allrows if lo_ < r["gap_proxy"] <= hi_]
        if not sub:
            continue
        s = stats([r["bp"] for r in sub])
        c = float(np.mean([r["completed"] for r in sub]))
        lbl = f"Q{bi+1} [{lo_ if bi else 0:.2f},{hi_ if bi<4 else gs.max():.2f}]"
        print(f"{lbl:<22}{len(sub):>7}{c:>9.3f}{s['mean']:>+10.3f}{s.get('t', float('nan')):>+7.1f}"
              f"{s['usd_per_decision']:>+10.5f}")
        rep["by_gap_quintile"].append({"quintile": bi + 1, "range": [lo_, hi_],
                                       "n": len(sub), "completion_rate": c, **s})

    # ---- 组合过滤：只在高点差 且 高gap 时进场 ----
    print(f"\n=== 组合过滤（两个特征同时高档）===")
    print(f"{'过滤':<28}{'n':>7}{'完成率':>9}{'每笔bp':>10}{'t':>7}{'$/笔':>10}")
    rep["combined_filters"] = []
    for sp_q, gp_q in ((0.6, 0.6), (0.7, 0.7), (0.8, 0.8), (0.8, 0.0), (0.0, 0.8)):
        sp_thr = float(np.quantile(xs, sp_q)) if sp_q > 0 else -1e18
        gp_thr = float(np.quantile(gs, gp_q)) if gp_q > 0 else -1e18
        sub = [r for r in allrows if r["sp_bp"] >= sp_thr and r["gap_proxy"] >= gp_thr]
        if len(sub) < 200:
            print(f"sp>={sp_q:.0%} gap>={gp_q:.0%}: n={len(sub)} 太少，跳过")
            continue
        s = stats([r["bp"] for r in sub])
        c = float(np.mean([r["completed"] for r in sub]))
        lbl = f"sp>={sp_q:.0%} & gap>={gp_q:.0%}"
        print(f"{lbl:<28}{len(sub):>7}{c:>9.3f}{s['mean']:>+10.3f}{s.get('t', float('nan')):>+7.1f}"
              f"{s['usd_per_decision']:>+10.5f}")
        rep["combined_filters"].append({"sp_q": sp_q, "gap_q": gp_q, "n": len(sub),
                                        "completion_rate": c, **s})

    p = OUT / "h13_conditional_net.json"
    p.write_text(json.dumps(rep, ensure_ascii=False, indent=2, cls=_Enc), encoding="utf-8")
    print(f"\nwrote {p}")


if __name__ == "__main__":
    main()
