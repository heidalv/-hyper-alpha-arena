"""H88：用**实盘周期数据**找最优挂单距离 —— 不需要任何模拟的成交模型。

# 为什么这次能成（与前三次失败的区别）

H74 / H87 都想用**模拟的成交判定**测挂单距离，两次都因判据退化而作废
（H87 输出每个距离都 ≥99.9%，因为"主动买打在卖一"几乎必然发生）。
**根因：我没有真实队列/成交机制，任何模拟判据都会退化。**

H84 之后，**实盘数据本身就有答案**：
  · `fill_basis` 每个周期有入场半宽 `edge_bp`（= 挂单距离，实测中位 0.4083）
  · **账本**有每笔的 `amount_usd`（真实美元盈亏）
  · ⇒ 把账本按 (symbol, 时间) 归到 H84 周期上，即可按 `edge_bp` 分档看**真实结果**

H86 已证 `edge_bp` 分档下强平率单调（0.070 以下 61.6% vs 0.32~0.485 为 8.9%）
⇒ 该变量与结果有真实关联。

# 判据（事先定死）

  · 若最好档与最差档的每周期净额差 **> 2 个合并标准误** ⇒ 值得把挂单距离推向最好档
  · 若差异在噪声内 ⇒ **不做没有证据的调参**
  · 每档样本 < 30 ⇒ 只报方向

用法：
    .venv\\Scripts\\python.exe scripts\\h88_optimal_edge_live.py
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
    print("H88  用实盘周期数据找最优挂单距离（无模拟）")
    print("=" * 104)

    rows = load()
    eps = derive(rows)
    if not eps:
        print("无周期")
        return 1
    # 入场 edge_bp（每周期第一条记录的）
    by_key = defaultdict(list)
    for r in rows:
        by_key[(r.get("symbol"), r.get("ts"))].append(r)

    # ── 账本：拿到 (symbol, 时间戳秒) -> 金额列表 ──
    cn = psycopg2.connect(_dsn("alpha_arena"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT created_at, amount_usd::float a,"
        "       metadata_json::jsonb->>'symbol' sym"
        "  FROM arbitrage_paper_ledgers"
        " WHERE account_id=101 AND action='paper_pnl'"
        "   AND metadata_json::jsonb->>'lane_id'='mm_asterdex'"
        "   AND created_at >= '2026-09-21 00:00:00'")
    led = cur.fetchall()
    cn.close()
    led_by = defaultdict(list)
    for r in led:
        led_by[(r["sym"], int(r["created_at"].timestamp()))].append(r["a"])
    print(f"  fill_basis 记录 {len(rows):,}   推导周期 {len(eps)}   账本行 {len(led):,}")

    recs = []
    unmatched = 0
    for e in eps:
        firsts = by_key.get((e["sym"], e.get("ts0"))) or []
        if not firsts:
            continue
        edge = float(firsts[0].get("edge_bp") or 0.0)
        pnl, n = 0.0, 0
        for t in {t for t in e["fillts"] if t}:
            sec = int(t)
            for ds in (0, 1, -1, 2):     # 账本落库可能晚 1~2 秒
                got = led_by.get((e["sym"], sec + ds))
                if got:
                    pnl += sum(got)
                    n += len(got)
                    break
        if n == 0:
            unmatched += 1
            continue
        recs.append({"edge": edge, "pnl": pnl, "flat": e["flat"], "n": e["n"],
                     "dur": e["dur_s"], "sym": e["sym"]})
    print(f"  能对齐到账本的周期 {len(recs)}（未对齐 {unmatched}）")
    if len(recs) < 50:
        print("  ⇒ 可对齐样本不足，无法分档")
        return 0

    edges = np.array([r["edge"] for r in recs])
    pnls = np.array([r["pnl"] for r in recs])
    flats = np.array([1 if r["flat"] else 0 for r in recs])
    print(f"\n  edge_bp：中位 {np.median(edges):.4f}  "
          f"p10 {np.percentile(edges,10):.4f}  p90 {np.percentile(edges,90):.4f}")
    print(f"  每周期净额 USD：均值 {pnls.mean():+.5f}  中位 {np.median(pnls):+.5f}  "
          f"合计 {pnls.sum():+.5f}")
    print(f"  周期级强平率 {flats.mean()*100:.1f}%")

    qs = np.percentile(edges, [20, 40, 60, 80])
    bins = [(-1e9, qs[0]), (qs[0], qs[1]), (qs[1], qs[2]), (qs[2], qs[3]), (qs[3], 1e9)]
    print("\n" + "=" * 104)
    print("按入场 edge_bp 分档（五等分）—— **真实结果，无模拟**")
    print("=" * 104)
    print(f"\n  {'edge_bp 区间':>22} {'周期':>6} {'强平率':>8} {'每周期净额USD':>14} "
          f"{'标准误':>9} {'笔/周期':>8}")
    print("  " + "-" * 76)
    tab = []
    for lo, hi in bins:
        m = (edges >= lo) & (edges < hi)
        if m.sum() < 5:
            continue
        sub = pnls[m]
        se = sub.std(ddof=1) / np.sqrt(len(sub)) if len(sub) > 1 else float("nan")
        lab = (f"[{lo:.4f}, {hi:.4f})" if lo > -1e8 else f"(-inf, {hi:.4f})")
        tab.append({"lo": float(lo), "hi": float(hi), "n": int(m.sum()),
                    "flat": float(flats[m].mean()), "pnl": float(sub.mean()),
                    "se": float(se), "nfill": float(np.median([r["n"] for r, k in zip(recs, m) if k]))})
        print(f"  {lab:>22} {int(m.sum()):>6} {flats[m].mean()*100:>7.1f}% "
              f"{sub.mean():>+14.5f} {se:>9.5f} {tab[-1]['nfill']:>8.0f}")

    print("\n" + "=" * 104)
    print("判据")
    print("=" * 104)
    if len(tab) >= 2:
        best = max(tab, key=lambda x: x["pnl"])
        worst = min(tab, key=lambda x: x["pnl"])
        diff = best["pnl"] - worst["pnl"]
        pooled = np.sqrt(best["se"] ** 2 + worst["se"] ** 2)
        print(f"\n  全体每周期净额 {pnls.mean():+.5f} USD（n={len(recs)}）")
        print(f"  最好档 edge∈[{best['lo']:.4f}, {best['hi']:.4f})  "
              f"净额 {best['pnl']:+.5f}  强平率 {best['flat']*100:.1f}%  n={best['n']}")
        print(f"  最差档 edge∈[{worst['lo']:.4f}, {worst['hi']:.4f})  "
              f"净额 {worst['pnl']:+.5f}  强平率 {worst['flat']*100:.1f}%  n={worst['n']}")
        print(f"\n  最好−最差 = {diff:+.5f} USD/周期   合并标准误 {pooled:.5f}  "
              f"比值 {abs(diff)/max(pooled,1e-12):.2f}")
        if abs(diff) > 2 * pooled:
            print("  ⇒ **> 2 SE ⇒ 显著** ⇒ 值得把挂单距离推向最好档")
        else:
            print("  ⇒ 在噪声内 ⇒ **不做没有证据的调参**")
        print(f"\n  ⚠️ 这是**观测**数据不是随机对照；edge 与币种/行情混杂，")
        print(f"     结论只能作为**方向性**建议，需 A/B 实测确认。")

    print("\n" + "=" * 104)
    print("与当前参数的关系")
    print("=" * 104)
    print("  · 引擎 `spread_mult=0.9` ⇒ 报价半宽 = 0.9 × 半价差(bp) = 上表的 edge_bp")
    print("  · 若最好档明显偏离当前水平 ⇒ 调整 `spread_mult`，然后**用周期口径 A/B 实测**")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
