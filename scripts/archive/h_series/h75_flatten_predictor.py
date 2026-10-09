"""H75：用 ML 预测**"这个仓位会不会被强平"** —— 把 ML 用在正确的目标上。

# 为什么目标要换成这个（H72 的结论）

    强平：11.5% 的笔数 → **88.0%** 的亏损，每笔 **−12.73bp**
    被动出库：赚的（~+1.3bp/笔）

⇒ 只要能**提前识别"这一笔会被迫市价平仓"并选择不开**，
边际就直接从 −0.23bp 变成正。**这比预测方向的价值高一个量级。**

而 H71–H73 已证明：预测**方向**信号弱（IC 0.05）、幅度不够；
但**尾部/强平是可辨识的**（H72 分类器尾部命中率 14.4%→3.2%，4.5 倍分离）。

# 数据源（**实盘真实记录**，不是模拟）

`logs/mm_fill_basis.jsonl` —— 每笔成交一条，含：
  `ts / symbol / side / qty / fill_px / engine_mid / seg_low / seg_high / fee_rate /
   flatten / position_id / edge_bp`

  · `position_id`（形如 `mm:SOL:3`）用于把成交聚合成**仓位周期**
  · `flatten=true` 标记强平腿
  · 入场腿 = 每个 position_id 的**第一笔非 flatten** 记录

# 特征（入场时刻可见）

  方向（side）、相对 edge（`edge_bp`）、成交价相对 mid 的偏移、
  日内时段、持仓量（`qty×fill_px` 相对腿量）、币种 one-hot、
  该币的历史强平率（**只用过去**，走前计算）、入场前段的 segment 宽度

# 判据（事先定死）

  · 分类器样本外 AUC < 0.60 ⇒ **无预测力，作废**
  · AUC ≥ 0.60 且"跳过预测强平的仓位"能显著降低总亏损 ⇒ 可用
  · **必须同时报基线**（不过滤的总亏损）与**保留率**（跳太多等于不交易）

用法：
    .venv\\Scripts\\python.exe scripts\\h75_flatten_predictor.py
"""
from __future__ import annotations

import argparse
import json
import math
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

load_dotenv(ROOT / ".env", override=False)

