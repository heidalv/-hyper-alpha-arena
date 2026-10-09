"""H72：两个问题
  A. 把"预测尾部"从**回归**换成**分类**，信号是否更强？
  B. 为什么**实盘** fill 腿 −0.0381bp，而我的模拟基准是 −0.4272bp？（差 0.39bp/笔）

# A 的动机

H71 的 Ridge：样本外 IC **+0.0534**、分档**单调**，但
最好档 +0.18bp / 最差档 −1.00bp ⇒ 只能造出 ~1.2bp 的跨度，
跳过 50% 成交后仍为 −0.24bp ⇒ 按事先判据作废。

**但那测的是"预测平均值"（回归）。** 尾部事件可能没有被回归捕捉 ——
因为回归最小化平方误差，会被中间那 95% 的样本主导 ✗。
换成**分类**（预测"这笔是否落在最亏的 q 分位"）：
  · 目标函数直接对准尾部 ⇒ 尾部若有可辨识签名，分类器更容易抓到
  · 输出是**概率** ⇒ 可以直接做"跳过概率 > p 的成交"

# B 的动机（可能更重要）

| | 每笔均值 |
|---|---|
| H71 模拟基准（对称双边挂单） | **−0.4272bp** |
| 实盘 fill 腿（H70，82 笔） | **−0.0381bp** |

**实盘比我的模型好 0.39bp/笔。** 若这个差是真的，那"实盘引擎已经做到了
我的模拟没建模的事"（库存偏斜 / 趋势偏斜 / 单边许可 / F281 新鲜 mid）——
**那才是当前最大的价值来源**，比再抠 ML 强。
⇒ 本脚本用**同一口径**在更长的实盘窗口上复算，确认这个差是否稳固。

判据（事先定死）：
  A. 分类器在"最高风险分位"的**实际均亏**显著低于基准，且
     **跳过该分位后均值由负转正** ⇒ 可用；只改善不转正 ⇒ 作废
  B. 实盘与模拟的差若 >0.3bp 且样本 ≥300 ⇒ 值得专项研究实盘做对了什么

用法：
    .venv\\Scripts\\python.exe scripts\\h72_tail_classifier_and_live_gap.py --hours 72
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

OUT = ROOT / "research_l1" / "out" / "h72_tail_clf.json"


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=72.0)
    ap.add_argument("--symbols", default="BTC,ETH,SOL,XRP,DOGE")
    a = ap.parse_args()

    import numpy as np

    print("=" * 104)
    print("H72-A  尾部预测：回归 → **分类**")
    print("=" * 104)

    sys.path.insert(0, str(ROOT / "scripts"))
    from h71_tail_ml import STEP_MS, build, walk_forward_eval

    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    per = {}
    for s in syms:
        d = build(s, a.hours, 0.9, 0.05, 0.0, 30.0)
        if d and len(d["X"]) > 500:
            per[d["symbol"]] = d
    if not per:
        print("无数据")
        return 1
    X = np.vstack([d["X"] for d in per.values()])
    y = np.concatenate([d["y"] for d in per.values()])
    m = np.isfinite(y) & np.isfinite(X).all(1)
    X, y = X[m], y[m]
    print(f"  样本 {len(y):,} 笔成交   基准均值 {y.mean():+.4f}bp  "
          f"中位 {np.median(y):+.4f}  胜率 {(y>0).mean():.4f}")

    # 尾部阈值：最亏 5% / 10%
    for tail_q in (0.95, 0.90):
        thr = float(np.percentile(y, (1 - tail_q) * 100))
        lab = (y <= thr).astype(int)
        print(f"\n  尾部定义：净额 ≤ {thr:+.4f}bp（最亏 {tail_q*100:.0f}%，"
              f"占比 {lab.mean():.4f}）")

        n = len(y)
        start = int(n * 0.40)
        fl = int((n - start) / 6)
        proba = np.full(n, np.nan)
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
        for k in range(6):
            aa, bb = start + k * fl, start + (k + 1) * fl
            tr = np.arange(0, aa); te = np.arange(aa, min(bb, n))
            if len(tr) < 200 or len(te) < 50:
                continue
            sc = StandardScaler().fit(X[tr])
            clf = LogisticRegression(max_iter=400, C=0.5)
            clf.fit(sc.transform(X[tr]), lab[tr])
            proba[te] = clf.predict_proba(sc.transform(X[te]))[:, 1]
        ok = np.isfinite(proba)
        if ok.sum() < 500:
            print("    样本不足")
            continue
        p_, y_ = proba[ok], y[ok]
        # 风险分位：概率最高 = 最可能巨亏
        order = np.argsort(-p_)
        print(f"\n    样本外 n={len(y_):,}   "
              f"corr(风险概率, 实际净额) = {np.corrcoef(p_, y_)[0,1]:+.4f}")
        print(f"\n    {'风险档':>7} {'n':>7} {'实际均值':>10} {'中位':>9} "
              f"{'胜率':>7} {'最亏':>10} {'尾部命中率':>11}")
        print("    " + "-" * 66)
        for i in range(5):
            lo = int(len(order) * i / 5); hi = int(len(order) * (i + 1) / 5)
            sel = order[lo:hi]
            yy = y_[sel]
            hi_rate = float((yy <= thr).mean())
            print(f"    {i+1:>7} {len(yy):>7,} {yy.mean():>+10.4f} "
                  f"{np.median(yy):>+9.4f} {(yy>0).mean():>7.4f} {yy.min():>+10.3f} "
                  f"{hi_rate:>10.4f}")
        print(f"\n    {'跳过最高风险':>12} {'保留':>8} {'过滤后均值':>11} "
              f"{'中位':>9} {'胜率':>7} {'改善':>9}")
        print("    " + "-" * 64)
        for q in (0.0, 0.10, 0.20, 0.30, 0.50):
            keep = order[int(len(order) * q):]
            sel = y_[keep]
            mk = " ★转正" if sel.mean() > 0 else ""
            print(f"    {q*100:>11.0f}% {len(keep)/len(y_)*100:>7.1f}% "
                  f"{sel.mean():>+11.4f} {np.median(sel):>+9.4f} "
                  f"{(sel>0).mean():>7.4f} {sel.mean()-y_.mean():>+9.4f}{mk}")

    # ── B：实盘 vs 模拟的差 ──
    print("\n" + "=" * 104)
    print("H72-B  实盘 fill 腿 vs 模拟基准（同口径对照）")
    print("=" * 104)
    import psycopg2
    import psycopg2.extras
    cn = psycopg2.connect(_dsn("alpha_arena"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT amount_usd::float a, created_at,"
        "       metadata_json::jsonb->>'symbol' sym"
        "  FROM arbitrage_paper_ledgers"
        " WHERE account_id=101 AND action='paper_pnl'"
        "   AND metadata_json::jsonb->>'lane_id'='mm_asterdex'"
        "   AND COALESCE(metadata_json::jsonb->>'phase','')='fill'"
        "   AND created_at >= '2026-09-20 00:00:00'")
    rows = cur.fetchall()
    # 每笔名义随时间变化（杠杆改了），按窗口分段用对应 notional
    cn.close()
    if rows:
        import datetime
        lev_at = datetime.datetime(2026, 9, 21, 1, 38, 0)
        pre = [r for r in rows if r["created_at"] < lev_at]
        post = [r for r in rows if r["created_at"] >= lev_at]
        for lab, sub, N in (("杠杆前（$28.08/腿）", pre, 28.08),
                            ("杠杆后（$140.2/腿）", post, 140.30)):
            if not sub:
                continue
            arr = np.array([r["a"] for r in sub]) / N * 1e4
            se = arr.std(ddof=1) / np.sqrt(len(arr)) if len(arr) > 1 else float("nan")
            print(f"\n  【{lab}】n={len(arr):,}")
            print(f"    均值 {arr.mean():>+9.4f}bp  标准误 {se:.4f}  "
                  f"95%CI [{arr.mean()-1.96*se:+.4f}, {arr.mean()+1.96*se:+.4f}]")
            print(f"    中位 {np.median(arr):>+9.4f}  胜率 {(arr>0).mean():.4f}  "
                  f"p5 {np.percentile(arr,5):+.3f}  最亏 {arr.min():+.3f}")
    print(f"\n  【模拟基准（H71/H72-A，对称双边）】均值 {y.mean():+.4f}bp  "
          f"中位 {np.median(y):+.4f}  胜率 {(y>0).mean():.4f}")
    print("\n  判读：实盘与模拟的差若 >0.3bp 且实盘样本 ≥300 ⇒ "
          "**值得专项研究实盘做对了什么**（库存/趋势偏斜、单边许可、F281 新鲜 mid）")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(
        {"hours": a.hours, "n": int(len(y)),
         "sim_mean": round(float(y.mean()), 4),
         "sim_median": round(float(np.median(y)), 4),
         "sim_win": round(float((y > 0).mean()), 4)},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[H72] 写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
