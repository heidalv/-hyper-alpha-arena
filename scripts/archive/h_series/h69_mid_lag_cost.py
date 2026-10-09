"""H69：把「引擎只能看到 15 秒前的 mid」这个约束加进模型 —— 我给引擎的模拟一直在放水。

# 为什么这个必须做

H68 实测（确定性的，不是推断）：
  · `market_orderbook_snapshots` = 引擎 mid 的唯一来源，**间隔中位 15,000ms**
  · `asterdex_book_ticker` = 实时源，**间隔中位 8~37ms**（快 **1,071 倍**）
  · tick 本身 0.26s 一次，但每次都重读**同一行 15 秒前的快照**

而 H56 / H61 / H64 的模拟里，**我用的都是"决策时刻最新的 mid"**
⇒ **那些模拟比我实际部署的引擎乐观**，乐观量就是这 15 秒。
这是本项目第 23 条口径错误的候选：**模拟的输入与实盘的输入不同源**。

# 本脚本做什么

同一套 tick 数据，跑两遍**完全相同**的策略（F280 挂宽 + 主动卖打到即成交 + hold 30s）：
  · **A 理想**：决策用"当前时刻"的 mid（= 我此前所有模拟）
  · **B 实盘同构**：决策用"**滞后 lag_s 秒**"的 mid（= 引擎真实可见的信息）

然后报两者的差 —— 那就是这 15 秒滞后的**直接经济代价**。

同时扫 lag ∈ {0, 1, 3, 7.5, 15, 30, 60}s，给出滞后-代价曲线。

# 判据（事先定死）

  · 若 lag=15s 的均值显著差于 lag=0 ⇒ **修数据源是最高优先级**（比任何策略参数都值钱）
  · 若差异很小 ⇒ 滞后不是主要矛盾，别改数据链路（改动大、风险高）
  · 若 lag 曲线**非单调** ⇒ 说明滞后通过某种机制反而有益，需查清再动

用法：
    .venv\\Scripts\\python.exe scripts\\h69_mid_lag_cost.py --hours 24
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

OUT = ROOT / "research_l1" / "out" / "h69_mid_lag_cost.json"
STEP_MS = 15_000
DEFAULT_SYMS = "BTC,ETH,SOL,XRP,DOGE,BNB"


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def run_symbol(sym, hours, spread_mult, cross_margin, maker_fee_bp, hold_s, lags_ms):
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
    if len(ob) < 3000 or len(tr) < 200:
        return None

    ot = np.array([r["event_ts_ms"] for r in ob], dtype=np.int64)
    ob_ = np.array([r["b"] for r in ob]); oa = np.array([r["a"] for r in ob])
    ok = (ob_ > 0) & (oa > ob_)
    ot, ob_, oa = ot[ok], ob_[ok], oa[ok]
    mid = 0.5 * (ob_ + oa)
    half = 0.5 * (oa - ob_)

    tt = np.array([r["event_ts_ms"] for r in tr], dtype=np.int64)
    tp = np.array([r["p"] for r in tr])
    tbm = np.array([bool(r["is_buyer_maker"]) for r in tr])
    v = tp > 0
    tt, tp, tbm = tt[v], tp[v], tbm[v]

    t0, t1 = int(ot[0]), int(ot[-1])
    grids = np.arange(t0 + 3600_000, t1 - int(hold_s * 1000), STEP_MS)
    if len(grids) < 20:
        return None

    out = {}
    for lag in lags_ms:
        # **决策用滞后 mid**：引擎在 grids[k] 时刻只能看到 grids[k]-lag 的盘口
        di = np.searchsorted(ot, grids - lag, side="right") - 1
        g = di >= 0
        if g.sum() < 20:
            continue
        gg, dii = grids[g], di[g]
        mb, ma = ob_[dii], oa[dii]
        mid_d = 0.5 * (mb + ma)
        half_d = 0.5 * (ma - mb)
        w = np.maximum(float(spread_mult) * half_d, 1e-12)
        px_bid = np.minimum(np.minimum(mid_d - w, ma - cross_margin * (ma - mb)), mid_d)
        # 成交判定用**真实成交流**（成交流是事件流，与 lag 无关）
        # 出场：hold_s 后按**真实 mid** 平（引擎平仓时也是一样的滞后，但取保守：用真实）
        fi = np.searchsorted(ot, gg + int(hold_s * 1000), side="right") - 1
        fi = np.clip(fi, 0, len(ot) - 1)
        fmid = mid[fi]
        nets = []
        for k in range(len(gg)):
            lo = gg[k]; hi = lo + int(hold_s * 1000)
            i0 = np.searchsorted(tt, lo, side="left")
            i1 = np.searchsorted(tt, hi, side="left")
            if i1 <= i0:
                continue
            seg_p = tp[i0:i1]; seg_bm = tbm[i0:i1]
            if not (seg_bm & (seg_p <= px_bid[k])).any():
                continue
            nets.append((fmid[k] - px_bid[k]) / mid_d[k] * 1e4 + maker_fee_bp)
        if nets:
            out[lag] = np.array(nets)
    return {"symbol": vs, "res": out}


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

    lags = [0, 1000, 3000, 7500, 15000, 30000, 60000]
    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    print("=" * 108)
    print("H69  「引擎只能看到 15 秒前的 mid」的代价 —— 我此前的模拟一直在放水")
    print("=" * 108)
    print(f"  窗口 {a.hours:.0f}h  ·  spread_mult {a.spread_mult}  ·  hold {a.hold_s:.0f}s"
          f"  ·  maker {a.maker_fee_bp:+.2f}bp")
    print(f"  H68 实测：引擎 mid 源间隔 **15,000ms**；实时源 8~37ms（快 1071 倍）")

    per = {}
    for s in syms:
        d = run_symbol(s, a.hours, a.spread_mult, a.cross_margin,
                       a.maker_fee_bp, a.hold_s, lags)
        if d and d["res"]:
            per[d["symbol"]] = d
    if not per:
        print("无数据")
        return 1

    print("\n" + "=" * 108)
    print("滞后-代价曲线（跨币等权）")
    print("=" * 108)
    print(f"\n  {'滞后':>8} {'币数':>5} {'均值bp':>10} {'中位':>9} {'p1':>9} "
          f"{'最亏':>10} {'胜率':>7} {'成交数':>10}")
    print("  " + "-" * 82)
    curve = {}
    for lag in lags:
        vs = [per[s]["res"][lag] for s in per if lag in per[s]["res"]]
        if not vs:
            continue
        m = float(np.mean([x.mean() for x in vs]))
        md = float(np.mean([np.median(x) for x in vs]))
        p1 = float(np.mean([np.percentile(x, 1) for x in vs]))
        wst = float(np.mean([x.min() for x in vs]))
        w = float(np.mean([(x > 0).mean() for x in vs]))
        n = int(sum(len(x) for x in vs))
        curve[lag] = {"mean": round(m, 4), "median": round(md, 4), "p1": round(p1, 4),
                      "worst": round(wst, 4), "win": round(w, 4), "n": n,
                      "n_symbols": len(vs)}
        print(f"  {f'{lag/1000:.1f}s':>8} {len(vs):>5} {m:>+10.4f} {md:>+9.4f} "
              f"{p1:>+9.3f} {wst:>+10.3f} {w:>7.4f} {n:>10,}")

    print("\n" + "=" * 108)
    print("判据")
    print("=" * 108)
    if 0 in curve and 15000 in curve:
        d15 = curve[15000]["mean"] - curve[0]["mean"]
        print(f"\n  lag=0（理想，= 我此前的全部模拟）：均值 {curve[0]['mean']:+.4f}bp")
        print(f"  lag=15s（实盘同构，= 引擎真实可见）：均值 {curve[15000]['mean']:+.4f}bp")
        print(f"\n  ⇒ **15 秒滞后的代价 = {d15:+.4f}bp/笔**")
        if abs(d15) > 0.3:
            print(f"  ⇒ 代价显著（|{d15:.3f}| > 0.3bp）⇒ **修数据源优先级高于任何策略参数**")
        elif abs(d15) > 0.1:
            print(f"  ⇒ 代价中等 ⇒ 值得修，但不是第一优先")
        else:
            print(f"  ⇒ 代价很小 ⇒ **滞后不是主要矛盾，不要改数据链路**（改动大、风险高）")
        if d15 < 0:
            print("  ⇒ 方向为负：滞后**有害** ⇒ 与 H67 的买卖方向偏差签名一致 ✓")
        else:
            print("  ⇒ 方向为正：滞后**反而有益**（可能是被动成分）⇒ 需查清再动 ✗")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"hours": a.hours, "spread_mult": a.spread_mult,
                               "hold_s": a.hold_s, "lags_ms": lags,
                               "curve": {str(k): v for k, v in curve.items()}},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[H69] 写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
