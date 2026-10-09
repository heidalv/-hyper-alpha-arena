"""H56：在**亏损窗口**上对拍「绝对 bp 挂宽」vs「价差相对挂宽」—— 上线前的反事实验证。

# 为什么必须先做这一步

实盘车道当前 **equity=285.50 / 300（−4.8%）**，已成交 1596 笔。
H55 给出了机械根因：挂宽是绝对 bp，而各币价差跨度 1629 倍 ⇒
同一宽度在 BTC 上是 230 倍半价差（打不到），在 SEI 上是 0.2 倍（被逆选择）。

**但"根因看起来对"不等于"改了就好"。** 本项目已经犯过 19 次口径错误，
其中至少 3 次是"以为修好了、其实改错了方向"（H36 符号错把 Binance 排第一、
H39/H42 两次排除不完全样本、H52 δ 单位错）。
⇒ **先在同一段真实数据上把两种挂宽口径对拍，再决定是否上线。**

# 对拍设计（同一份数据、同一套成交判定、只改挂宽口径）

对每个币、每个 15s 决策点：
  · **旧口径**：w = max(min_width_bp, w_base_bp)（绝对 bp，与引擎一致）
  · **新口径**：w = spread_mult × 半价差（F280），并用**真实盘口**做不穿越钳制

成交判定（**两个口径完全相同**，避免判定差异污染对比）：
  · 买单成交 ⟺ 该段内有主动卖成交价 ≤ 我们的买价（H54 修正后的物理判据）
  · 卖单成交 ⟺ 该段内有主动买成交价 ≥ 我们的卖价
  · **若报价穿越了对侧最优价 ⇒ 该样本作废并计数**（不掩盖穿越，H54 的教训）
退出：持有 hold_s 后按**当段 mid** 平仓（不用成交价，避免价差污染）
净额 = (平仓 mid − 开仓报价)/mid×1e4 + maker_fee（双边按腿）

# 判据（事先定死）

  · 若新口径在**同一窗口**上的净额均值 > 旧口径 ⇒ 上线（并给出建议 spread_mult）
  · 若新口径 ≤ 旧口径 ⇒ **不上线**，说明价差相对化不是本窗口亏损的主因，继续查
  · 任何情况下同时报：成交率、**穿越率**、均值/中位/p5/胜率（第 15/16 条教训）

用法：
    .venv\\Scripts\\python.exe scripts\\h56_width_mode_counterfactual.py --hours 8
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

OUT = ROOT / "research_l1" / "out" / "h56_width_counterfactual.json"
STEP_MS = 15_000
SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "BNBUSDT",
        "SEIUSDT", "VIRTUALUSDT", "PENDLEUSDT", "1000SHIBUSDT"]


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def _st(x):
    import numpy as np

    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) == 0:
        return {"n": 0}
    return {"n": int(len(x)), "mean": round(float(x.mean()), 4),
            "median": round(float(np.median(x)), 4),
            "p5": round(float(np.percentile(x, 5)), 4),
            "win": round(float((x > 0).mean()), 4)}


def run_symbol(sym, hours, mode, w_abs_bp, spread_mult, maker_fee_bp, hold_s):
    import numpy as np
    import psycopg2
    import psycopg2.extras

    cn = psycopg2.connect(_dsn("alpha_market"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    since = f"(extract(epoch from now())*1000)::bigint - {int(hours * 3600_000)}"

    cur.execute(
        "SELECT event_ts_ms, bid_px::float b, ask_px::float a"
        f"  FROM asterdex_book_ticker WHERE symbol=%s AND event_ts_ms > {since}"
        "  ORDER BY event_ts_ms", (sym,))
    ob = cur.fetchall()
    cur.execute(
        "SELECT event_ts_ms, price::float p, qty::float q, is_buyer_maker"
        f"  FROM asterdex_trades WHERE symbol=%s AND event_ts_ms > {since}"
        "  ORDER BY event_ts_ms", (sym,))
    tr = cur.fetchall()
    cn.close()
    if len(ob) < 2000 or len(tr) < 200:
        return None

    ot = np.array([r["event_ts_ms"] for r in ob], dtype=np.int64)
    ob_ = np.array([r["b"] for r in ob]); oa = np.array([r["a"] for r in ob])
    ok = (ob_ > 0) & (oa > ob_)
    ot, ob_, oa = ot[ok], ob_[ok], oa[ok]
    tt = np.array([r["event_ts_ms"] for r in tr], dtype=np.int64)
    tp = np.array([r["p"] for r in tr]); tq = np.array([r["q"] for r in tr])
    tbm = np.array([bool(r["is_buyer_maker"]) for r in tr])
    v = (tp > 0) & (tq > 0)
    tt, tp, tq, tbm = tt[v], tp[v], tq[v], tbm[v]

    # 按 15s 网格决策
    t0 = int(ot[0]); t1 = int(ot[-1])
    grids = np.arange(t0, t1 - int(hold_s * 1000), STEP_MS)
    if len(grids) < 10:
        return None

    # 每个网格点的最近盘口（严格因果）
    gi = np.searchsorted(ot, grids, side="right") - 1
    g = gi >= 0
    grids, gi = grids[g], gi[g]
    mb = ob_[gi]; ma = oa[gi]
    mid = 0.5 * (mb + ma)
    half = 0.5 * (ma - mb)
    spread_bp = (ma - mb) / mid * 1e4

    if mode == "abs":
        w = np.full(len(mid), float(w_abs_bp))
    else:
        w = float(spread_mult) * half / mid * 1e4   # 以 bp 表示的半价差倍数

    px_bid = mid * (1.0 - w / 1e4)
    px_ask = mid * (1.0 + w / 1e4)
    # 不穿越钳制（两个口径**都**施加，保证只比宽度、不比钳制）
    cross_bid = px_bid > ma
    cross_ask = px_ask < mb
    px_bid = np.minimum(px_bid, ma)
    px_ask = np.maximum(px_ask, mb)
    px_bid = np.minimum(px_bid, mid)
    px_ask = np.maximum(px_ask, mid)

    # 平仓 mid（hold_s 后）
    fi = np.searchsorted(ot, grids + int(hold_s * 1000), side="right") - 1
    fi = np.clip(fi, 0, len(ot) - 1)
    fmid = 0.5 * (ob_[fi] + oa[fi])

    nets = []
    n_cross = 0
    n_fill = 0
    for k in range(len(grids)):
        lo = grids[k]; hi = lo + int(hold_s * 1000)
        i0 = np.searchsorted(tt, lo, side="left")
        i1 = np.searchsorted(tt, hi, side="left")
        if i1 <= i0:
            continue
        seg_p = tp[i0:i1]; seg_bm = tbm[i0:i1]
        # 买单：主动卖（is_buyer_maker=True）成交价 <= 我们的买价
        sell_hit = seg_bm & (seg_p <= px_bid[k])
        # 卖单：主动买（is_buyer_maker=False）成交价 >= 我们的卖价
        buy_hit = (~seg_bm) & (seg_p >= px_ask[k])
        if not sell_hit.any() and not buy_hit.any():
            continue
        n_fill += 1
        if cross_bid[k] or cross_ask[k]:
            n_cross += 1
        # 买腿优先（与 H52 一致：单边先成交即建仓）
        if sell_hit.any():
            # 开仓买在 px_bid，平仓卖在 fmid
            nets.append((fmid[k] - px_bid[k]) / mid[k] * 1e4 + maker_fee_bp)
        else:
            nets.append((px_ask[k] - fmid[k]) / mid[k] * 1e4 + maker_fee_bp)

    if not nets:
        return None
    return {"symbol": sym, "decisions": int(len(grids)), "fills": int(n_fill),
            "fill_rate": round(n_fill / len(grids), 4),
            "cross_rate": round(n_cross / max(n_fill, 1), 4),
            "spread_bp_median": round(float(np.median(spread_bp)), 4),
            "net": _st(nets)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=8.0)
    ap.add_argument("--symbols", default=",".join(SYMS))
    ap.add_argument("--w-abs-bp", type=float, default=1.425, help="实盘 avg_width_bp")
    ap.add_argument("--spread-mult", type=float, default=0.9)
    ap.add_argument("--maker-fee-bp", type=float, default=0.0)
    ap.add_argument("--hold-s", type=float, default=30.0)
    a = ap.parse_args()

    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    print("=" * 104)
    print("H56  反事实对拍：绝对 bp 挂宽（实盘现状） vs 价差相对挂宽（F280）")
    print("=" * 104)
    print(f"  窗口 {a.hours:.0f}h  ·  hold {a.hold_s:.0f}s  ·  maker {a.maker_fee_bp:+.2f}bp")
    print(f"  旧口径 w = {a.w_abs_bp}bp（实盘 status avg_width_bp）"
          f"   新口径 w = {a.spread_mult} × 半价差")
    print(f"\n{'币':<14} {'价差p50':>9} {'口径':>7} {'决策':>7} {'成交率':>7} "
          f"{'穿越率':>7} {'净额均值':>10} {'中位':>9} {'p5':>9} {'胜率':>7}")
    print("-" * 104)

    agg = defaultdict(list)
    for s in syms:
        for mode, lab in (("abs", "旧abs"), ("rel", "新rel")):
            d = run_symbol(s, a.hours, mode, a.w_abs_bp, a.spread_mult,
                           a.maker_fee_bp, a.hold_s)
            if not d or not d["net"].get("n"):
                continue
            agg[lab].extend([d["net"]["mean"]] * 1)
            st = d["net"]
            print(f"{d['symbol']:<14} {d['spread_bp_median']:>9.4f} {lab:>7} "
                  f"{d['decisions']:>7,} {d['fill_rate']*100:>6.1f}% "
                  f"{d['cross_rate']*100:>6.1f}% {st['mean']:>10.4f} {st['median']:>9.4f} "
                  f"{st['p5']:>9.4f} {st['win']*100:>6.1f}%")
        print()

    print("=" * 104)
    print("汇总（按币等权 —— 只用于方向判读，不是金额加权）")
    print("=" * 104)
    for lab, v in agg.items():
        m = sum(v) / len(v) if v else float("nan")
        pos = sum(1 for x in v if x > 0)
        print(f"  {lab:<8} 币数 {len(v):>2}  跨币均值 {m:>+9.4f}bp  "
              f"为正的币 {pos}/{len(v)}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"hours": a.hours, "w_abs_bp": a.w_abs_bp,
                               "spread_mult": a.spread_mult,
                               "maker_fee_bp": a.maker_fee_bp,
                               "hold_s": a.hold_s}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\n[H56] 写出 {OUT}")
    print("\n判读：新口径跨币均值 > 旧口径 ⇒ 上线 F280；否则不上线，继续查根因。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
