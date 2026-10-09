"""H53：把 Albers et al. Table 1 的「队列位置 → 成交后漂移」测到我们自己的场地上。

# 为什么这是现在最该做的一件事

H52 证明了「进价差内」这个**机制**赚钱（5 天 +0.3902bp，成交率 97.93%，强平 0.0%）。
但它**没有测队列位置**——因为离线模拟里"我们在队首"是一个**假设**，不是观测量。
这正是 H43/H46 那个错误的同一类，只是深了一层（第 17 条教训）。

Albers, Cucuringu, Howison & Shestopaloff（arXiv:2502.18625v2）在 **Binance BTCUSDT 永续实盘**
上直接测了这个量。§5 Table 1 —— 成交后平均漂移（bp），按（**近侧队列规模档**, **近侧队列位置 QP**）
（QP=0 队首，QP=1 队尾）：

| 近侧/对侧        | QP 0–0.1 | 0.1–0.4 | 0.4–0.75 | 0.75–1  |
|------------------|----------|---------|----------|---------|
| 大 near / 小 opp | **−0.058** | −0.586 | −0.743 | **−0.775** |
| 大 near / 大 opp | −0.296   | −0.882  | −0.967   | **−1.157** |
| 小 near / 小 opp | −0.562   | −0.711  | −0.622   | −0.677  |
| 小 near / 大 opp | −0.539   | −0.645  | −0.686   | −0.763  |

论文原话：*"most configurations … result in mainly negative outcomes; the exception is for
front-of-queue orders (QP ≈ 0) in a **large near-side queue** where returns are notably positive
when the opposite-side queue is small, i.e. with a favorable order book imbalance."*

**⇒ 队首↔队尾的落差是 0.72bp（大/小账本）到 1.10bp（大/大账本）。
   而我的全部边际是 −0.38bp。⇒ 队列位置不是二阶修正，它是主项。**

# 这个脚本测什么

我们的数据（`asterdex_book_ticker`，9900 万行，p50 间隔 36ms）**没有队列位置**。
但 Albers 的分类只需要 **两个可观测量**：

  1. **近侧队列规模** `bq`（我们买单所在侧 = bid_qty）
  2. **对侧队列规模** `aq`
  3. **队列位置 QP** —— 我们用**可执行的代理**：
     - **QP≈0（队首）**：报价 = `bid`（贴 touch）。此时我们排在已有队列的**队尾**，
       除非队列为空；把这一点显式分开统计（见 `bq` 分档）。
     - **改善最优价（进价差内）**：报价 = `bid*(1+δ)`。这**必然**把原来的买一全部挤到我们身后
       ⇒ **我们就是新队列的 position 0**。Arroyo QF 2024 Table 3 正是这个机制（成交概率 8.2 倍）。

⇒ **"是否改善最优价" 就是 QP 的可执行二分**，而它恰好是我在 H52 里已经在做的事。
   本脚本把 H52 的成交，按 Albers 的（队列规模档 × 是否进价差内）四象限切开，
   看**漂移**而不是看往返净额 —— 这样能与 Table 1 直接对照。

# 判据（事先定死，防事后编故事）

  · 若「大 near / 小 opp」格的漂移显著好于「大 near / 大 opp」⇒ **Albers 的失衡项在我们场地成立**
  · 若「进价差内」相对「贴 touch」的漂移改善量落在 **0.5–1.5bp** ⇒ **Table 1 的落差可移植**，
    且我们的 +0.39bp 里已经吃到了它
  · 若改善量 ≈ 0 ⇒ 我们场地的漂移不由队列位置决定，H52 的收益另有来源（必须查清）

# 口径纪律（第 17/18 条教训）

  · 漂移定义为 **mid(t+h) − mid(t)**，买单为正表示价格上行（对我们有利）。
    卖单取反。**绝不用 abs()**（第 14 条教训：30s 曾把 markout 的 std 当成本）。
  · **不做任何样本排除**：分母是全部成交样本，被迫平仓/超时的也计入（第 16 条教训）。
  · 同时报 **均值 / 中位 / p5 / 胜率**（第 15 条教训：不得用中位数代替均值）。
  · `mid` 用 **post-trade 最近一笔 book_ticker** 的前向填充，**不用**成交价（避免买卖价差污染）。

用法：
    .venv\\Scripts\\python.exe scripts\\h53_queue_position_drift.py --hours 24
    .venv\\Scripts\\python.exe scripts\\h53_queue_position_drift.py --hours 96 --symbols BTC,ETH
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

OUT = ROOT / "research_l1" / "out" / "h53_queue_drift.json"

# Albers Table 1 的 4 个 QP 桶（我们只能测两档，但保持桶名以便对照）
HORIZONS_MS = (1_000, 3_000, 5_000, 10_000, 30_000, 60_000)
PRIMARY_H = 10_000  # 主视界：Cartea et al. arXiv:2312.05827（毒性占比 6.6%@1s → 71%@70s）


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def _q(a, p):
    import numpy as np

    return float(np.percentile(a, p)) if len(a) else 0.0


def run_symbol(sym: str, hours: float, delta_bp: float):
    """返回该币的原始样本：每笔"我们会被成交"的市场成交，带两侧队列规模与漂移。

    我们的报价（买单侧）：
      · 贴 touch：px = bid          —— QP = 队尾（或空队列的队首）
      · 进价差内：px = bid*(1+δ/1e4) —— QP = 0（我们把原买一挤到身后）
    """
    import numpy as np
    import psycopg2
    import psycopg2.extras

    vs = sym if sym.endswith("USDT") else f"{sym}USDT"
    cn = psycopg2.connect(_dsn("alpha_market"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    since = f"(extract(epoch from now())*1000)::bigint - {int(hours * 3600_000)}"

    # ---- 1) 账本快照：mid / 两侧规模 / 价差 ----
    cur.execute(
        "SELECT event_ts_ms, bid_px::float b, ask_px::float a,"
        "       bid_qty::float bq, ask_qty::float aq"
        f"  FROM asterdex_book_ticker WHERE symbol=%s AND event_ts_ms > {since}"
        "  ORDER BY event_ts_ms",
        (vs,),
    )
    rows = cur.fetchall()
    if len(rows) < 1000:
        cn.close()
        return None
    bt = np.array([r["event_ts_ms"] for r in rows], dtype=np.int64)
    bb = np.array([r["b"] for r in rows], dtype=np.float64)
    ba = np.array([r["a"] for r in rows], dtype=np.float64)
    bq = np.array([r["bq"] for r in rows], dtype=np.float64)
    aq = np.array([r["aq"] for r in rows], dtype=np.float64)
    del rows

    ok = (bb > 0) & (ba > bb) & (bq > 0) & (aq > 0)
    bt, bb, ba, bq, aq = bt[ok], bb[ok], ba[ok], bq[ok], aq[ok]
    if len(bt) < 1000:
        cn.close()
        return None
    mid = 0.5 * (bb + ba)
    spread_bp = (ba - bb) / mid * 1e4

    # ---- 2) 成交流：只取主动卖单（打我们的买单） ----
    cur.execute(
        "SELECT event_ts_ms, price::float p, qty::float q, is_buyer_maker"
        f"  FROM asterdex_trades WHERE symbol=%s AND event_ts_ms > {since}"
        "  ORDER BY event_ts_ms",
        (vs,),
    )
    trows = cur.fetchall()
    cn.close()
    if len(trows) < 200:
        return None
    tt = np.array([r["event_ts_ms"] for r in trows], dtype=np.int64)
    tp = np.array([r["p"] for r in trows], dtype=np.float64)
    tq = np.array([r["q"] for r in trows], dtype=np.float64)
    tbm = np.array([bool(r["is_buyer_maker"]) for r in trows])
    del trows
    # is_buyer_maker=True ⇒ 主动方是卖方 ⇒ 打的是买盘 ⇒ 我们的买单会成交
    sell_hit = tbm & (tp > 0) & (tq > 0)
    tt, tp, tq = tt[sell_hit], tp[sell_hit], tq[sell_hit]
    if len(tt) < 100:
        return None

    # ---- 3) 逐笔对齐到"上一笔快照"（严格因果：只用 t 时刻已可见的账本） ----
    idx = np.searchsorted(bt, tt, side="right") - 1
    good = idx >= 0
    tt, tp, tq, idx = tt[good], tp[good], tq[good], idx[good]
    if len(tt) < 100:
        return None

    bid_t = bb[idx]
    ask_t = ba[idx]
    bq_t = bq[idx]
    aq_t = aq[idx]
    mid_t = mid[idx]
    sp_t = spread_bp[idx]

    # 主动卖单成交价 ≤ 我们的买价 ⇒ 我们成交
    #   · 贴 touch：px = bid_t
    #   · 进价差内：px = bid_t*(1+δ/1e4)
    px_touch = bid_t
    px_inside = bid_t * (1.0 + delta_bp / 1e4)

    # ---- 4) 前向 mid（同样用"最近一笔快照"，避免成交价污染） ----
    out = {}
    for h in HORIZONS_MS:
        j = np.searchsorted(bt, tt + h, side="right") - 1
        jj = np.clip(j, 0, len(bt) - 1)
        fwd = mid[jj]
        # 买单：价格上行有利 ⇒ drift = mid(t+h) − mid(t)。卖单为零（本脚本只测买单侧，符号物理一致）
        out[h] = (fwd - mid_t) / mid_t * 1e4

    # 相对我们成交价的漂移（更贴近真实 P&L：我们要先"付出"报价相对 mid 的位置）
    edge_touch_bp = (mid_t - px_touch) / mid_t * 1e4      # 贴 touch 的即时优势（≈ 半价差）
    edge_inside_bp = (mid_t - px_inside) / mid_t * 1e4    # 进价差内的即时优势（更大）

    return {
        "symbol": vs,
        "n": len(tt),
        "tt": tt, "tp": tp, "tq": tq,
        "bq_t": bq_t, "aq_t": aq_t,
        "mid_t": mid_t, "sp_t": sp_t,
        "fwd": out,
        "edge_touch_bp": edge_touch_bp,
        "edge_inside_bp": edge_inside_bp,
    }


def describe(x):
    import numpy as np

    x = np.asarray(x, dtype=np.float64)
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--symbols", default="BTC,ETH")
    ap.add_argument("--delta-bp", type=float, default=0.05)
    ap.add_argument("--horizon-ms", type=int, default=PRIMARY_H)
    a = ap.parse_args()

    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    data = []
    for s in syms:
        print(f"[H53] 拉取 {s} …", flush=True)
        d = run_symbol(s, a.hours, a.delta_bp)
        if d:
            print(f"        {d['symbol']}: {d['n']:,} 笔主动卖成交，"
                  f"价差中位 {_q(d['sp_t'], 50):.3f}bp", flush=True)
            data.append(d)
        else:
            print(f"        {d and d['symbol']} 数据不足，跳过", flush=True)
    if not data:
        print("[H53] 无数据")
        return 1

    import numpy as np

    H = a.horizon_ms
    # 合并所有币（先各自算 bp 再合并 —— 第 11 条教训：不许跨名义加权）
    bq = np.concatenate([d["bq_t"] for d in data])
    aq = np.concatenate([d["aq_t"] for d in data])
    drift = np.concatenate([d["fwd"][H] for d in data])
    sp = np.concatenate([d["sp_t"] for d in data])
    e_touch = np.concatenate([d["edge_touch_bp"] for d in data])
    e_inside = np.concatenate([d["edge_inside_bp"] for d in data])

    # ── Albers 的 2×2：近侧规模档 × 对侧规模档（用中位数切，等价于论文的 min→95th 分箱的粗版）
    bq_med, aq_med = float(np.median(bq)), float(np.median(aq))
    large_near = bq >= bq_med
    large_opp = aq >= aq_med

    print("\n" + "=" * 92)
    print(f"H53 队列失衡 × 成交后漂移（{H/1000:.0f}s 视界）"
          f"  —— 对标 Albers arXiv:2502.18625v2 §5 Table 1")
    print(f"  币种 {','.join(d['symbol'] for d in data)}  ·  窗口 {a.hours:.0f}h"
          f"  ·  样本 {len(drift):,} 笔  ·  近侧规模中位 {bq_med:,.2f}  对侧 {aq_med:,.2f}")
    print("=" * 92)

    quad = {
        "大 near / 小 opp": large_near & ~large_opp,
        "大 near / 大 opp": large_near & large_opp,
        "小 near / 小 opp": ~large_near & ~large_opp,
        "小 near / 大 opp": ~large_near & large_opp,
    }
    print(f"\n{'象限（Albers Table 1 的行）':<22} {'n':>9} {'均值bp':>9} {'中位':>8} "
          f"{'p5':>8} {'p95':>8} {'胜率':>7}")
    print("-" * 92)
    for k, m in quad.items():
        st = describe(drift[m])
        if st["n"]:
            print(f"{k:<22} {st['n']:>9,} {st['mean']:>9.4f} {st['median']:>8.4f} "
                  f"{st['p5']:>8.4f} {st['p95']:>8.4f} {st['win']:>7.3f}")

    # ── 论文的 Table 1 数字（只列我们场地可对照的两档）
    print("\n【Albers Table 1 原文（Binance BTCUSDT 永续，成交后漂移 bp）】")
    print(f"  {'大 near / 小 opp':<22} QP0–0.1 = −0.058    QP0.75–1 = −0.775   落差 0.717bp")
    print(f"  {'大 near / 大 opp':<22} QP0–0.1 = −0.296    QP0.75–1 = −1.157   落差 0.861bp")

    # ── 核心：我们能不能"进价差内"（= QP 0）？能的话漂移改善多少？
    #    我们的成交条件是 主动卖价 ≤ 我们的报价。贴 touch 与进价差内是两个不同的样本集。
    print("\n" + "=" * 92)
    print("核心测量：以真实市场成交为条件，比较【贴 touch】与【进价差内】的样本")
    print("=" * 92)
    # 由于我们没有"我们的挂单"，用市场成交做条件代理：
    #   贴 touch 成交 = 成交价 ≈ bid_t（价格已经跌到买一）
    #   进价差内成交 = 成交价 ≤ bid_t*(1+δ) 但 > bid_t（价格没跌到买一就被我们接住）
    hit_touch = tp <= bid_t * (1.0 + 1e-9)
    hit_inside = (tp <= px_inside) & (tp > bid_t * (1.0 + 1e-9))

    print(f"\n{'样本':<26} {'n':>9} {'占比':>7} {'漂移均值':>10} {'中位':>9} {'胜率':>7} "
          f"{'即时优势bp':>11}")
    print("-" * 92)
    for nm, m, e in (("贴 touch（价已跌到买一）", hit_touch, e_touch),
                     ("进价差内（未跌到买一）", hit_inside, e_inside)):
        if m.sum() == 0:
            continue
        st = describe(drift[m])
        print(f"{nm:<26} {st['n']:>9,} {m.mean():>7.3f} {st['mean']:>10.4f} "
              f"{st['median']:>9.4f} {st['win']:>7.3f} {e[m].mean():>11.4f}")

    d_t = describe(drift[hit_touch])
    d_i = describe(drift[hit_inside])
    if d_t.get("n") and d_i.get("n"):
        gap = d_i["mean"] - d_t["mean"]
        print(f"\n  ⇒ 进价差内 − 贴touch 的漂移差 = {gap:+.4f}bp")
        print(f"  ⇒ 即时优势差 = {e_inside[hit_inside].mean() - e_touch[hit_touch].mean():+.4f}bp")
        print(f"  ⇒ 合计（漂移 + 即时优势）= "
              f"{gap + e_inside[hit_inside].mean() - e_touch[hit_touch].mean():+.4f}bp")
        print(f"  ⇒ Albers 的队首↔队尾落差是 0.72–1.16bp；我们的可执行二分落差 {gap:+.4f}bp "
              f"（{'同量级 ✓' if gap > 0.3 else '偏小 —— 需查原因 ✗'}）")

    # ── 多视界表（漂移的时间结构）
    print("\n" + "=" * 92)
    print("漂移的时间结构（对标 Albers §5 的 1s/10s/30s/60s 与 Cartea 的毒性曲线）")
    print("=" * 92)
    print(f"\n{'视界':>7} {'贴touch 均值':>13} {'进价差内 均值':>14} {'差':>9} "
          f"{'贴touch 胜率':>13} {'进价差内 胜率':>14}")
    print("-" * 92)
    ts = {}
    for h in HORIZONS_MS:
        dv = np.concatenate([d["fwd"][h] for d in data])
        t_ = describe(dv[hit_touch])
        i_ = describe(dv[hit_inside])
        ts[h] = {"touch": t_, "inside": i_}
        if t_.get("n") and i_.get("n"):
            print(f"{h//1000:>6}s {t_['mean']:>13.4f} {i_['mean']:>14.4f} "
                  f"{i_['mean']-t_['mean']:>9.4f} {t_['win']:>13.3f} {i_['win']:>14.3f}")

    payload = {
        "hours": a.hours,
        "symbols": [d["symbol"] for d in data],
        "delta_bp": a.delta_bp,
        "horizon_ms": H,
        "n": int(len(drift)),
        "quadrants": {k: describe(drift[m]) for k, m in quad.items()},
        "touch": d_t, "inside": d_i,
        "timeseries": {str(k): v for k, v in ts.items()},
        "albers_table1": {
            "large_near_small_opp": {"qp0": -0.058, "qp1": -0.775},
            "large_near_large_opp": {"qp0": -0.296, "qp1": -1.157},
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[H53] 写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
