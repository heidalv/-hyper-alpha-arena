"""H108：砍币之前的结构性检验 —— 逐周期净额分布（中位 vs 尾部）。

# 为什么必须做这一步

H107 给出候选（净贡献 <−$5 且周期 ≥50）：**ARB / DOGE / SOL / XRP**，
合计 −$32.12（占整夜 −$52.22 的 61.5%）。

**但我的判据脚本自己写了警告：若中位为正、被几笔拉偏 ⇒ 不能砍。**
本项目已犯过多次"用均值代替分布"的错（第 15 条教训）。

# 本脚本对每个候选币算逐周期净额的**完整分布**

  · 周期数、均值、**中位**、p10、p25、p75、最亏、胜率
  · **尾部贡献**：最亏 5% 的周期占总亏损的比例
  · 判据：
      - 中位 < 0 **且** 尾部贡献 <60% ⇒ **结构性亏损** ⇒ 可砍
      - 中位 ≥ 0 或尾部贡献 >80% ⇒ 被少数笔拉偏 ⇒ **不能砍**

# 同时做一件更重要的事

把"强平率"与"每周期净额"放在一起，看**强平率是不是唯一的解释变量**：
  · 若强平率能解释绝大部分币间差异 ⇒ 调整方向明确：**降强平率（含只做低强平率的币）**
  · 若解释不了 ⇒ 还有别的因素

用法：
    .venv\\Scripts\\python.exe scripts\\h108_cut_candidates.py
"""
from __future__ import annotations

import os
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
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

NOTIONAL = 135.0
DAY = "2026-09-21 00:00:00"


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def main():
    import numpy as np
    import psycopg2
    import psycopg2.extras

    from h84_derive_episodes import derive, load

    print("=" * 104)
    print("H108  砍币前的结构性检验（逐周期净额分布）")
    print("=" * 104)

    rows_all = load()
    eps = derive(rows_all)
    # 账本 → (symbol, 秒) 金额
    cn = psycopg2.connect(_dsn("alpha_arena"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT created_at, amount_usd::float a,"
        "       metadata_json::jsonb->>'symbol' sym"
        "  FROM arbitrage_paper_ledgers"
        " WHERE account_id=101 AND action<>'create_account'"
        "   AND metadata_json::jsonb->>'lane_id'='mm_asterdex'"
        "   AND created_at >= %s", (DAY,))
    by = defaultdict(float)
    for r in cur.fetchall():
        by[(r["sym"], int(r["created_at"].timestamp()))] += r["a"]
    cn.close()

    def ep_pnl(e):
        t = 0.0
        for x in {y for y in e["fillts"] if y}:
            for ds in (0, 1, -1, 2):
                k = (e["sym"], int(x) + ds)
                if k in by:
                    t += by[k]
                    break
        return t

    bysym = defaultdict(list)
    for e in eps:
        bysym[e["sym"]].append(e)

    print(f"\n  {'币':<10} {'周期':>6} {'强平率':>8} {'均值USD':>11} {'中位':>10} "
          f"{'p10':>10} {'p75':>10} {'最亏':>10} {'胜率':>7} {'尾部占比':>9}")
    print("  " + "-" * 104)
    stat = {}
    for s in sorted(bysym, key=lambda k: -len(bysym[k])):
        es = bysym[s]
        if len(es) < 10:
            continue
        fr = sum(1 for e in es if e["flat"]) / len(es)
        v = np.array([ep_pnl(e) for e in es])
        v = v[np.isfinite(v)]
        if len(v) < 10:
            continue
        tot = v.sum()
        o = np.argsort(v)
        k5 = max(1, int(len(v) * 0.05))
        tail = v[o[:k5]].sum()
        tail_share = tail / tot * 100 if abs(tot) > 1e-12 else float("nan")
        stat[s] = {"n": len(es), "fr": fr, "mean": v.mean(),
                   "median": float(np.median(v)), "p10": float(np.percentile(v, 10)),
                   "p75": float(np.percentile(v, 75)), "worst": float(v.min()),
                   "win": float((v > 0).mean()), "tail": tail_share}
        print(f"  {s:<10} {len(es):>6} {fr*100:>7.1f}% {v.mean():>+11.5f} "
              f"{np.median(v):>+10.5f} {np.percentile(v,10):>+10.5f} "
              f"{np.percentile(v,75):>+10.5f} {v.min():>+10.4f} "
              f"{(v>0).mean()*100:>6.1f}% {tail_share:>8.1f}%")

    print("\n" + "=" * 104)
    print("判据：中位 < 0 且 尾部贡献 < 60% ⇒ 结构性 ⇒ 可砍")
    print("=" * 104)
    print(f"\n  {'币':<10} {'中位USD':>11} {'尾部占比':>9} {'强平率':>8} {'判定':<16}")
    print("  " + "-" * 60)
    cuts, keeps = [], []
    for s, d in sorted(stat.items(), key=lambda x: x[1]["mean"]):
        if d["median"] < 0 and (not np.isfinite(d["tail"]) or d["tail"] < 60):
            verdict = "**结构性 ⇒ 可砍**"
            cuts.append(s)
        elif d["median"] >= 0:
            verdict = "中位为正 ⇒ 不可砍"
            keeps.append(s)
        else:
            verdict = "尾部主导 ⇒ 不可砍"
            keeps.append(s)
        print(f"  {s:<10} {d['median']:>+11.5f} {d['tail']:>8.1f}% "
              f"{d['fr']*100:>7.1f}% {verdict:<16}")

    print(f"\n  ⇒ **可砍**：{cuts}")
    print(f"  ⇒ **不可砍**（中位为正或尾部主导）：{keeps}")

    # ── 强平率是否能解释币间差异 ──
    print("\n" + "=" * 104)
    print("强平率 vs 每周期净额：强平率是唯一解释变量吗？")
    print("=" * 104)
    frs = np.array([d["fr"] for d in stat.values()])
    mns = np.array([d["mean"] for d in stat.values()])
    if len(frs) >= 3 and frs.std() > 0 and mns.std() > 0:
        c = float(np.corrcoef(frs, mns)[0, 1])
        print(f"\n  corr(强平率, 每周期净额) = **{c:+.4f}**")
        # 线性拟合：每周期净额 ≈ a + b×强平率
        b, a = np.polyfit(frs, mns, 1)
        print(f"  拟合：每周期净额 ≈ {a:+.5f} + {b:+.5f} × 强平率")
        print(f"  ⇒ 斜率 {b:+.5f} USD/单位强平率"
              f"（即强平率每升 10pp，每周期净额变化 {b*0.1:+.5f} USD）")
        if abs(c) > 0.7:
            print(f"  ⇒ |corr| > 0.7 ⇒ **强平率是主要解释变量** ⇒ 方向明确：降强平率")
        else:
            print(f"  ⇒ |corr| ≤ 0.7 ⇒ 强平率**不足以**单独解释 ⇒ 还有别的因素")
    print(f"\n  ⚠️ 样本是整个币种（n={len(stat)}），corr 本身噪声大，只看方向。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
