"""H61：左尾才是主要矛盾 —— 量化"赢 73% 却亏钱"的结构。

# 触发本脚本的实测（H60b，实盘真实账本逐笔）

| | 修前（111 笔） | 修后（150 笔） |
|---|---|---|
| 均值 | **−4.1992bp** | **−2.3205bp** |
| **中位** | **+1.2561bp** | **+0.4106bp** |
| 胜率 | 0.730 | 0.787 |
| 最小 | **−99.8749bp** | −56.7713bp |
| 最大 | +4.8784bp | +27.3509bp |
| p5 | −37.38 | −29.02 |
| **最亏 5 笔占总亏损** | **70.9%** | **71.5%** |

**⇒ 胜率 73~79%、中位为正，但均值为负。亏损**不是**来自"每笔小幅逆选择"，
而是来自极少数巨亏笔（−100bp，而最大盈利只有 +4.9bp）—— 不对称约 20:1。**

这推翻了我此前"挂宽 → 逆选择 → 均值漂移"的单一框架：
**那个框架只解释均值，解释不了尾部；而尾部贡献 >70% 的亏损。**

# 本脚本测什么（用真实 tick，不用实盘账本 —— 样本量才够）

1. **尾部占比**：最亏 k% 的成交贡献了多少总亏损？（k = 1%, 2%, 5%, 10%）
2. **尾部是不是"事件"**：最亏成交是否在时间上聚集？（若聚集 ⇒ 是行情事件，
   不是个股特性 ⇒ 应对方式是"事件期间不挂单"而非"调宽度"）
3. **尾部 vs 持有时长**：尾部的绝对幅度是否随 hold_s 增长？（若增长 ⇒
   长持有是尾部放大器 ⇒ 印证 `max_one_side_seconds=300` 是风险源）
4. **尾部 vs 挂宽**：宽挂单的尾部是否更肥？（宽 ⇒ 只有大行情才打到 ⇒ 打到就是大行情）

# 判据（事先定死）

  · 若最亏 5% 贡献 > 60% 总亏损 ⇒ **必须做尾部控制**（止损/事件回避），
    单纯调挂宽或加择时闸都不够
  · 若尾部随时间聚集 ⇒ 做"事件闸"；若分散 ⇒ 做"每笔止损"
  · 若尾部随 hold_s 显著增长 ⇒ **缩短持有上限是直接有效的尾部控制**

用法：
    .venv\\Scripts\\python.exe scripts\\h61_left_tail_structure.py --hours 24
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

OUT = ROOT / "research_l1" / "out" / "h61_left_tail.json"
STEP_MS = 15_000
DEFAULT_SYMS = "BTC,ETH,SOL,XRP,DOGE,BNB,SEI,VIRTUAL,PENDLE,1000SHIB"


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def collect(sym, hours, spread_mult, maker_fee_bp, hold_s, cross_margin):
    """收集每个决策点的净额（买单视角），返回 (net_bp, grid_ts)。"""
    import numpy as np
    import psycopg2
    import psycopg2.extras

    vs = sym if sym.endswith("USDT") else f"{sym}USDT"
    cn = psycopg2.connect(_dsn("alpha_market"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    since = f"(extract(epoch from now())*1000)::bigint - {int(hours * 3600_000)}"

    cur.execute(
        "SELECT event_ts_ms, bid_px::float b, ask_px::float a"
        f"  FROM asterdex_book_ticker WHERE symbol=%s AND event_ts_ms > {since}"
        "  ORDER BY event_ts_ms", (vs,))
    ob = cur.fetchall()
    cur.execute(
        "SELECT event_ts_ms, price::float p, is_buyer_maker"
        f"  FROM asterdex_trades WHERE symbol=%s AND event_ts_ms > {since}"
        "  ORDER BY event_ts_ms", (vs,))
    tr = cur.fetchall()
    cn.close()
    if len(ob) < 2000 or len(tr) < 200:
        return None

    ot = np.array([r["event_ts_ms"] for r in ob], dtype=np.int64)
    ob_ = np.array([r["b"] for r in ob]); oa = np.array([r["a"] for r in ob])
    ok = (ob_ > 0) & (oa > ob_)
    ot, ob_, oa = ot[ok], ob_[ok], oa[ok]
    mid = 0.5 * (ob_ + oa)

    tt = np.array([r["event_ts_ms"] for r in tr], dtype=np.int64)
    tp = np.array([r["p"] for r in tr])
    tbm = np.array([bool(r["is_buyer_maker"]) for r in tr])
    v = tp > 0
    tt, tp, tbm = tt[v], tp[v], tbm[v]

    t0, t1 = int(ot[0]), int(ot[-1])
    grids = np.arange(t0, t1 - int(hold_s * 1000), STEP_MS)
    gi = np.searchsorted(ot, grids, side="right") - 1
    g = gi >= 0
    grids, gi = grids[g], gi[g]
    mb, ma = ob_[gi], oa[gi]
    mid_g = 0.5 * (mb + ma)
    half_g = 0.5 * (ma - mb)
    w = np.maximum(float(spread_mult) * half_g, 1e-12)
    px_bid = np.minimum(np.minimum(mid_g - w, ma - cross_margin * (ma - mb)), mid_g)
    fi = np.searchsorted(ot, grids + int(hold_s * 1000), side="right") - 1
    fi = np.clip(fi, 0, len(ot) - 1)
    fmid = 0.5 * (ob_[fi] + oa[fi])

    nets, gts, hws = [], [], []
    for k in range(len(grids)):
        lo = grids[k]; hi = lo + int(hold_s * 1000)
        i0 = np.searchsorted(tt, lo, side="left")
        i1 = np.searchsorted(tt, hi, side="left")
        if i1 <= i0:
            continue
        seg_p = tp[i0:i1]; seg_bm = tbm[i0:i1]
        if not (seg_bm & (seg_p <= px_bid[k])).any():
            continue
        nets.append((fmid[k] - px_bid[k]) / mid_g[k] * 1e4 + maker_fee_bp)
        gts.append(grids[k])
        hws.append(half_g[k] / mid_g[k] * 1e4)
    if not nets:
        return None
    return {"symbol": vs, "net": np.array(nets), "ts": np.array(gts),
            "half_bp": np.array(hws)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--symbols", default=DEFAULT_SYMS)
    ap.add_argument("--spread-mult", type=float, default=0.9)
    ap.add_argument("--cross-margin", type=float, default=0.05)
    ap.add_argument("--maker-fee-bp", type=float, default=0.0)
    ap.add_argument("--hold-s", type=float, default=30.0)
    a = ap.parse_args()

    import numpy as np

    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    print("=" * 104)
    print("H61  左尾结构 —— 为什么「胜率 75% 却在亏钱」")
    print("=" * 104)
    print(f"  窗口 {a.hours:.0f}h  ·  hold {a.hold_s:.0f}s  ·  spread_mult {a.spread_mult}"
          f"  ·  maker {a.maker_fee_bp:+.2f}bp")

    data = {}
    for s in syms:
        d = collect(s, a.hours, a.spread_mult, a.maker_fee_bp, a.hold_s, a.cross_margin)
        if d:
            data[d["symbol"]] = d
    if not data:
        print("无数据")
        return 1

    allnet = np.concatenate([d["net"] for d in data.values()])
    allts = np.concatenate([d["ts"] for d in data.values()])
    allhalf = np.concatenate([d["half_bp"] for d in data.values()])
    tot = allnet.sum()

    print(f"\n  总成交 {len(allnet):,} 笔   合计 {tot:>+12.2f}bp")
    print(f"  均值 {allnet.mean():>+9.4f}bp   中位 {np.median(allnet):>+9.4f}bp"
          f"   胜率 {(allnet>0).mean():.4f}")
    print(f"  最小 {allnet.min():>+10.4f}bp   最大 {allnet.max():>+10.4f}bp"
          f"   不对称比 {abs(allnet.min())/max(allnet.max(),1e-9):>7.1f}:1")

    # ── 1) 尾部分位贡献 ──
    print("\n" + "=" * 104)
    print("1) 左尾贡献（最亏的 k% 占总亏损的比例）")
    print("=" * 104)
    order = np.argsort(allnet)
    print(f"\n  {'最亏 k%':>9} {'笔数':>8} {'该组合计bp':>14} {'占总计%':>10} "
          f"{'该组均值bp':>12} {'该组最大单笔':>13}")
    print("  " + "-" * 74)
    tail = {}
    for pct in (0.5, 1, 2, 5, 10, 20):
        k = max(1, int(len(allnet) * pct / 100.0))
        sel = allnet[order[:k]]
        frac = sel.sum() / tot * 100 if tot != 0 else float("nan")
        tail[pct] = {"k": k, "sum_bp": round(float(sel.sum()), 4),
                     "share_pct": round(float(frac), 2),
                     "mean_bp": round(float(sel.mean()), 4),
                     "worst_bp": round(float(sel.min()), 4)}
        print(f"  {pct:>8.1f}% {k:>8,} {sel.sum():>+14.2f} {frac:>9.1f}% "
              f"{sel.mean():>+12.4f} {sel.min():>+13.4f}")

    # ── 2) 尾部是不是"事件"（时间聚集）──
    print("\n" + "=" * 104)
    print("2) 尾部是否在时间上聚集（聚集 ⇒ 行情事件 ⇒ 做事件闸；分散 ⇒ 做每笔止损）")
    print("=" * 104)
    k5 = max(1, int(len(allnet) * 0.05))
    worst = np.sort(allts[order[:k5]])
    if len(worst) > 1:
        gaps_min = np.diff(worst) / 60000.0
        span_h = (allts.max() - allts.min()) / 3600000.0
        # 随机基准：k5 个点均匀撒在 span 内的期望间隔
        exp_gap = (span_h * 60.0) / max(k5, 1)
        print(f"\n  最亏 5% 共 {k5:,} 笔，跨度 {span_h:.2f}h")
        print(f"    实际相邻间隔 中位 {np.median(gaps_min):>8.2f} 分   "
              f"p25 {np.percentile(gaps_min,25):>8.2f}   p75 {np.percentile(gaps_min,75):>8.2f}")
        print(f"    若均匀分布，期望间隔 {exp_gap:>8.2f} 分")
        # 聚集度：间隔 < 1 分钟的对数占比
        tight = (gaps_min < 1.0).mean()
        print(f"    间隔 < 1 分钟的比例 {tight*100:.1f}%  "
              f"（均匀分布下应约 {min(100.0, 100.0*1.0/max(exp_gap,1e-9)):.1f}%）")
        if tight > 0.3:
            print("    ⇒ **显著聚集** ⇒ 是行情事件，优先做「事件期间不挂单」")
        else:
            print("    ⇒ 分散 ⇒ 优先做「每笔止损/缩短持有」")

    # ── 3) 尾部幅度 vs 持有时长 ──
    print("\n" + "=" * 104)
    print("3) 持有时间对尾部的影响（hold 越长，尾部越肥？）")
    print("=" * 104)
    print(f"\n  {'hold_s':>8} {'均值bp':>10} {'中位':>9} {'p5':>9} {'p1':>9} "
          f"{'最小':>10} {'胜率':>8} {'样本':>9}")
    print("  " + "-" * 76)
    hold_curve = {}
    for hs in (5, 15, 30, 60, 120, 300):
        nets = []
        for s, d in data.items():
            dd = collect(s, a.hours, a.spread_mult, a.maker_fee_bp, hs, a.cross_margin)
            if dd:
                nets.append(dd["net"])
        if not nets:
            continue
        x = np.concatenate(nets)
        hold_curve[hs] = {"mean": round(float(x.mean()), 4),
                          "p1": round(float(np.percentile(x, 1)), 4),
                          "min": round(float(x.min()), 4),
                          "n": int(len(x))}
        print(f"  {hs:>8} {x.mean():>+10.4f} {np.median(x):>+9.4f} "
              f"{np.percentile(x,5):>+9.4f} {np.percentile(x,1):>+9.4f} "
              f"{x.min():>+10.4f} {(x>0).mean():>8.4f} {len(x):>9,}")

    # ── 4) 尾部 vs 挂宽 ──
    print("\n" + "=" * 104)
    print("4) 尾部 vs 挂宽（宽挂单是否尾部更肥？）")
    print("=" * 104)
    qs = np.percentile(allhalf, [20, 40, 60, 80])
    print(f"\n  半价差分位: {[round(float(q),4) for q in qs]}")
    print(f"\n  {'半价差区间':>22} {'n':>8} {'均值bp':>10} {'中位':>9} {'p1':>9} {'最小':>10}")
    print("  " + "-" * 72)
    bins = [(-np.inf, qs[0]), (qs[0], qs[1]), (qs[1], qs[2]), (qs[2], qs[3]),
            (qs[3], np.inf)]
    for lo, hi in bins:
        m = (allhalf >= lo) & (allhalf < hi)
        if m.sum() < 10:
            continue
        x = allnet[m]
        print(f"  {f'[{lo:.4f}, {hi:.4f})':>22} {m.sum():>8,} {x.mean():>+10.4f} "
              f"{np.median(x):>+9.4f} {np.percentile(x,1):>+9.4f} {x.min():>+10.4f}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"hours": a.hours, "hold_s": a.hold_s,
                               "n": int(len(allnet)), "total_bp": round(float(tot), 4),
                               "mean": round(float(allnet.mean()), 4),
                               "median": round(float(np.median(allnet)), 4),
                               "win": round(float((allnet > 0).mean()), 4),
                               "tail": tail, "hold_curve": hold_curve},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[H61] 写出 {OUT}")
    print("\n判读：最亏 5% 贡献 >60% ⇒ 必须做尾部控制，单靠调宽度/择时闸不够。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
