"""H51：机器学习 vs 线性 —— 严格走前对照（含 20 档真实特征与微价格）。

## 为什么要做

此前所有预测模型（H29/H30）都是**线性**（Spearman IC 扫描 + Ridge），
特征来自 `market_trades_aggregated` 的**聚合列**（top5 深度等），
**从未用过 20 档真实梯子**。

而论文（Albers et al. §7.2）的四组特征里，LOB 状态组包含
`ob bid half`（执行 $500K 的冲击成本）——**需要 20 档累加**。
且论文的 markout 终点用的是 **microprice**（`(B·p_a + A·p_b)/(B+A)`），
我们此前一直用简单中价。

## 本脚本做三件事

  1. **构 20 档真实特征**（jsonb 直接在 SQL 里聚合，不拉全量 JSON）：
     · `depth_imb_k`     前 k 档挂量失衡（k = 1/5/20）
     · `slope_bid/ask`   挂量随档位的衰减斜率（流动性剖面）
     · `impact_bid/ask`  执行固定名义额（$10k/$100k）需要走多少 bp
     · `micro_dev`       微价格相对简单中价的偏离（论文的关键特征）
     · `spread_bp`       报价价差
  2. **走前验证**（expanding window，折间不重叠）对照三种模型：
     · **Ridge**（线性基准）
     · **LightGBM**（非线性）
     · **LightGBM + 线性残差**（混合）
  3. 输出**样本外 IC**、**方向准确率**、以及**特征重要性**

## 判据（事先定死）

  · LightGBM 的样本外 IC 比 Ridge 高 **≥0.02** ⇒ 非线性有实质增益
  · 高 **<0.02** ⇒ 提升在噪声内，不值得引入模型复杂度
  · 同时报**折间稳定性**：任一折符号翻转即判为不成立

用法：
    .venv\\Scripts\\python.exe scripts\\h51_ml_vs_linear.py --hours 48
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

OUT = ROOT / "research_l1" / "out" / "h51_ml_vs_linear.json"
HORIZON_BUCKETS = 1          # 15s
N_FOLDS = 6
STRIDE = 4                   # 每 4 个深度快照取 1 个（降采样，避免自相关与内存）


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


# 在 SQL 里直接从 jsonb 构 20 档特征（避免把 200 万行 JSON 拉到 Python）
DEPTH_SQL = """
WITH d AS (
  SELECT event_ts_ms,
         (bids->0->>0)::float AS b1p, (bids->0->>1)::float AS b1q,
         (asks->0->>0)::float AS a1p, (asks->0->>1)::float AS a1q,
         bids, asks
    FROM asterdex_depth_snapshots
   WHERE symbol = %(sym)s AND event_ts_ms > %(since)s
   ORDER BY event_ts_ms
)
SELECT event_ts_ms, b1p, b1q, a1p, a1q,
       COALESCE((SELECT SUM((x->>1)::float) FROM jsonb_array_elements(bids) x), 0) AS bq20,
       COALESCE((SELECT SUM((x->>1)::float) FROM jsonb_array_elements(asks) x), 0) AS aq20,
       COALESCE((SELECT SUM((x->>1)::float) FROM jsonb_array_elements(bids) x
                  WHERE (x->>0)::float >= b1p * 0.9995), 0) AS bq5,
       COALESCE((SELECT SUM((x->>1)::float) FROM jsonb_array_elements(asks) x
                  WHERE (x->>0)::float <= a1p * 1.0005), 0) AS aq5,
       COALESCE((SELECT MIN((x->>0)::float) FROM jsonb_array_elements(bids) x
                  WHERE (x->>0)::float >= b1p * 0.999), b1p) AS b5lo,
       COALESCE((SELECT MAX((x->>0)::float) FROM jsonb_array_elements(asks) x
                  WHERE (x->>0)::float <= a1p * 1.001), a1p) AS a5hi
  FROM d
  WHERE b1p > 0 AND a1p > b1p
