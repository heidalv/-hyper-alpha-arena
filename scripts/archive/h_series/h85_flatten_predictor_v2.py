"""H85：强平预测器（用 H84 推导的**真实周期标签**）—— 把 ML 用在正确的目标上。

# 为什么重写 H75

H75 用 `logs/mm_fill_basis.jsonl` 的 `position_id` 分组，实测只有 **41 个不同值**
⇒ 只有 36~41 个"周期"，无法训练。

**根因（第 40 条教训）**：`mm:SYMBOL:N` 是**每币单调计数器**，不是一次往返。
一个 `mm:ASTER:1` 就占 440 行。

H84 改为**从持仓曲线推导**（`position_after = Σ signed_qty`，按 0→非0→0 切分）
⇒ 得到 **558 个真实周期**（含 flatten 132 个 = **23.7%**）。

# 目标（这是关键，不是预测方向）

    强平周期 23.7% 的笔数 → 吃掉绝大部分亏损（H72/H76/H80 三重口径一致）
    ⇒ 预测**"这个仓位会不会被强平"**，用它决定**要不要开这个仓**

# 特征（入场时刻可见，严格走前）

  · 方向（side）、入场 edge_bp（报价相对 mid）、qty
  · 入场价相对 engine_mid 的偏移
  · 段宽（seg_high−seg_low）/mid
  · 该币**截至此刻**的历史强平率（只用过去 ⇒ 严格走前）
  · 该币累计成交数（活跃度代理）
  · 日内时段

# 判据（事先定死，与 H71/H72 同一套）

  · 样本外 AUC < 0.60 ⇒ **无预测力，作废**
  · AUC ≥ 0.60 且"跳过预测强平的仓位"能显著降低**每周期**损失
    ⇒ 下一步做影子验证
  · **同时报基线**（不过滤）与**保留率**（跳太多等于不交易）

用法：
    .venv\\Scripts\\python.exe scripts\\h85_flatten_predictor_v2.py
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

OUT = ROOT / "research_l1" / "out" / "h85_flatten_pred.json"
N_FOLDS = 5


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-episodes", type=int, default=200)
    a = ap.parse_args()

    import numpy as np

    from h84_derive_episodes import derive, load

    print("=" * 100)
    print("H85  强平预测器 v2（H84 真实周期标签）")
    print("=" * 100)

    rows = load()
    eps = derive(rows)
    if len(eps) < a.min_episodes:
        print(f"  周期数 {len(eps)} < {a.min_episodes} ⇒ 样本不足，需继续累积")
        return 1
    n_flat = sum(1 for e in eps if e["flat"])
    print(f"  周期 {len(eps):,}   含强平 {n_flat:,}（{n_flat/len(eps)*100:.1f}%）")

    # ── 取每个周期的**入场腿**上下文 ──
    by_ts = defaultdict(list)
    for r in rows:
        by_ts[r.get("symbol")].append(r)
    for s in by_ts:
        by_ts[s].sort(key=lambda r: r.get("ts") or 0)

    # 把 H84 周期映射回具体记录
    eps2 = []
    for e in eps:
        # 用 (symbol, ts0) 找该周期的第一条记录
        cand = [r for r in by_ts.get(e["sym"], [])
                if r.get("ts") == e.get("ts0")]
        if not cand:
            continue
        eps2.append((e, cand[0]))
    print(f"  能映射到入场记录的周期：{len(eps2)}")

    syms = sorted({e["sym"] for e, _ in eps2})
    pos_of = {s: i for i, s in enumerate(syms)}
    hist = defaultdict(lambda: [0, 0])   # 走前历史强平率

    def feats(e, r):
        edge = float(r.get("edge_bp") or 0.0)
        m = float(r.get("engine_mid") or 0.0)
        px = float(r.get("fill_px") or 0.0)
        qty = float(r.get("qty") or 0.0)
        lo = float(r.get("seg_low") or 0.0)
        hi = float(r.get("seg_high") or 0.0)
        seg_w = (hi - lo) / m * 1e4 if (m > 0 and hi > lo) else 0.0
        dev = (px - m) / m * 1e4 if m > 0 else 0.0
        hf, ht = hist[e["sym"]]
        rate = (hf / ht) if ht > 0 else 0.5
        hh = 0.0
        try:
            import datetime
            hh = datetime.datetime.fromtimestamp(e["ts0"]).hour / 24.0
        except Exception:
            pass
        f = [edge, dev, seg_w, hh,
             math.log10(max(abs(e["notional"]), 1e-6)),
             1.0 if str(r.get("side")).lower() == "buy" else 0.0,
             rate, math.log10(max(ht, 1))]
        f += [1.0 if e["sym"] == s else 0.0 for s in syms]
        return np.array(f, dtype=float)

    X_list, y_list, keep = [], [], []
    for e, r in eps2:
        X_list.append(feats(e, r))
        y_list.append(1 if e["flat"] else 0)
        keep.append(e)
        hist[e["sym"]][0] += int(e["flat"])
        hist[e["sym"]][1] += 1
    X = np.vstack(X_list)
    y = np.array(y_list, dtype=int)
    print(f"  特征维度 {X.shape[1]}（含 {len(syms)} 币 one-hot + 走前历史强平率）")

    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    from sklearn.preprocessing import StandardScaler

    n = len(y)
    start = int(n * 0.40)
    fl = max(1, int((n - start) / N_FOLDS))
    proba = np.full(n, np.nan)
    for k in range(N_FOLDS):
        a_, b_ = start + k * fl, start + (k + 1) * fl
        if b_ > n:
            break
        tr, te = np.arange(0, a_), np.arange(a_, b_)
        if len(tr) < 60 or len(te) < 20 or y[tr].sum() < 5 or len(set(y[tr])) < 2:
            continue
        sc = StandardScaler().fit(X[tr])
        clf = LogisticRegression(max_iter=600, C=0.5, class_weight="balanced")
        clf.fit(sc.transform(X[tr]), y[tr])
        proba[te] = clf.predict_proba(sc.transform(X[te]))[:, 1]

    ok = np.isfinite(proba)
    print(f"\n  样本外周期数 {int(ok.sum())}（前 40% 训练、后 60% 分 {N_FOLDS} 折走前）")
    if ok.sum() < 60:
        print("  ⇒ 样本外不足，无法判定")
        return 1
    p_, y_ = proba[ok], y[ok]
    keep_ = [keep[i] for i in np.nonzero(ok)[0]]
    try:
        auc = float(roc_auc_score(y_, p_))
    except Exception:
        auc = float("nan")
    print(f"  **AUC = {auc:.4f}**   基线强平率 {y_.mean():.3f}")

    order = np.argsort(-p_)
    print(f"\n  {'风险档':>7} {'n':>6} {'实际强平率':>11} {'提升':>7}")
    print("  " + "-" * 36)
    for i in range(5):
        lo = int(len(order) * i / 5); hi = int(len(order) * (i + 1) / 5)
        sel = y_[order[lo:hi]]
        print(f"  {i+1:>7} {len(sel):>6} {sel.mean():>11.4f} "
              f"{sel.mean()/max(y_.mean(),1e-9):>6.2f}x")

    # 经济价值：按周期口径估（含强平 vs 无强平的净额差来自 H80）
    print("\n" + "=" * 100)
    print("经济价值（用 H80 的周期净额口径）")
    print("=" * 100)
    FLAT_USD, NOFLAT_USD = -0.01459, 0.12560   # H80 实测
    base_val = y_.mean() * FLAT_USD + (1 - y_.mean()) * NOFLAT_USD
    print(f"  基线每周期期望 {base_val:>+.5f} USD"
          f"（强平率 {y_.mean():.3f}）")
    print(f"\n  {'跳过最高风险':>12} {'保留':>8} {'过滤后强平率':>13} "
          f"{'每周期期望USD':>15} {'改善':>10}")
    print("  " + "-" * 64)
    for q in (0.0, 0.10, 0.20, 0.30, 0.40, 0.50):
        kk = order[int(len(order) * q):]
        fr = y_[kk].mean()
        val = fr * FLAT_USD + (1 - fr) * NOFLAT_USD
        print(f"  {q*100:>11.0f}% {len(kk)/len(y_)*100:>7.1f}% {fr:>13.4f} "
              f"{val:>+15.5f} {val-base_val:>+10.5f}")

    print("\n" + "=" * 100)
    print("判据")
    print("=" * 100)
    if not np.isfinite(auc) or auc < 0.60:
        print(f"  AUC={auc:.4f} < 0.60 ⇒ **无足够预测力，作废**")
        print("  （与 H71/H72 的结论一致：排序可学，但幅度不足以改变结果）")
    else:
        print(f"  AUC={auc:.4f} ≥ 0.60 ⇒ **有预测力** ⇒ 下一步做影子验证")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(
        {"n_episodes": len(eps), "n_flat": n_flat,
         "flat_rate": n_flat / len(eps),
         "n_oos": int(ok.sum()),
         "auc": None if not np.isfinite(auc) else round(auc, 4)},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[H85] 写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
