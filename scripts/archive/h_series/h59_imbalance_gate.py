"""H59：失衡择时闸 —— 在修正后的挂宽之上，加"什么时候挂"的闸门。

# 为什么这是现在最该做的一件事

报告 §6 第 1 项。三条独立证据指向同一个变量：

1. **Albers et al. arXiv:2502.18625v2 §5 Table 1**（Binance BTCUSDT 永续**实盘**）：
   队首↔队尾落差 **0.72~1.16bp**，且**最优格**是「大 near / 小 opp」——
   QP≈0 时漂移仅 **−0.058bp**（几乎为零）。
2. **同论文 §4**：成交概率由**近侧队列规模（p=0.000）与失衡（p=0.001）**决定，
   4 参数 OLS **R²=0.946**；且 **>90% 成交概率出现在"近侧空、对侧大"的不利失衡下**
   ⇒ **"高成交概率"与"好漂移"在同一变量上对立**，必须显式择时。
3. **H53 在我们自己场地上实测**（58,162 笔主动卖成交，10s 视界）：

   | 象限 | n | 漂移均值 | 胜率 |
   |---|---|---|---|
   | 大 near / 小 opp | 13,286 | **+0.0510** | 0.450 |
   | 大 near / 大 opp | 15,813 | −0.1154 | 0.418 |
   | 小 near / 小 opp | 15,792 | −0.2456 | 0.320 |
   | 小 near / 大 opp | 13,271 | **−0.5621** | 0.320 |

   **跨度 0.61bp** —— 而修后策略仍差 0.78bp。**这个跨度足以覆盖缺口。**

# 一个反直觉但关键的细节

Albers 的「大 near / 小 opp」**不是**看失衡方向，而是看**队列绝对规模相对自身历史**：
  · 「大 near」= 我们所在侧（bid）的队列规模相对**它自己的历史**是大的
  · 「小 opp」= 对侧的绝对规模相对**它自己的历史**是小的

两个都是**比率**判据（相对自身分布），**不是符号判据**。
若误把"大 near"理解成"bid 量 > ask 量"（失衡为正），方向就**反了**。

# 闸门族（4 个，逐一测，事先定死判据）

  G0 无条件（= 当前实盘，对照基线）
  G1 失衡方向：买单要求 imb <= 0（对侧不重）
  G2 对侧轻：买单要求 ask_q < 自身中位
  G3 对侧轻 + near 充实：ask_q < 自身 p40 且 bid_q > 自身 p40   ← 最接近 Albers 的最优格
  G4 对侧轻 + 本侧薄（配合 spread_mult=0.9 进价差内，本侧薄才拿得到队首）
       ask_q < 自身 p40 且 bid_q < 自身 p50

# 口径纪律

  · 挂宽**固定用 F280 上线值**（spread_mult=0.9 + 不穿越钳制），
    这样测的是**闸门的增量**，不是挂宽的增量
  · 成交判定与 H56 一致（主动卖价 ≤ 我方买价）
  · 同时报**决策覆盖数**（闸门会减少挂单数 ⇒ 必须看"每单位时间收益"而非只看单笔）
  · **不做任何样本排除**；报均值/中位/p5/胜率

用法：
    .venv\\Scripts\\python.exe scripts\\h59_imbalance_gate.py --hours 24
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

OUT = ROOT / "research_l1" / "out" / "h59_imbalance_gate.json"
STEP_MS = 15_000
DEFAULT_SYMS = "BTC,ETH,SOL,XRP,DOGE,BNB,SEI,VIRTUAL,PENDLE,1000SHIB"


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


def run_symbol(sym, hours, spread_mult, maker_fee_bp, hold_s, cross_margin):
    """返回每个 (闸门, 方向) 的净额样本。买单与卖单分别测（符号是对称的）。"""
    import numpy as np
    import psycopg2
    import psycopg2.extras

    vs = sym if sym.endswith("USDT") else f"{sym}USDT"
    cn = psycopg2.connect(_dsn("alpha_market"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    since = f"(extract(epoch from now())*1000)::bigint - {int(hours * 3600_000)}"

    cur.execute(
        "SELECT event_ts_ms, bid_px::float b, ask_px::float a,"
        "       bid_qty::float bq, ask_qty::float aq"
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
    bq = np.array([r["bq"] for r in ob]); aq = np.array([r["aq"] for r in ob])
    ok = (ob_ > 0) & (oa > ob_) & (bq > 0) & (aq > 0)
    ot, ob_, oa, bq, aq = ot[ok], ob_[ok], oa[ok], bq[ok], aq[ok]
    mid = 0.5 * (ob_ + oa)
    half = 0.5 * (oa - ob_)

    tt = np.array([r["event_ts_ms"] for r in tr], dtype=np.int64)
    tp = np.array([r["p"] for r in tr])
    tbm = np.array([bool(r["is_buyer_maker"]) for r in tr])
    v = tp > 0
    tt, tp, tbm = tt[v], tp[v], tbm[v]

    # 自身历史分位（**只用全样本分位做阈值定义**，不是逐时点滚动 ——
    # 逐时点滚动会引入"未来不可知"以外的复杂度；这里阈值是**静态**的，无未来函数）
    bq_p40, bq_p50, bq_med = (np.percentile(bq, 40), np.percentile(bq, 50), np.median(bq))
    aq_p40, aq_p50, aq_med = (np.percentile(aq, 40), np.percentile(aq, 50), np.median(aq))

    # 15s 决策网格（严格因果：用网格时刻**之前**最近一笔快照）
    t0, t1 = int(ot[0]), int(ot[-1])
    grids = np.arange(t0, t1 - int(hold_s * 1000), STEP_MS)
    if len(grids) < 20:
        return None
    gi = np.searchsorted(ot, grids, side="right") - 1
    g = gi >= 0
    grids, gi = grids[g], gi[g]
    mb, ma = ob_[gi], oa[gi]
    hb, ha = bq[gi], aq[gi]
    mid_g = 0.5 * (mb + ma)
    half_g = 0.5 * (ma - mb)
    imb = (hb - ha) / (hb + ha)

    # F280 挂宽：spread_mult × 半价差，然后不穿越钳制
    w = float(spread_mult) * half_g
    px_bid = np.minimum(mid_g - w, ma - cross_margin * (ma - mb))   # 绝不 >= 卖一
    px_ask = np.maximum(mid_g + w, mb + cross_margin * (ma - mb))
    px_bid = np.minimum(px_bid, mid_g)
    px_ask = np.maximum(px_ask, mid_g)

    fi = np.searchsorted(ot, grids + int(hold_s * 1000), side="right") - 1
    fi = np.clip(fi, 0, len(ot) - 1)
    fmid = 0.5 * (ob_[fi] + oa[fi])

    # 闸门定义（买单视角；卖单镜像）
    gates_buy = {
        "G0 无条件": np.ones(len(grids), dtype=bool),
        "G1 imb<=0": imb <= 0.0,
        "G2 ask_q<p50": ha < aq_med,
        "G3 ask<p40 & bid>p40": (ha < aq_p40) & (hb > bq_p40),
        "G4 ask<p40 & bid<p50": (ha < aq_p40) & (hb < bq_p50),
    }
    # 卖单镜像：对侧 = bid
    gates_sell = {
        "G0 无条件": np.ones(len(grids), dtype=bool),
        "G1 imb>=0": imb >= 0.0,
        "G2 bid_q<p50": hb < bq_med,
        "G3 bid<p40 & ask>p40": (hb < bq_p40) & (ha > aq_p40),
        "G4 bid<p40 & ask<p50": (hb < bq_p40) & (ha < aq_p50),
    }

    res = {}
    for label, gates, is_buy in (("买腿", gates_buy, True), ("卖腿", gates_sell, False)):
        for gname, gmask in gates.items():
            nets, n_dec = [], 0
            for k in np.nonzero(gmask)[0]:
                n_dec += 1
                lo = grids[k]; hi = lo + int(hold_s * 1000)
                i0 = np.searchsorted(tt, lo, side="left")
                i1 = np.searchsorted(tt, hi, side="left")
                if i1 <= i0:
                    continue
                seg_p = tp[i0:i1]; seg_bm = tbm[i0:i1]
                if is_buy:
                    hit = seg_bm & (seg_p <= px_bid[k])
                    if not hit.any():
                        continue
                    nets.append((fmid[k] - px_bid[k]) / mid_g[k] * 1e4 + maker_fee_bp)
                else:
                    hit = (~seg_bm) & (seg_p >= px_ask[k])
                    if not hit.any():
                        continue
                    nets.append((px_ask[k] - fmid[k]) / mid_g[k] * 1e4 + maker_fee_bp)
            st = _st(nets)
            st["decisions"] = int(n_dec)
            st["fill_rate"] = round(len(nets) / max(n_dec, 1), 4)
            res[f"{label}|{gname}"] = st
    return {"symbol": vs, "n_grids": int(len(grids)), "gates": res,
            "thr": {"bq_p40": float(bq_p40), "bq_p50": float(bq_p50),
                    "aq_p40": float(aq_p40), "aq_med": float(aq_med)}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--symbols", default=DEFAULT_SYMS)
    ap.add_argument("--spread-mult", type=float, default=0.9)
    ap.add_argument("--cross-margin", type=float, default=0.05)
    ap.add_argument("--maker-fee-bp", type=float, default=0.0)
    ap.add_argument("--hold-s", type=float, default=30.0)
    a = ap.parse_args()

    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    print("=" * 112)
    print("H59  失衡择时闸 —— 在 F280 挂宽之上加「什么时候挂」")
    print("=" * 112)
    print(f"  窗口 {a.hours:.0f}h  ·  hold {a.hold_s:.0f}s  ·  maker {a.maker_fee_bp:+.2f}bp"
          f"  ·  spread_mult {a.spread_mult}  ·  cross_margin {a.cross_margin}")
    print(f"  依据：Albers arXiv:2502.18625v2 §5 Table1（最优格 大near/小opp，QP0 漂移 −0.058bp）")
    print(f"        + H53 我们场地象限跨度 0.61bp")

    per = {}
    for s in syms:
        d = run_symbol(s, a.hours, a.spread_mult, a.maker_fee_bp, a.hold_s, a.cross_margin)
        if d:
            per[d["symbol"]] = d
    if not per:
        print("无数据")
        return 1

    # 汇总：按闸门×腿，跨币等权平均 + 总成交数
    keys = sorted({k for d in per.values() for k in d["gates"]},
                  key=lambda x: (x.split("|")[0], x.split("|")[1]))
    print(f"\n{'闸门':<26} {'腿':<5} {'币数':>5} {'决策合计':>10} {'成交合计':>10} "
          f"{'净额均值':>10} {'中位':>9} {'p5':>9} {'胜率':>7} {'为正币数':>9}")
    print("-" * 112)
    summary = {}
    for k in keys:
        lab, gate = k.split("|")
        means, n_dec, n_fill, meds, p5s, wins, pos = [], 0, 0, [], [], [], 0
        for s, d in per.items():
            st = d["gates"].get(k)
            if not st or not st.get("n"):
                continue
            means.append(st["mean"]); n_dec += st["decisions"]; n_fill += st["n"]
            meds.append(st["median"]); p5s.append(st["p5"]); wins.append(st["win"])
            if st["mean"] > 0:
                pos += 1
        if not means:
            continue
        f = lambda v: sum(v) / len(v)
        summary[k] = {"n_symbols": len(means), "mean": round(f(means), 4),
                      "median": round(f(meds), 4), "p5": round(f(p5s), 4),
                      "win": round(f(wins), 4), "decisions": n_dec,
                      "fills": n_fill, "positive_symbols": pos}
        print(f"{gate:<26} {lab:<5} {len(means):>5} {n_dec:>10,} {n_fill:>10,} "
              f"{f(means):>10.4f} {f(meds):>9.4f} {f(p5s):>9.4f} {f(wins):>7.3f} "
              f"{pos:>4}/{len(means):<4}")

    # 判据
    print("\n" + "=" * 112)
    print("判据（事先定死）：闸门 G 优于 G0 ⇔ 净额均值更高 **且** 成交数不塌到 <20%")
    print("=" * 112)
    for lab in ("买腿", "卖腿"):
        g0 = summary.get(f"{lab}|G0 无条件")
        if not g0:
            continue
        print(f"\n  【{lab}】基线 G0 均值 {g0['mean']:+.4f}bp，成交 {g0['fills']:,}")
        for k, v in summary.items():
            if not k.startswith(lab + "|") or k.endswith("G0 无条件"):
                continue
            gain = v["mean"] - g0["mean"]
            keep = v["fills"] / max(g0["fills"], 1)
            verdict = ("✓可用" if (gain > 0.15 and keep > 0.20)
                       else ("~边际" if gain > 0 else "✗无效"))
            print(f"    {k.split('|')[1]:<24} {v['mean']:>+9.4f}bp  "
                  f"增益 {gain:>+8.4f}bp  保留成交 {keep*100:>5.1f}%  {verdict}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"hours": a.hours, "spread_mult": a.spread_mult,
                               "maker_fee_bp": a.maker_fee_bp, "hold_s": a.hold_s,
                               "summary": summary,
                               "per_symbol": {s: d["gates"] for s, d in per.items()}},
                              ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n[H59] 写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
