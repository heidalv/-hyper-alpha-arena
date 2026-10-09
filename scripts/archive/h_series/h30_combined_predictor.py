"""H30：组合预测模型 —— 把单特征 IC 变成可用的边际（含走前验证）。

## 已有（H29，72h，10 币，23 特征，逐币 1.7 万桶）

单特征 IC 扫描，按"全窗/前折/后折三者同号"筛选后：

    feature         h       IC全窗     IC前折     IC后折    同号
    flow_imb        15s    +0.1178   +0.1198   +0.1139   10/10
    ret_4           15s    +0.1015   +0.0951   +0.1079   10/10
    depth_imb       60s    +0.0750   +0.0657   +0.0802    9/10
    flow_imb        30s    +0.0766   +0.0743   +0.0774   10/10
    flow_imb_4      15s    +0.0660   +0.0612   +0.0701   10/10

**单特征已过折半检验**（前后折几乎相同）。但向量之间高度相关
（flow_imb / ret_4 / flow_imb_4 都是"主动买占优 ⇒ 价格上行"的同一件事），
所以**不能把 IC 相加**。必须拟合一个组合模型。

## 本脚本做什么

  **① 组合 IC**：OLS / Ridge 拟合 23 个特征 → 未来收益，走前（expanding window）
     验证，报**样本外** IC。
  **② 换算成 bp**：`edge_bp = IC × σ_forward`（Grinold 基本法则 α = IC·σ·z）。
  **③ 对照成本**：半个点差（挂单成本）与 4bp（taker 费）。
  **④ 分币报告**：不能只看跨币均值（H18-a 里 PENDLE/ARB 的信号是 0）。

## 走前验证的做法（防未来函数）

**只能用 t 时点之前的数据拟合**。本脚本用 expanding window：
    前 40% 训练 → 预测接下来 15% → 把这段并入训练 → 预测再 15% → …
每折之间**不重叠**，且任何一折的模型都没见过该折的数据。
（不用 KFold 随机切分 —— 那会泄漏未来信息，是时间序列的常见错误。）

## 判据（事先定死）

  · 样本外 IC ≥ **0.08** ⇒ 模型有实质预测力，值得做执行研究
  · 样本外 IC ≥ 0.05 ⇒ 弱可用，需与执行结合
  · 样本外 IC < 0.05 或**任一分折翻转** ⇒ 不成立
  · 且必须 `edge_bp > 半价差` 才算能覆盖被动挂单成本

用法：
    .venv\\Scripts\\python.exe scripts\\h30_combined_predictor.py --hours 72
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

OUT_DIR = ROOT / "research_l1" / "out"
HORIZON_BUCKETS = 1      # 15s（H29 里 IC 最高的视界）
N_FOLDS = 6


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=72.0)
    ap.add_argument("--exchange", default="asterdex")
    ap.add_argument("--min-buckets", type=int, default=800)
    ap.add_argument("--ridge-alpha", type=float, default=1.0)
    args = ap.parse_args()

    import numpy as np
    import psycopg2
    import psycopg2.extras

    # 复用 H29 的特征构造（同一个实现，避免两套口径）
    sys.path.insert(0, str(ROOT / "scripts"))
    from h29_feature_ic_scan import HORIZONS, build_features  # noqa: E402

    cn = psycopg2.connect(_dsn("alpha_market"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT DISTINCT symbol FROM market_trades_aggregated"
        " WHERE exchange = %s AND timestamp > (extract(epoch from now())*1000)::bigint - %s",
        (args.exchange, int(args.hours * 3600_000)),
    )
    syms = [r["symbol"] for r in cur.fetchall()]

    print(f"H30 组合预测模型（走前验证）  exchange={args.exchange}  窗口={args.hours}h")
    print(f"视界 = {HORIZON_BUCKETS} 桶 = {HORIZON_BUCKETS*15}s    折数 = {N_FOLDS}")
    print(f"Ridge alpha = {args.ridge_alpha}\n")

    per_sym = {}
    for s in syms:
        cur.execute(
            "SELECT timestamp, taker_buy_volume, taker_sell_volume,"
            "       taker_buy_count, taker_sell_count,"
            "       taker_buy_notional, taker_sell_notional,"
            "       vwap, high_price, low_price,"
            "       bid_depth_top5, ask_depth_top5, largest_trade_usd"
            "  FROM market_trades_aggregated"
            " WHERE exchange = %s AND symbol = %s"
            "   AND timestamp > (extract(epoch from now())*1000)::bigint - %s"
            " ORDER BY timestamp",
            (args.exchange, s, int(args.hours * 3600_000)),
        )
        rows = cur.fetchall()
        if len(rows) < args.min_buckets:
            continue
        d = build_features(rows)
        if d:
            per_sym[s] = d
    print(f"可用币 {len(per_sym)}: {' '.join(sorted(per_sym))}\n")
    if not per_sym:
        return 1

    names = sorted(next(iter(per_sym.values()))["F"].keys())
    H = HORIZON_BUCKETS

    def _design(d, idx):
        """构造设计矩阵 X（全特征）与 y（未来 H 桶收益），只用 idx 指定的行。"""
        X = np.column_stack([d["F"][nm][idx] for nm in names])
        y = d["fwd"][H][idx]
        return X, y

    def _clean(X, y):
        m = np.isfinite(X).all(axis=1) & np.isfinite(y)
        return X[m], y[m]

    def _standardize(Xtr, Xte):
        """按**训练集**的均值方差标准化（绝不能用测试集的统计量）。"""
        mu = np.nanmean(Xtr, axis=0)
        sd = np.nanstd(Xtr, axis=0)
        sd = np.where(sd > 0, sd, 1.0)
        return (Xtr - mu) / sd, (Xte - mu) / sd

    def _ridge_fit(X, y, alpha):
        """闭式解 (XᵗX + αI)⁻¹Xᵗy（带截距：给 X 加一列 1）。"""
        Xa = np.column_stack([np.ones(len(X)), X])
        A = Xa.T @ Xa
        reg = np.eye(A.shape[0]) * alpha
        reg[0, 0] = 0.0                      # 不惩罚截距
        try:
            w = np.linalg.solve(A + reg, Xa.T @ y)
        except np.linalg.LinAlgError:
            w = np.linalg.lstsq(A + reg, Xa.T @ y, rcond=None)[0]
        return w

    def _predict(w, X):
        return np.column_stack([np.ones(len(X)), X]) @ w

    # ── 走前验证：对每个币独立做 expanding-window ────────────────
    print("[1] 逐币走前验证（前 40% 起步，之后每折 10%，折间不重叠）")
    print("    %-10s %8s %10s %10s %10s" %
          ("symbol", "OOS n", "IC_OOS", "edge_bp", "sigma_fwd"))

    per_symbol = {}
    for s, d in sorted(per_sym.items()):
        n = d["n"]
        start = int(n * 0.40)
        fold_len = int((n - start) / N_FOLDS)
        if fold_len < 100:
            continue
        preds, acts = [], []
        for k in range(N_FOLDS):
            a = start + k * fold_len
            b = a + fold_len
            tr = np.arange(0, a)                    # **只用过去**
            te = np.arange(a, min(b, n))
            if len(tr) < 300 or len(te) < 50:
                continue
            Xtr, ytr = _clean(*_design(d, tr))
            Xte_raw, yte = _design(d, te)
            mte = np.isfinite(Xte_raw).all(axis=1) & np.isfinite(yte)
            Xte_raw, yte = Xte_raw[mte], yte[mte]
            if len(Xtr) < 200 or len(Xte_raw) < 30:
                continue
            Xtr_s, Xte_s = _standardize(Xtr, Xte_raw)
            w = _ridge_fit(Xtr_s, ytr, args.ridge_alpha)
            preds.append(_predict(w, Xte_s))
            acts.append(yte)
        if not preds:
            continue
        p = np.concatenate(preds)
        a_ = np.concatenate(acts)
        ic = float(np.corrcoef(p, a_)[0, 1]) if p.std() > 0 and a_.std() > 0 else 0.0
        sd = float(a_.std())
        per_symbol[s] = {"n_oos": int(len(p)), "ic_oos": ic,
                         "sigma_fwd_bp": sd, "edge_bp": ic * sd}
        print("    %-10s %8d %+10.4f %10.4f %10.3f"
              % (s, len(p), ic, ic * sd, sd))

    if not per_symbol:
        print("\n没有币完成走前验证")
        return 1

    ics = np.array([v["ic_oos"] for v in per_symbol.values()])
    edges = np.array([v["edge_bp"] for v in per_symbol.values()])
    sds = np.array([v["sigma_fwd_bp"] for v in per_symbol.values()])
    icm_all = float(ics.mean())

    # ⚠️⚠️ 必须按**流动性**分层报告。
    #
    # 实测：SYN/XPL 的 15s σ 是 **43~44bp**，而 BTC 只有 **1.41bp** —— 差 30 倍。
    # `edge_bp = IC × σ` ⇒ 跨币**平均 edge** 会被低流动币的巨幅波动主导：
    # 未分层时均值 1.607bp，其中 SYN(5.34) + XPL(6.14) 两项就贡献了 1.15bp，
    # 而它们各只有 3839 / 708 个样本（XPL 连一个币的 1/14 都不到）。
    # 拿这个均值去说"可覆盖半价差 0.72bp"是**错的** —— 主流的真实 edge 只有 0.2~1.3bp。
    #
    # 分层规则：σ(15s) ≤ 6bp 视为"主流流动性"（BTC 1.4 / ETH 2.3 / BNB 2.0 /
    # SOL 2.9 / DOGE 3.4 / XRP 3.7 / ASTER 3.9 全部落在这里）。
    LIQ_SIGMA_MAX = 6.0
    liq = {s: v for s, v in per_symbol.items() if v["sigma_fwd_bp"] <= LIQ_SIGMA_MAX}
    illq = {s: v for s, v in per_symbol.items() if v["sigma_fwd_bp"] > LIQ_SIGMA_MAX}

    print("\n[2] 汇总（**样本外**；按流动性分层 —— 不分层会被低流动币的巨幅波动污染）")

    def _block(title, dd):
        if not dd:
            print(f"    {title}: （空）")
            return None
        i = np.array([v["ic_oos"] for v in dd.values()])
        e = np.array([v["edge_bp"] for v in dd.values()])
        s = np.array([v["sigma_fwd_bp"] for v in dd.values()])
        se_ = i.std(ddof=1) / np.sqrt(len(i)) if len(i) > 1 else 0.0
        print("    %s  （%d 币: %s）" % (title, len(dd), " ".join(sorted(dd))))
        print("      IC_OOS    均值 %+.4f   中位 %+.4f   min %+.4f   max %+.4f"
              % (i.mean(), np.median(i), i.min(), i.max()))
        print("      同号      %d / %d     跨币 t %+.2f"
              % (int((i > 0).sum()), len(i), i.mean() / se_ if se_ > 0 else 0.0))
        print("      σ(15s)    均值 %.3f bp" % s.mean())
        print("      **edge    均值 %.4f bp / 决策**（= IC × σ）" % e.mean())
        return {"n_sym": len(dd), "ic_mean": float(i.mean()),
                "n_pos": int((i > 0).sum()), "sigma_mean": float(s.mean()),
                "edge_mean": float(e.mean()), "symbols": sorted(dd)}

    blk_liq = _block("主流流动性（σ≤6bp）", liq)
    blk_illq = _block("低流动性（σ>6bp，样本少）", illq)
    print("\n    （全体，**仅供参考，不要用**）IC %.4f  edge %.4f bp"
          % (ics.mean(), edges.mean()))

    # 判定用主流分层
    use_ic = blk_liq["ic_mean"] if blk_liq else float(ics.mean())
    use_edge = blk_liq["edge_mean"] if blk_liq else float(edges.mean())
    use_pos = blk_liq["n_pos"] if blk_liq else int((ics > 0).sum())
    use_n = blk_liq["n_sym"] if blk_liq else len(ics)

    print("\n[3] 对照成本（用**主流分层**的 edge）")
    print("    被动挂单成本（半个点差）  ≈ 0.72 bp")
    print("    主动成交成本（taker 费）  = 4.00 bp")
    print("    ⇒ edge / 半价差 = %.2f 倍" % (use_edge / 0.72))
    print("    ⇒ edge / taker  = %.2f 倍" % (use_edge / 4.0))

    print("\n[4] 判据（用主流分层）")
    sign_stable = use_pos >= max(7, int(0.7 * use_n))
    if use_ic >= 0.08 and sign_stable:
        print("    ⇒ ✓ 样本外 IC %.4f ≥ 0.08 且 %d/%d 同号 ⇒ **模型有实质预测力**。"
              % (use_ic, use_pos, use_n))
    elif use_ic >= 0.05 and sign_stable:
        print("    ⇒ ~ 样本外 IC %.4f，弱可用（需与执行结合）。" % use_ic)
    else:
        print("    ⇒ ✗ 样本外 IC %.4f 不足或符号不稳 ⇒ 不成立。" % use_ic)
    if use_edge > 0.72:
        print("    且 edge %.3fbp > 半价差 0.72bp ⇒ **可覆盖被动挂单成本**。" % use_edge)
    else:
        print("    但 edge %.3fbp < 半价差 0.72bp ⇒ **单靠它覆盖不了被动挂单成本**，"
              % use_edge)
        print("       必须配合执行侧（贴 touch 增成交、或缩短持仓）才可能转正。")
    print("\n    ⚠️ 口径提醒：edge = IC × σ 是 Grinold 基本法则的**上界估计**，")
    print("       它假设我们可以**按预测方向拿到成交**。被动挂单做不到这一点 ——")
    print("       预测「要涨」时挂买单，只有在价格**跌下来**时才会成交（逆向选择）。")
    print("       ⇒ 从「有预测力」到「能赚钱」之间，还差一个**执行侧**论证。")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = OUT_DIR / "h30_combined_predictor.json"
    p.write_text(json.dumps({
        "hours": args.hours, "horizon_buckets": H, "n_folds": N_FOLDS,
        "ridge_alpha": args.ridge_alpha, "features": names,
        "per_symbol": per_symbol,
        "stratified": {"liquid_sigma_le_6bp": blk_liq,
                       "illiquid_sigma_gt_6bp": blk_illq},
        "summary": {"ic_oos_mean_all": icm_all, "ic_oos_median": float(np.median(ics)),
                    "n_pos_all": int((ics > 0).sum()), "n_sym": len(ics),
                    "sigma_fwd_bp_mean_all": float(sds.mean()),
                    "edge_bp_mean_all": float(edges.mean()),
                    "ic_oos_mean_liquid": use_ic, "edge_bp_mean_liquid": use_edge},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {p}")
    cur.close()
    cn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
