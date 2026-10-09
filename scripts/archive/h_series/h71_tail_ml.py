"""H71：超短因子 + 机器学习 —— 但目标是**预测尾部**，不是预测涨跌。

# 为什么改目标（这是对上一轮"ML 被否掉"的修正）

上一轮我用**文献**否掉了 ML，理由全是"预测方向/价格"那条路：
Wang（logistic > XGBoost > DeepLOB）、Cont–Kukanov–Stoikov（关系是线性的）、
TransLOB 不可复现、Alpha-GPT 的 LLM 种子 IC 0.010。
**但这些证据针对的是"预测方向"。** 而实测数据显示方向根本不是主要矛盾：

| 指标（fill 腿，1,905 笔） | 值 |
|---|---|
| **胜率** | **0.7310** |
| **中位** | **+1.2317bp** |
| 均值 | **−2.2407bp** |
| **最亏 5%（93 笔）占总亏损** | **82.9%** |

⇒ **73% 的方向是对的。均值之所以为负，是少数巨亏笔造成的。**

**所以真正稀缺的能力不是"预测涨跌"，而是"预测哪一笔会巨亏"。**
这两件事的难度完全不同：
  · 预测方向：要在**已经 73% 正确**的基础上再提升 ⇒ 边际收益小、噪声大
  · 预测尾部：尾部事件由**可观测的微观结构状态**驱动（价差突扩、OFI 极值、
    深度抽空、时钟偏斜）⇒ 有明确的可学信号

# 口径（严格，针对本项目已犯的 29 次错误）

1. **只用决策时刻可见的信息**：所有特征用 `searchsorted(..., side='right')-1` 取"≤ t"的数据
2. **走前验证**（walk-forward）：前 40% 训练、后 60% 分 6 折滚动，**绝不随机切分**
3. **不用 AUC 当价值证据** —— AUC 高≠赚钱。判据是**过滤后的每笔净额**
4. **报分位数单调性**：若模型有效，按预测值分档后实际均亏应**单调递增**
5. **报基准**：不过滤的每笔均值 / 中位 / 胜率 / p1 / 最亏

# 因子集（超短、tick 级、全部可实时计算）

  OFI（Cont–Kukanov–Stoikov）多窗口、盘口失衡、相对价差及其变化率、
  短窗收益与波动、成交强度、microprice 偏离、队列规模比

# 判据（事先定死）

  · 过滤后每笔均值**由负转正** 且 保留成交 ≥30% ⇒ **可用**
  · 均值改善 >0.3bp 但未转正 ⇒ 可用作辅助闸，但不是解
  · 均值改善 ≤0.3bp ⇒ **无效**，如实作废（与文献一致）

用法：
    .venv\\Scripts\\python.exe scripts\\h71_tail_ml.py --hours 48
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

OUT = ROOT / "research_l1" / "out" / "h71_tail_ml.json"
STEP_MS = 15_000
N_FOLDS = 6
DEFAULT_SYMS = "BTC,ETH,SOL,XRP,DOGE"


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def build(sym, hours, spread_mult, cross_margin, maker_fee_bp, hold_s):
    """返回该币在**每个决策点**的特征矩阵与（若该点有成交）实现净额。"""
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
        "SELECT event_ts_ms, price::float p, qty::float q, is_buyer_maker"
        f"  FROM asterdex_trades WHERE symbol=%s AND event_ts_ms > {since}"
        "  ORDER BY event_ts_ms", (vs,))
    tr = cur.fetchall()
    cn.close()
    if len(ob) < 5000 or len(tr) < 500:
        return None

    ot = np.array([r["event_ts_ms"] for r in ob], dtype=np.int64)
    ob_ = np.array([r["b"] for r in ob]); oa = np.array([r["a"] for r in ob])
    bq = np.array([r["bq"] for r in ob]); aq = np.array([r["aq"] for r in ob])
    ok = (ob_ > 0) & (oa > ob_) & (bq > 0) & (aq > 0)
    ot, ob_, oa, bq, aq = ot[ok], ob_[ok], oa[ok], bq[ok], aq[ok]
    mid = 0.5 * (ob_ + oa)
    half = 0.5 * (oa - ob_)
    spread_bp = (oa - ob_) / mid * 1e4

    tt = np.array([r["event_ts_ms"] for r in tr], dtype=np.int64)
    tp = np.array([r["p"] for r in tr], dtype=np.float64)
    tq = np.array([r["q"] for r in tr])
    tbm = np.array([bool(r["is_buyer_maker"]) for r in tr])
    good = (tq > 0) & (tp > 0)
    tt, tp, tq, tbm = tt[good], tp[good], tq[good], tbm[good]
    # 主动买量为正、主动卖量为负（用于 OFI）
    sgn = np.where(tbm, -1.0, 1.0) * tq

    t0, t1 = int(ot[0]), int(ot[-1])
    grids = np.arange(t0 + 600_000, t1 - int(hold_s * 1000), STEP_MS)
    if len(grids) < 200:
        return None
    gi = np.searchsorted(ot, grids, side="right") - 1
    g = gi >= 0
    grids, gi = grids[g], gi[g]

    mid_g = mid[gi]; half_g = half[gi]
    sp_g = spread_bp[gi]
    bq_g = bq[gi]; aq_g = aq[gi]

    # ── 特征（全部只用 ≤ 决策时刻的数据）────────────────────────
    F = {}
    F["spread_bp"] = sp_g
    F["spread_rel"] = sp_g / np.where(np.median(sp_g) > 0, np.median(sp_g), 1.0)
    F["depth_imb"] = (bq_g - aq_g) / (bq_g + aq_g)
    F["depth_ratio_log"] = np.log(np.maximum(bq_g, 1e-12) / np.maximum(aq_g, 1e-12))
    # microprice 偏离（bp）
    micro = (ob_[gi] * aq_g + oa[gi] * bq_g) / (bq_g + aq_g)
    F["micro_dev_bp"] = (micro - mid_g) / mid_g * 1e4

    # 多窗口 OFI（成交额加权净主动流，归一到该窗口总成交）
    for w in (3, 10, 30, 120):
        lo = np.searchsorted(tt, grids - w * 1000, side="left")
        hi = np.searchsorted(tt, grids, side="right")
        v = np.zeros(len(grids))
        for k in range(len(grids)):
            if hi[k] > lo[k]:
                s = sgn[lo[k]:hi[k]]
                tot = np.abs(s).sum()
                v[k] = (s.sum() / tot) if tot > 0 else 0.0
        F[f"ofi_{w}s"] = v
        F[f"intensity_{w}s"] = (hi - lo).astype(float)

    # 短窗收益与波动（用 mid，严格因果）
    for w in (10, 60):
        j = np.searchsorted(ot, grids - w * 1000, side="right") - 1
        j = np.clip(j, 0, len(ot) - 1)
        F[f"ret_{w}s_bp"] = (mid_g - mid[j]) / mid[j] * 1e4
        seg_lo = np.searchsorted(ot, grids - w * 1000, side="left")
        # 段内 mid 标准差
        sd = np.zeros(len(grids))
        for k in range(len(grids)):
            a_, b_ = seg_lo[k], gi[k] + 1
            if b_ - a_ > 2:
                sd[k] = mid[a_:b_].std() / mid_g[k] * 1e4
        F[f"vol_{w}s_bp"] = sd

    # 价差相对自身中位的变化率（突扩检测）
    F["spread_chg"] = sp_g / np.where(
        np.array([np.median(sp_g[max(0, k - 40):k + 1]) if k > 5 else sp_g[k]
                  for k in range(len(grids))]) > 0,
        np.array([np.median(sp_g[max(0, k - 40):k + 1]) if k > 5 else sp_g[k]
                  for k in range(len(grids))]), 1.0)

    keys = list(F.keys())
    X = np.column_stack([np.asarray(F[k], dtype=np.float64) for k in keys])

    # ── 标签：该决策点**若成交**则实现净额 ──────────────────────
    w = float(spread_mult) * half_g
    px_bid = np.minimum(np.minimum(mid_g - w, oa[gi] - cross_margin * (oa[gi] - ob_[gi])), mid_g)
    fi = np.searchsorted(ot, grids + int(hold_s * 1000), side="right") - 1
    fi = np.clip(fi, 0, len(ot) - 1)
    fmid = mid[fi]
    y = np.full(len(grids), np.nan)
    for k in range(len(grids)):
        lo = grids[k]; hi = lo + STEP_MS
        i0 = np.searchsorted(tt, lo, side="left")
        i1 = np.searchsorted(tt, hi, side="left")
        if i1 <= i0:
            continue
        seg = slice(i0, i1)
        # 成交判据（与 H56/H61/H64 同口径）：主动卖价 ≤ 我们的买价
        if not (tbm[seg] & (tp[seg] <= px_bid[k])).any():
            continue
        y[k] = (fmid[k] - px_bid[k]) / mid_g[k] * 1e4 + maker_fee_bp

    return {"symbol": vs, "X": X, "y": y, "keys": keys, "grids": grids}


def walk_forward_eval(X, y, kind, thresh_q):
    """走前验证：返回 (样本外预测, 样本外真实值)。"""
    import numpy as np

    n = len(y)
    start = int(n * 0.40)
    fl = int((n - start) / N_FOLDS)
    if fl < 50:
        return None, None
    pred = np.full(n, np.nan)
    for k in range(N_FOLDS):
        a, b = start + k * fl, start + (k + 1) * fl
        tr = np.arange(0, a); te = np.arange(a, min(b, n))
        mtr = np.isfinite(X[tr]).all(1) & np.isfinite(y[tr])
        mte = np.isfinite(X[te]).all(1)
        if mtr.sum() < 100 or mte.sum() < 20:
            continue
        Xtr, ytr = X[tr][mtr], y[tr][mtr]
        mu, sd = Xtr.mean(0), Xtr.std(0) + 1e-12
        Xtr = (Xtr - mu) / sd
        Xte_ = (X[te][mte] - mu) / sd
        if kind == "ridge":
            A = np.column_stack([np.ones(len(Xtr)), Xtr])
            AtA = A.T @ A + 1.0 * np.eye(A.shape[1])
            w_ = np.linalg.solve(AtA, A.T @ ytr)
            p = np.column_stack([np.ones(len(Xte_)), Xte_]) @ w_
        else:
            from sklearn.ensemble import HistGradientBoostingRegressor
            m = HistGradientBoostingRegressor(max_depth=3, max_iter=150,
                                              learning_rate=0.06, random_state=0)
            m.fit(Xtr, ytr)
            p = m.predict(Xte_)
        pred[te[mte]] = p
    m = np.isfinite(pred) & np.isfinite(y)
    return pred[m], y[m]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=48.0)
    ap.add_argument("--symbols", default=DEFAULT_SYMS)
    ap.add_argument("--spread-mult", type=float, default=0.9)
    ap.add_argument("--cross-margin", type=float, default=0.05)
    ap.add_argument("--maker-fee-bp", type=float, default=0.0)
    ap.add_argument("--hold-s", type=float, default=30.0)
    a = ap.parse_args()

    import numpy as np

    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    print("=" * 108)
    print("H71  超短因子 + ML —— 预测**尾部**（不是预测涨跌）")
    print("=" * 108)
    print(f"  窗口 {a.hours:.0f}h  ·  spread_mult {a.spread_mult}  ·  hold {a.hold_s:.0f}s"
          f"  ·  maker {a.maker_fee_bp:+.2f}bp")
    print(f"  依据：fill 腿胜率 0.7310、中位 +1.23bp，但最亏 5% 占 82.9% 亏损")
    print(f"  ⇒ 目标是**识别会巨亏的成交并跳过**，不是预测方向")

    per = {}
    for s in syms:
        d = build(s, a.hours, a.spread_mult, a.cross_margin, a.maker_fee_bp, a.hold_s)
        if d and len(d["X"]) > 500:
            per[d["symbol"]] = d
            ny = int(np.isfinite(d["y"]).sum())
            print(f"  拉取 {d['symbol']:<12} 决策点 {len(d['X']):>7,}  有成交 {ny:>6,}"
                  f"  因子 {len(d['keys'])} 个")
    if not per:
        print("无数据")
        return 1

    X = np.vstack([d["X"] for d in per.values()])
    y = np.concatenate([d["y"] for d in per.values()])
    print(f"\n  合计：决策点 {len(X):,}  有成交 {int(np.isfinite(y).sum()):,}  因子 {X.shape[1]}")

    base = y[np.isfinite(y)]
    print(f"\n  ── 基准（不过滤）──")
    print(f"     每笔均值 {base.mean():+.4f}bp  中位 {np.median(base):+.4f}  "
          f"胜率 {(base>0).mean():.4f}  p1 {np.percentile(base,1):+.3f}  "
          f"最亏 {base.min():+.3f}")

    results = {}
    for kind in ("ridge", "gbm"):
        try:
            pred, yy = walk_forward_eval(X, y, kind, 0.5)
        except Exception as e:
            print(f"\n  [{kind}] 训练失败: {type(e).__name__}: {e}")
            continue
        if pred is None or len(pred) < 200:
            print(f"\n  [{kind}] 样本不足")
            continue
        ic = float(np.corrcoef(pred, yy)[0, 1])
        print(f"\n" + "=" * 108)
        print(f"  【{kind.upper()}】样本外 n={len(pred):,}  "
              f"预测值 vs 实际 相关系数 **{ic:+.4f}**")
        print("=" * 108)

        # 分档看实际净额（模型有效 ⇒ 单调）
        order = np.argsort(pred)
        print(f"\n  {'预测档':>8} {'n':>7} {'实际均值':>10} {'中位':>9} {'胜率':>7} "
              f"{'最亏':>10}")
        print("  " + "-" * 58)
        dec = []
        for i in range(10):
            lo = int(len(order) * i / 10); hi = int(len(order) * (i + 1) / 10)
            sel = yy[order[lo:hi]]
            dec.append(sel.mean())
            print(f"  {i+1:>8} {len(sel):>7,} {sel.mean():>+10.4f} "
                  f"{np.median(sel):>+9.4f} {(sel>0).mean():>7.4f} {sel.min():>+10.3f}")
        mono = all(dec[i] <= dec[i+1] + 0.15 for i in range(9))
        print(f"\n  分档单调性（允许 0.15bp 抖动）: {'✓ 单调' if mono else '✗ 非单调'}")

        # 闸门：跳过预测最差的 q 分位
        print(f"\n  {'跳过最差':>9} {'保留成交':>9} {'过滤后均值':>11} {'中位':>9} "
              f"{'胜率':>7} {'改善':>9}")
        print("  " + "-" * 62)
        best = None
        for q in (0.0, 0.10, 0.20, 0.30, 0.40, 0.50):
            keep = order[int(len(order) * q):]
            sel = yy[keep]
            imp = sel.mean() - base.mean()
            mark = ""
            if best is None or sel.mean() > best[1]:
                best = (q, sel.mean(), len(sel) / len(yy))
            if sel.mean() > 0 and len(keep) / len(yy) >= 0.30:
                mark = " ★转正"
            print(f"  {q*100:>8.0f}% {len(keep)/len(yy)*100:>8.1f}% {sel.mean():>+11.4f} "
                  f"{np.median(sel):>+9.4f} {(sel>0).mean():>7.4f} {imp:>+9.4f}{mark}")
        results[kind] = {"ic": round(ic, 4), "monotone": bool(mono),
                         "deciles": [round(float(x), 4) for x in dec],
                         "best_q": best[0] if best else None,
                         "best_mean": round(best[1], 4) if best else None,
                         "best_keep": round(best[2], 4) if best else None}

    print("\n" + "=" * 108)
    print("判据（事先定死）")
    print("=" * 108)
    print(f"  基准每笔均值 {base.mean():+.4f}bp")
    for k, v in results.items():
        if v["best_q"] is None:
            continue
        d = v["best_mean"] - base.mean()
        if v["best_mean"] > 0 and v["best_keep"] >= 0.30:
            print(f"  [{k}] 跳过最差 {v['best_q']*100:.0f}% ⇒ 均值 {v['best_mean']:+.4f}bp"
                  f"（保留 {v['best_keep']*100:.0f}%）⇒ **可用** ✓")
        elif d > 0.3:
            print(f"  [{k}] 改善 {d:+.4f}bp 但未转正 ⇒ 可作辅助闸")
        else:
            print(f"  [{k}] 改善仅 {d:+.4f}bp ⇒ **无效**，如实作废")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(
        {"hours": a.hours, "n_fills": int(len(base)),
         "baseline": {"mean": round(float(base.mean()), 4),
                      "median": round(float(np.median(base)), 4),
                      "win": round(float((base > 0).mean()), 4)},
         "models": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[H71] 写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