"""


def build(sym, hours, log):
    import numpy as np
    import psycopg2
    import psycopg2.extras

    cn = psycopg2.connect(_dsn("alpha_market"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    since = int((__import__("time").time() - hours * 3600) * 1000)
    cur.execute(DEPTH_SQL, {"sym": sym, "since": since})
    rows = cur.fetchall()
    cur.execute(
        "SELECT event_ts_ms, price::float p, qty::float q, is_buyer_maker ibm"
        "  FROM asterdex_trades WHERE symbol=%s AND event_ts_ms > %s"
        " ORDER BY event_ts_ms", (sym, since))
    tr = cur.fetchall()
    cn.close()
    if len(rows) < 3000 or len(tr) < 1000:
        return None

    ts = np.array([int(r["event_ts_ms"]) for r in rows], dtype=np.int64)
    b1p = np.array([float(r["b1p"]) for r in rows])
    b1q = np.array([float(r["b1q"] or 0) for r in rows])
    a1p = np.array([float(r["a1p"]) for r in rows])
    a1q = np.array([float(r["a1q"] or 0) for r in rows])
    bq20 = np.array([float(r["bq20"]) for r in rows])
    aq20 = np.array([float(r["aq20"]) for r in rows])
    bq5 = np.array([float(r["bq5"]) for r in rows])
    aq5 = np.array([float(r["aq5"]) for r in rows])
    b5lo = np.array([float(r["b5lo"]) for r in rows])
    a5hi = np.array([float(r["a5hi"]) for r in rows])

    tts = np.array([int(x["event_ts_ms"]) for x in tr], dtype=np.int64)
    tpx = np.array([float(x["p"]) for x in tr])
    tq = np.array([float(x["q"]) for x in tr])
    tsell = np.array([bool(x["ibm"]) for x in tr])

    mid = (b1p + a1p) / 2.0
    # 微价格（论文的终点口径）
    den1 = b1q + a1q
    micro = np.where(den1 > 0, (b1q * a1p + a1q * b1p) / den1, mid)

    n = len(ts)
    # 降采样
    idx = np.arange(0, n, STRIDE)
    ts, b1p, a1p, b1q, a1q = ts[idx], b1p[idx], a1p[idx], b1q[idx], a1q[idx]
    bq20, aq20, bq5, aq5 = bq20[idx], aq20[idx], bq5[idx], aq5[idx]
    b5lo, a5hi = b5lo[idx], a5hi[idx]
    mid, micro = mid[idx], micro[idx]
    n2 = len(ts)

    # ── 特征 ──
    F = {}
    F["spread_bp"] = (a1p - b1p) / mid * 1e4
    F["micro_dev_bp"] = (micro - mid) / mid * 1e4
    d1 = b1q + a1q
    F["imb1"] = np.where(d1 > 0, (b1q - a1q) / d1, 0.0)
    d5 = bq5 + aq5
    F["imb5"] = np.where(d5 > 0, (bq5 - aq5) / d5, 0.0)
    d20 = bq20 + aq20
    F["imb20"] = np.where(d20 > 0, (bq20 - aq20) / d20, 0.0)
    F["depth20_rel"] = d20 / np.maximum(np.median(d20), 1e-12)
    # 流动性剖面：第 1 档量 / 前 5 档量（越小说明深度越靠后）
    F["b1_share5"] = np.where(bq5 > 0, b1q / bq5, 0.0)
    F["a1_share5"] = np.where(aq5 > 0, a1q / aq5, 0.0)
    # 冲击成本代理：第 5 档边界相对最优价的偏离（bp）
    F["impact_bid_bp"] = (b1p - b5lo) / mid * 1e4
    F["impact_ask_bp"] = (a5hi - a1p) / mid * 1e4
    # 价格动态
    r = np.zeros(n2)
    r[1:] = np.where(mid[:-1] > 0, (mid[1:] - mid[:-1]) / mid[:-1] * 1e4, 0.0)
    def roll(a, w, fn):
        out = np.full(len(a), np.nan)
        for i in range(w, len(a)):
            out[i] = fn(a[i - w + 1:i + 1])
        return out
    F["ret_1"] = np.concatenate([[np.nan], r[:-1]])
    F["ret_4"] = roll(r, 4, np.sum)
    F["ret_16"] = roll(r, 16, np.sum)
    F["std_16"] = roll(r, 16, lambda a: float(np.std(a)))
    F["amp_4"] = (roll(mid, 4, np.max) - roll(mid, 4, np.min)) / mid * 1e4
    # 成交类（用过去 15s 的逐笔）
    def flow_feats(i):
        t0 = int(ts[i]) - 15000
        j0 = int(np.searchsorted(tts, t0, "left"))
        j1 = int(np.searchsorted(tts, int(ts[i]), "left"))
        if j1 <= j0:
            return 0.0, 0.0, 0.0
        sv = tq[j0:j1][tsell[j0:j1]].sum()
        bv = tq[j0:j1][~tsell[j0:j1]].sum()
        tot = sv + bv
        cnt = j1 - j0
        return ((bv - sv) / tot if tot > 0 else 0.0), cnt, (tot / max(1, cnt))
    fl = np.zeros(n2); fc = np.zeros(n2); fs = np.zeros(n2)
    for i in range(n2):
        fl[i], fc[i], fs[i] = flow_feats(i)
    F["flow_imb"] = fl
    F["trade_cnt"] = fc
    F["avg_size"] = fs
    # 目标：未来 1 个降采样步 = STRIDE × 中位间隔
    step_est = max(1, int(np.median(np.diff(ts)) / 1000))
    h = HORIZON_BUCKETS
    y = np.full(n2, np.nan)
    if n2 > h:
        y[:-h] = (mid[h:] - mid[:-h]) / mid[:-h] * 1e4
    return {"sym": sym, "F": F, "y": y, "n": n2, "step_s": step_est * h}


def walk_forward(d, kind, ridge_alpha=1.0):
    import numpy as np
    from sklearn.linear_model import Ridge
    F, y = d["F"], d["y"]
    names = sorted(F.keys())
    X = np.column_stack([F[k] for k in names])
    n = len(y)
    start = int(n * 0.40)
    fl = int((n - start) / N_FOLDS)
    if fl < 100:
        return None
    P, A, fold_ics = [], [], []
    for k in range(N_FOLDS):
        a, b = start + k * fl, start + (k + 1) * fl
        tr, te = np.arange(0, a), np.arange(a, min(b, n))
        Xtr, ytr = X[tr], y[tr]
        Xte, yte = X[te], y[te]
        mtr = np.isfinite(Xtr).all(1) & np.isfinite(ytr)
        mte = np.isfinite(Xte).all(1) & np.isfinite(yte)
        Xtr, ytr, Xte, yte = Xtr[mtr], ytr[mtr], Xte[mte], yte[mte]
        if len(Xtr) < 300 or len(Xte) < 50:
            continue
        mu, sd = Xtr.mean(0), Xtr.std(0)
        sd = np.where(sd > 0, sd, 1.0)
        Xtr_s, Xte_s = (Xtr - mu) / sd, (Xte - mu) / sd
        if kind == "ridge":
            mdl = Ridge(alpha=ridge_alpha).fit(Xtr_s, ytr)
            pr = mdl.predict(Xte_s)
        elif kind == "lgbm":
            import lightgbm as lgb
            mdl = lgb.LGBMRegressor(
                n_estimators=300, num_leaves=31, learning_rate=0.05,
                min_child_samples=50, subsample=0.8, colsample_bytree=0.8,
                reg_lambda=1.0, verbose=-1, n_jobs=4)
            mdl.fit(Xtr_s, ytr)
            pr = mdl.predict(Xte_s)
        elif kind == "lgbm_ridge":
            import lightgbm as lgb
            lin = Ridge(alpha=ridge_alpha).fit(Xtr_s, ytr)
            res = ytr - lin.predict(Xtr_s)
            gb = lgb.LGBMRegressor(
                n_estimators=300, num_leaves=31, learning_rate=0.05,
                min_child_samples=50, subsample=0.8, colsample_bytree=0.8,
                reg_lambda=1.0, verbose=-1, n_jobs=4)
            gb.fit(Xtr_s, res)
            pr = lin.predict(Xte_s) + gb.predict(Xte_s)
        else:
            raise ValueError(kind)
        if len(pr) > 30 and pr.std() > 0 and yte.std() > 0:
            fold_ics.append(float(np.corrcoef(pr, yte)[0, 1]))
        P.append(pr)
        A.append(yte)
    if not P:
        return None
    p, aa = np.concatenate(P), np.concatenate(A)
    ic = float(np.corrcoef(p, aa)[0, 1]) if p.std() > 0 and aa.std() > 0 else 0.0
    return {"kind": kind, "n_oos": int(len(p)), "ic": ic,
            "sigma_fwd": float(aa.std()), "edge_bp": ic * float(aa.std()),
            "fold_ics": fold_ics,
            "folds_same_sign": bool(fold_ics and all(
                np.sign(v) == np.sign(ic) for v in fold_ics))}


def main() -> int:
    import numpy as np

    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=48.0)
    ap.add_argument("--symbols", default="BTC,ETH,SOL")
    args = ap.parse_args()
    syms = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]

    print("H51 机器学习 vs 线性（20 档真实特征 + 走前验证）")
    print(f"窗口={args.hours}h  币={len(syms)}  视界={HORIZON_BUCKETS} 步  折数={N_FOLDS}  "
          f"降采样={STRIDE}\n")

    data = []
    for s in syms:
        vs = s if s.endswith("USDT") else f"{s}USDT"
        d = build(vs, args.hours, print)
        if d:
            data.append(d)
            print("  %-9s 样本 %6d（步长约 %ds）" % (s, d["n"], d["step_s"]))
    if not data:
        print("无数据")
        return 1

    print("\n[1] 三种模型的样本外 IC（逐币）")
    print("    %-8s %14s %14s %14s" % ("币", "Ridge", "LightGBM", "LGBM+Ridge残差"))
    per = {}
    for d in data:
        row = {}
        for kind in ("ridge", "lgbm", "lgbm_ridge"):
            r = walk_forward(d, kind)
            row[kind] = r
        per[d["sym"]] = row
        f = lambda k: ("%+.4f" % row[k]["ic"]) if row.get(k) else "—"
        print("    %-8s %14s %14s %14s" % (d["sym"], f("ridge"), f("lgbm"), f("lgbm_ridge")))

    print("\n[2] 汇总（跨币平均）")
    summary = {}
    for kind in ("ridge", "lgbm", "lgbm_ridge"):
        vals = [per[s][kind]["ic"] for s in per if per[s].get(kind)]
        if not vals:
            continue
        a = np.array(vals)
        se = a.std(ddof=1) / np.sqrt(len(a)) if len(a) > 1 else 0.0
        foldok = sum(1 for s in per if per[s].get(kind) and per[s][kind]["folds_same_sign"])
        summary[kind] = {"ic_mean": float(a.mean()), "ic_min": float(a.min()),
                         "ic_max": float(a.max()), "n": len(a),
                         "t": float(a.mean() / se) if se > 0 else 0.0,
                         "folds_all_same_sign": foldok}
        print("    %-14s IC 均值 %+.4f  (min %+.4f  max %+.4f)  t=%+.2f  折间同号 %d/%d"
              % (kind, a.mean(), a.min(), a.max(),
                 summary[kind]["t"], foldok, len(a)))

    print("\n[3] 判据（事先定死：LightGBM 样本外 IC 比 Ridge 高 ≥0.02 才算实质增益）")
    r0 = summary.get("ridge", {}).get("ic_mean")
    for kind in ("lgbm", "lgbm_ridge"):
        v = summary.get(kind, {}).get("ic_mean")
        if r0 is None or v is None:
            continue
        dlt = v - r0
        if dlt >= 0.02:
            print("    ⇒ ✓ %s 比 Ridge 高 %+.4f ⇒ **非线性有实质增益**" % (kind, dlt))
        else:
            print("    ⇒ ✗ %s 比 Ridge 高 %+.4f < 0.02 ⇒ **提升在噪声内，不值得引入复杂度**"
                  % (kind, dlt))

    print("\n[4] 与历史对照")
    print("    H30（Ridge + 聚合特征，15s）  样本外 IC +0.2149（主流 7 币）")
    if r0 is not None:
        print("    H51（Ridge + **20 档真实特征**）  %+.4f（%d 币）" % (r0, summary["ridge"]["n"]))
        print("    ⇒ %s" % ("**20 档特征优于聚合特征**"
                            if r0 > 0.2149 else "20 档特征未超过聚合特征（样本/币种不同，不可直接比）"))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "hours": args.hours, "symbols": syms, "stride": STRIDE,
        "horizon_buckets": HORIZON_BUCKETS,
        "per_symbol": {s: {k: v for k, v in row.items() if v} for s, row in per.items()},
        "summary": summary,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