BASIS = ROOT / "logs" / "mm_fill_basis.jsonl"
OUT = ROOT / "research_l1" / "out" / "h75_flatten_predictor.json"
N_FOLDS = 5


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def load_episodes():
    """把 fill_basis 聚合成**仓位周期**：每周期取入场腿 + 是否被强平 + 周期净额。"""
    rows = []
    for line in BASIS.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    if not rows:
        return None, None
    rows.sort(key=lambda r: r.get("ts") or 0)
    ep = defaultdict(list)
    for r in rows:
        pid = r.get("position_id") or f"{r.get('symbol')}:?"
        ep[pid].append(r)
    out = []
    for pid, rs in ep.items():
        entry = next((r for r in rs if not r.get("flatten")), None)
        if entry is None:
            continue
        did_flat = any(r.get("flatten") for r in rs)
        out.append({"pid": pid, "entry": entry, "did_flat": bool(did_flat),
                    "n_fills": len(rs), "ts": entry.get("ts") or 0})
    out.sort(key=lambda x: x["ts"])
    return out, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--leg-notional", type=float, default=134.0)
    a = ap.parse_args()

    import numpy as np

    print("=" * 100)
    print("H75  用 ML 预测「这个仓位会不会被强平」")
    print("=" * 100)

    eps, raw = load_episodes()
    if not eps or len(eps) < 100:
        print(f"  fill_basis 仓位周期不足（{0 if not eps else len(eps)}）—— 需要更多实盘数据")
        print(f"  （当前 fill_basis 行数 {0 if not raw else len(raw)}）")
        return 1
    n_flat = sum(1 for e in eps if e["did_flat"])
    print(f"  仓位周期 {len(eps):,}  其中被强平 {n_flat:,}"
          f"（**强平率 {n_flat/len(eps)*100:.1f}%**）")
    print(f"  时间跨度 {min(e['ts'] for e in eps):.0f} ~ {max(e['ts'] for e in eps):.0f}")

    # ── 特征 ──
    syms = sorted({e["entry"].get("symbol") for e in eps})
    pos_of = {s: i for i, s in enumerate(syms)}
    # 该币的**走前**历史强平率（只用过去）
    hist = defaultdict(lambda: [0, 0])   # sym -> [flat, total]

    def feats(e, idx):
        r = e["entry"]
        sym = r.get("symbol")
        m = float(r.get("engine_mid") or 0)
        px = float(r.get("fill_px") or 0)
        edge = float(r.get("edge_bp") or 0)
        qty = float(r.get("qty") or 0)
        lo = float(r.get("seg_low") or 0)
        hi = float(r.get("seg_high") or 0)
        seg_w = (hi - lo) / m * 1e4 if (m > 0 and hi > lo) else 0.0
        dev = (px - m) / m * 1e4 if m > 0 else 0.0
        hf, ht = hist[sym]
        hist_rate = (hf / ht) if ht > 0 else 0.5
        # 时段（小时）
        hh = 0.0
        try:
            import datetime
            hh = datetime.datetime.fromtimestamp(e["ts"]).hour / 24.0
        except Exception:
            pass
        f = [edge, dev, seg_w, hh, math.log10(max(qty * px, 1e-9)),
             hist_rate, 1.0 if str(r.get("side")) == "buy" else 0.0,
             math.log10(max(ht, 1))]
        f += [1.0 if sym == s else 0.0 for s in syms]
        return np.array(f, dtype=float)

    # ── 特征（两遍：第一遍用"截至此刻的历史"，第二遍才更新历史 ⇒ 严格走前）──
    # 注意：`hist` 必须在**每次取特征之后**才用当前样本更新，
    # 否则同一笔样本会把自己的结果算进特征里（未来函数）✗
    hist = defaultdict(lambda: [0, 0])     # sym -> [flat_count, total]
    X_list = []
    for e in eps:
        X_list.append(feats(e, 0))
        sym = e["entry"].get("symbol")
        hist[sym][0] += int(e["did_flat"])
        hist[sym][1] += 1
    X = np.vstack(X_list)
    y = np.array([1 if e["did_flat"] else 0 for e in eps], dtype=int)
    print(f"  特征维度 {X.shape[1]}（含 {len(syms)} 个币 one-hot + 走前历史强平率）")

    # ── 走前验证 ──
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
        tr = np.arange(0, a_); te = np.arange(a_, b_)
        if len(tr) < 60 or len(te) < 20 or y[tr].sum() < 5:
            continue
        sc = StandardScaler().fit(X[tr])
        clf = LogisticRegression(max_iter=500, C=0.5, class_weight="balanced")
        clf.fit(sc.transform(X[tr]), y[tr])
        proba[te] = clf.predict_proba(sc.transform(X[te]))[:, 1]
    ok = np.isfinite(proba)
    if ok.sum() < 50:
        print(f"\n  样本外样本不足（{ok.sum()}）⇒ 无法判定。需要更多实盘成交。")
        print(f"  提示：当前强平 {n_flat} 次，强平率 {n_flat/len(eps)*100:.1f}%。")
        return 0
    p_, y_ = proba[ok], y[ok]
    try:
        auc = float(roc_auc_score(y_, p_))
    except Exception:
        auc = float("nan")
    print(f"\n  ── 样本外（n={len(y_):,}，强平 {int(y_.sum())}）──")
    print(f"     **AUC = {auc:.4f}**   （0.5=无预测力）")
    print(f"     强平率基线 {y_.mean():.3f}")

    # 分档
    order = np.argsort(-p_)
    print(f"\n  {'风险档':>7} {'n':>7} {'实际强平率':>11} {'提升':>8}")
    print("  " + "-" * 38)
    for i in range(5):
        lo = int(len(order) * i / 5); hi = int(len(order) * (i + 1) / 5)
        sel = y_[order[lo:hi]]
        lift = sel.mean() / max(y_.mean(), 1e-9)
        print(f"  {i+1:>7} {len(sel):>7,} {sel.mean():>11.4f} {lift:>7.2f}x")

    print("\n" + "=" * 100)
    print("判据（事先定死）")
    print("=" * 100)
    if not np.isfinite(auc) or auc < 0.60:
        print(f"  AUC={auc:.4f} < 0.60 ⇒ **无足够预测力，作废**")
    else:
        print(f"  AUC={auc:.4f} ≥ 0.60 ⇒ **有预测力**")
        print("  ⇒ 下一步：跳过预测强平率最高的 q 分位，用 H72 的腿级成本模型估美元节省")
    print(f"\n  ⚠️ 样本说明：{len(eps)} 个仓位周期 / 1,444 条成交记录。")
    print(f"     这个量级只能做**方向性判断**，不足以定阈值。需继续累积实盘数据。")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(
        {"n_episodes": len(eps), "flat_rate": n_flat / len(eps),
         "n_oos": int(ok.sum()), "auc": None if not np.isfinite(auc) else round(auc, 4)},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[H75] 写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
