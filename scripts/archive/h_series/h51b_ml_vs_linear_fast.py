"""H51b：LightGBM vs Ridge —— 用已有聚合表做快速非线性检验。

## 为什么改口径（H51 超时）

H51 想用 `asterdex_depth_snapshots` 的 jsonb 直接构 20 档特征，
但那个查询在 200 万行上跑超时（600s）。**放弃 20 档，改用
`market_trades_aggregated` 的聚合列** —— 与 H29/H30 完全相同的口径，
这样结果**可以直接与 H30 的样本外 IC 0.2149 比较**。

## 文献预期（本轮调研，可直接检验）

  · **Wang, arXiv:2506.05764**（Bybit BTC/USDT，100ms LOB）：
    500ms/1000ms 上 **logistic 0.5434/0.5382 > XGBoost 0.5393/0.4999 >
    CNN+LSTM 0.5252/0.4936 > DeepLOB 0.5237/0.4762** ⇒ 线性模型在长视界上更强
  · **Cont–Kukanov–Stoikov**：OFI → 价格变动的二次项只把 R² 从 65% 提到 68%
    且不显著 ⇒ **关系是线性的，GBM 没有非线性可捡**

⇒ 本脚本的判据（事先定死）：
  · LightGBM 样本外 IC 比 Ridge 高 **≥0.02** ⇒ 非线性有实质增益，值得引入
  · 高 **<0.02** ⇒ 提升在噪声内 ⇒ **与文献一致，不上 ML**

用法：
    .venv\\Scripts\\python.exe scripts\\h51b_ml_vs_linear_fast.py --hours 168
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

OUT = ROOT / "research_l1" / "out" / "h51b_ml_vs_linear.json"
N_FOLDS = 6


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def walk_forward(X, y, kind):
    import numpy as np
    n = len(y)
    start = int(n * 0.40)
    fl = int((n - start) / N_FOLDS)
    if fl < 100:
        return None
    P, A, fold_ics = [], [], []
    for k in range(N_FOLDS):
        a, b = start + k * fl, start + (k + 1) * fl
        tr, te = np.arange(0, a), np.arange(a, min(b, n))
        Xtr, ytr, Xte, yte = X[tr], y[tr], X[te], y[te]
        mtr = np.isfinite(Xtr).all(1) & np.isfinite(ytr)
        mte = np.isfinite(Xte).all(1) & np.isfinite(yte)
        Xtr, ytr, Xte, yte = Xtr[mtr], ytr[mtr], Xte[mte], yte[mte]
        if len(Xtr) < 400 or len(Xte) < 50:
            continue
        mu, sd = Xtr.mean(0), Xtr.std(0)
        sd = np.where(sd > 0, sd, 1.0)
        Xtr_s, Xte_s = (Xtr - mu) / sd, (Xte - mu) / sd
        if kind == "ridge":
            from sklearn.linear_model import Ridge
            pr = Ridge(alpha=1.0).fit(Xtr_s, ytr).predict(Xte_s)
        elif kind == "lgbm":
            import lightgbm as lgb
            m = lgb.LGBMRegressor(n_estimators=400, num_leaves=31, learning_rate=0.05,
                                  min_child_samples=60, subsample=0.8,
                                  colsample_bytree=0.8, reg_lambda=1.0,
                                  verbose=-1, n_jobs=4)
            m.fit(Xtr_s, ytr)
            pr = m.predict(Xte_s)
        else:
            raise ValueError(kind)
        if len(pr) > 30 and pr.std() > 0 and yte.std() > 0:
            fold_ics.append(float(np.corrcoef(pr, yte)[0, 1]))
        P.append(pr); A.append(yte)
    if not P:
        return None
    p, aa = np.concatenate(P), np.concatenate(A)
    ic = float(np.corrcoef(p, aa)[0, 1]) if p.std() > 0 and aa.std() > 0 else 0.0
    return {"kind": kind, "n_oos": int(len(p)), "ic": ic,
            "sigma_fwd_bp": float(aa.std()), "edge_bp": ic * float(aa.std()),
            "n_folds": len(fold_ics), "fold_ics": fold_ics,
            "folds_same_sign": bool(fold_ics and all(
                np.sign(v) == np.sign(ic) for v in fold_ics))}


def main() -> int:
    import numpy as np
    import psycopg2
    import psycopg2.extras

    from h29_feature_ic_scan import build_features  # noqa: E402

    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=168.0)
    ap.add_argument("--symbols", default="BTC,ETH,SOL,XRP,ASTER")
    ap.add_argument("--min-buckets", type=int, default=800)
    args = ap.parse_args()
    syms = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]

    cn = psycopg2.connect(_dsn("alpha_market"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    since = int(args.hours * 3600_000)

    print("H51b LightGBM vs Ridge（同一套聚合特征，可直接比 H30）")
    print(f"窗口={args.hours}h  币={len(syms)}  折数={N_FOLDS}\n")

    per = {}
    for s in syms:
        cur.execute(
            "SELECT timestamp, taker_buy_volume, taker_sell_volume,"
            " taker_buy_count, taker_sell_count, taker_buy_notional,"
            " taker_sell_notional, vwap, high_price, low_price,"
            " bid_depth_top5, ask_depth_top5, largest_trade_usd"
            "  FROM market_trades_aggregated"
            " WHERE exchange='asterdex' AND symbol=%s"
            "   AND timestamp > (extract(epoch from now())*1000)::bigint - %s"
            " ORDER BY timestamp", (s, since))
        rows = cur.fetchall()
        if len(rows) < args.min_buckets:
            print(f"  {s:<8} 桶不足 {len(rows)} → 跳过")
            continue
        d = build_features(rows)
        if not d:
            continue
        names = sorted(d["F"].keys())
        X = np.column_stack([d["F"][k] for k in names])
        y = d["fwd"][1]                     # 未来 1 桶 = 15s
        r = {}
        for kind in ("ridge", "lgbm"):
            r[kind] = walk_forward(X, y, kind)
        per[s] = {"n": d["n"], "n_feat": len(names), "res": r}
        print("  %-8s 桶 %6d  特征 %2d   Ridge IC %+.4f   LightGBM IC %+.4f   差 %+.4f"
              % (s, d["n"], len(names),
                 r["ridge"]["ic"] if r["ridge"] else float("nan"),
                 r["lgbm"]["ic"] if r["lgbm"] else float("nan"),
                 ((r["lgbm"]["ic"] - r["ridge"]["ic"])
                  if (r["ridge"] and r["lgbm"]) else float("nan"))))
    cn.close()
    if not per:
        print("\n无数据")
        return 1

    print("\n[1] 汇总（跨币平均，**样本外**）")
    summary = {}
    for kind in ("ridge", "lgbm"):
        vals = [per[s]["res"][kind]["ic"] for s in per if per[s]["res"].get(kind)]
        if not vals:
            continue
        a = np.array(vals)
        se = a.std(ddof=1) / np.sqrt(len(a)) if len(a) > 1 else 0.0
        ok = sum(1 for s in per
                 if per[s]["res"].get(kind) and per[s]["res"][kind]["folds_same_sign"])
        summary[kind] = {"ic_mean": float(a.mean()), "ic_min": float(a.min()),
                         "ic_max": float(a.max()), "n": len(a),
                         "t": float(a.mean() / se) if se > 0 else 0.0,
                         "folds_all_same_sign": ok}
        print("    %-9s IC 均值 %+.4f  (min %+.4f  max %+.4f)  t=%+.2f  折间同号 %d/%d"
              % (kind, a.mean(), a.min(), a.max(), summary[kind]["t"], ok, len(a)))

    print("\n[2] 判据（事先定死：LightGBM 比 Ridge 高 ≥0.02 才算实质增益）")
    r0 = summary.get("ridge", {}).get("ic_mean")
    r1 = summary.get("lgbm", {}).get("ic_mean")
    if r0 is not None and r1 is not None:
        d = r1 - r0
        print("    差 %+.4f" % d)
        if d >= 0.02:
            print("    ⇒ ✓ **非线性有实质增益**，值得引入 LightGBM")
        else:
            print("    ⇒ ✗ 提升 %+.4f < 0.02 ⇒ **在噪声内**" % d)
            print("       ⇒ 与文献一致：")
            print("         · Wang arXiv:2506.05764：500ms/1000ms 上 logistic > XGBoost > DeepLOB")
            print("         · Cont-Kukanov-Stoikov：OFI→价格二次项只把 R² 65%→68% 且不显著（线性）")

    print("\n[3] 与历史对照")
    print("    H30（Ridge，7 币，24h）：样本外 IC +0.2149")
    if r0 is not None:
        print("    H51b（Ridge，%d 币，%.0fh）：%+.4f" % (summary["ridge"]["n"], args.hours, r0))
    if r1 is not None:
        print("    H51b（LightGBM）：%+.4f" % r1)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "hours": args.hours, "symbols": syms, "n_folds": N_FOLDS,
        "per_symbol": {s: {"n": v["n"], "n_feat": v["n_feat"],
                           "ridge": v["res"].get("ridge"), "lgbm": v["res"].get("lgbm")}
                       for s, v in per.items()},
        "summary": summary,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
