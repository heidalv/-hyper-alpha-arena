"""H54：δ 的单位错了 —— H52 的"进价差内"其实是"穿越价差"。

# 根因（本脚本要证实的）

我在 H46/H49/H52 里一直**默认 BTC 的价差 ~1.2bp**。实测（H53b 诊断，book_ticker 与
20 档 depth 快照交叉核对一致）：

| 币   | 价差 p50    | 折美元            |
|------|-------------|-------------------|
| BTC  | **0.0124bp**| $0.10 / $80,855   |
| ETH  | 0.0388bp    | $0.01 / $2,605    |
| SOL  | **0.9247bp**| $0.01 / $108      |

**BTC 的真实价差是 0.0124bp —— 我假设值的 1/100。**
⇒ H52 的「进价差内」买价 `bid*(1+δ/1e4)`，δ=0.05 ⇒ 报价在买一**上方 $0.40**，
   而半价差只有 $0.05 ⇒ **报价越过卖一 8 倍** ⇒ 这是**可成交单（marketable）**，
   不是挂单，却拿了 maker 返佣。

这正好解释了 H52 全部异常特征：
  · δ=0.05 成交率 **98.71%**、成交时间 **2.0s**（挂单不可能这样）
  · δ 扫描**单调递减**（0.05→+0.7153，0.40→+0.1091）：δ 越小穿越越少，"假边际"越小
  · 而"贴 touch + 队尾"只有 85.74% / 7.8s —— **那才是挂单的样子**

# 本脚本做三件事

1. **直接量化穿越**：对每个 δ，统计报价 `px > ask`（穿越）的比例。若 δ=0.05 穿越率 ≈100%
   ⇒ 证实 H52 是伪影。
2. **改用价差比例 δ_frac**：报价 = `bid + δ_frac * (ask-bid)/2`，`δ_frac ∈ [0,1]`。
   `δ_frac=1` = 贴买一（队尾），`δ_frac=0.5` = 中点，`δ_frac→0` = 逼近卖一但**不穿越**。
   这是**唯一在任意价差下都物理合法**的参数化。
3. **重跑 H52 的往返模拟**，用 `δ_frac` 参数化，看**真实**边际是多少。

# 判据（事先定死）

  · 若 `δ_frac ∈ (0,1]` 全区间都没有正收益 ⇒ **"进价差内"在 Aster 上不成立**（价差太窄，$0.10/80k）
  · 若某 `δ_frac` 为正 ⇒ 那才是真信号，且必须**同时**报成交率、穿越率、强平率
  · **绝不再用 bp 作 δ 的单位**（第 19 条教训）

用法：
    .venv\\Scripts\\python.exe scripts\\h54_delta_crossing_check.py --hours 24
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

OUT = ROOT / "research_l1" / "out" / "h54_delta_crossing.json"
FRACS = (1.00, 0.75, 0.50, 0.25, 0.10)   # 1.0 = 贴买一；越小越靠卖一（不穿越）


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def _stats(x):
    import numpy as np

    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) == 0:
        return {"n": 0}
    return {
        "n": int(len(x)),
        "mean": round(float(x.mean()), 4),
        "median": round(float(np.median(x)), 4),
        "p5": round(float(np.percentile(x, 5)), 4),
        "p95": round(float(np.percentile(x, 95)), 4),
        "win": round(float((x > 0).mean()), 4),
    }


def run(sym, hours, frac, delta_bp_old, maker_fee_bp, hold_s):
    """跑一遍：只在**不穿越**的前提下把报价放进价差内。"""
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
        "  ORDER BY event_ts_ms",
        (vs,),
    )
    rows = cur.fetchall()
    if len(rows) < 2000:
        cn.close()
        return None, None
    bt = np.array([r["event_ts_ms"] for r in rows], dtype=np.int64)
    bb = np.array([r["b"] for r in rows])
    ba = np.array([r["a"] for r in rows])
    del rows
    ok = (bb > 0) & (ba > bb)
    bt, bb, ba = bt[ok], bb[ok], ba[ok]
    mid = 0.5 * (bb + ba)
    half = 0.5 * (ba - bb)
    spread_bp = (ba - bb) / mid * 1e4

    cur.execute(
        "SELECT event_ts_ms, price::float p, qty::float q, is_buyer_maker"
        f"  FROM asterdex_trades WHERE symbol=%s AND event_ts_ms > {since}"
        "  ORDER BY event_ts_ms",
        (vs,),
    )
    tr = cur.fetchall()
    cn.close()
    if len(tr) < 200:
        return None, None
    tt = np.array([r["event_ts_ms"] for r in tr], dtype=np.int64)
    tp = np.array([r["p"] for r in tr])
    tq = np.array([r["q"] for r in tr])
    tbm = np.array([bool(r["is_buyer_maker"]) for r in tr])
    del tr
    sell = tbm & (tp > 0) & (tq > 0)          # 打买盘
    tt, tp, tq = tt[sell], tp[sell], tq[sell]
    if len(tt) < 100:
        return None, None

    idx = np.searchsorted(bt, tt, side="right") - 1
    g = idx >= 0
    tt, tp, tq, idx = tt[g], tp[g], tq[g], idx[g]
    b_t, a_t, h_t, m_t, sp_t = bb[idx], ba[idx], half[idx], mid[idx], spread_bp[idx]

    # ── 两种报价参数化 ──────────────────────────────────────────
    # (a) H52 的旧口径：δ 单位 bp（相对价格）
    px_old = b_t * (1.0 + delta_bp_old / 1e4)
    # (b) 新口径：δ_frac × 半价差
    px_new = b_t + frac * h_t

    cross_old = float((px_old > a_t).mean())      # 穿越卖一的比例
    cross_new = float((px_new > a_t).mean())

    # ── 前向 mid（严格因果） ────────────────────────────────────
    j = np.searchsorted(bt, tt + int(hold_s * 1000), side="right") - 1
    jj = np.clip(j, 0, len(bt) - 1)
    fwd_mid = mid[jj]

    out = {"symbol": vs, "n": int(len(tt)),
           "spread_bp_median": round(float(np.median(sp_t)), 4),
           "cross_old": round(cross_old, 4), "cross_new": round(cross_new, 4),
           "px_old_above_ask_bp": round(float(np.median((px_old - a_t) / m_t * 1e4)), 4),
           "px_new_above_ask_bp": round(float(np.median((px_new - a_t) / m_t * 1e4)), 4)}

    # 往返净额（买单侧，成交后持 hold_s，按 mid 平）：
    #   net = (mid(t+h) − px) / mid * 1e4 + maker_fee（返佣为正）
    for nm, px in (("old_delta_bp", px_old), ("new_frac", px_new)):
        net = (fwd_mid - px) / m_t * 1e4 + maker_fee_bp
        hit = tp <= px * (1 + 1e-12)
        out[nm] = {
            "fill_rate_on_sell_hits": round(float(hit.mean()), 4),
            "all_sell_hits": _stats(net[hit]),
            "px_vs_ask_bp": round(float(np.median((px - a_t) / m_t * 1e4)), 4),
            "px_vs_mid_bp": round(float(np.median((px - m_t) / m_t * 1e4)), 4),
        }
    return out, sp_t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--symbols", default="BTC,ETH,SOL")
    ap.add_argument("--delta-bp-old", type=float, default=0.05)
    ap.add_argument("--maker-fee-bp", type=float, default=-0.5)
    ap.add_argument("--hold-s", type=float, default=30.0)
    a = ap.parse_args()

    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]

    print("=" * 100)
    print("H54  δ 的单位错误确认 —— H52 的「进价差内」到底是不是「穿越价差」")
    print("=" * 100)
    print(f"  窗口 {a.hours:.0f}h  ·  maker 费 {a.maker_fee_bp:+.2f}bp  ·  持有 {a.hold_s:.0f}s")
    print(f"  (a) 旧口径 δ = {a.delta_bp_old}bp（相对价格，H52 用的）")
    print(f"  (b) 新口径 δ = frac × 半价差，frac ∈ {FRACS}")

    allres = {}
    for s in syms:
        d, sp = run(s, a.hours, 1.0, a.delta_bp_old, a.maker_fee_bp, a.hold_s)
        if not d:
            print(f"\n  {s}: 数据不足")
            continue
        allres[s] = d
        print("\n" + "-" * 100)
        print(f"【{d['symbol']}】样本 {d['n']:,} 笔主动卖成交  ·  价差 p50 = {d['spread_bp_median']}bp")
        print(f"  旧口径 δ={a.delta_bp_old}bp 的报价在卖一【上方】{d['px_old_above_ask_bp']:+.4f}bp"
              f"  ⇒ **穿越率 {d['cross_old']*100:.2f}%**")
        print(f"  新口径 frac=1.0 的报价在卖一【上方】{d['px_new_above_ask_bp']:+.4f}bp"
              f"  ⇒ 穿越率 {d['cross_new']*100:.2f}%（应为 0）")
        o = d["old_delta_bp"]
        print(f"\n  (a) 旧口径：成交率 {o['fill_rate_on_sell_hits']*100:.2f}%  "
              f"报价 vs mid {o['px_vs_mid_bp']:+.4f}bp  vs ask {o['px_vs_ask_bp']:+.4f}bp")
        st = o["all_sell_hits"]
        if st.get("n"):
            print(f"      净额 均值 {st['mean']:+.4f}  中位 {st['median']:+.4f}  "
                  f"p5 {st['p5']:+.4f}  胜率 {st['win']*100:.1f}%  n={st['n']:,}")

    # ── frac 扫描（真正物理合法的参数化） ──
    print("\n" + "=" * 100)
    print("frac 扫描：报价 = 买一 + frac × 半价差（frac=1 贴买一，frac→0 逼近卖一但不穿越）")
    print("=" * 100)
    print(f"\n{'币':<9} {'frac':>5} {'穿越率':>8} {'成交率':>8} {'vs mid bp':>10} "
          f"{'净额均值':>10} {'中位':>9} {'p5':>9} {'胜率':>7} {'n':>8}")
    print("-" * 100)
    for s in syms:
        for f in FRACS:
            d, _ = run(s, a.hours, f, a.delta_bp_old, a.maker_fee_bp, a.hold_s)
            if not d:
                continue
            nn = d["new_frac"]
            st = nn["all_sell_hits"]
            if not st.get("n"):
                continue
            print(f"{d['symbol']:<9} {f:>5.2f} {d['cross_new']*100:>7.2f}% "
                  f"{nn['fill_rate_on_sell_hits']*100:>7.2f}% {nn['px_vs_mid_bp']:>10.4f} "
                  f"{st['mean']:>10.4f} {st['median']:>9.4f} {st['p5']:>9.4f} "
                  f"{st['win']*100:>6.1f}% {st['n']:>8,}")
        print("-" * 100)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(
        {"hours": a.hours, "maker_fee_bp": a.maker_fee_bp, "hold_s": a.hold_s,
         "delta_bp_old": a.delta_bp_old, "fracs": list(FRACS), "per_symbol": allres},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[H54] 写出 {OUT}")

    print("\n" + "=" * 100)
    print("判读：")
    print("  · 若旧口径穿越率 ≈100% ⇒ **H52 的 +0.3902bp 是穿越伪影**，必须撤回")
    print("  · 若 frac 全区间均为负 ⇒ 「进价差内」在 Aster（$0.10 / $80k 的极窄价差）上不成立")
    print("  · 若某 frac 为正且穿越率 0% ⇒ 那才是可执行的真信号")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
