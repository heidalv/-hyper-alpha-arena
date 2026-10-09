# -*- coding: utf-8 -*-
"""[H155 2026-09-21] 仓位大小的风险量化 —— 单腿 100% 权益到底意味着什么。

# 为什么要量

用户在面板上看到「单腿名义 $299.38（100.0%）」并注意到"好像还是不对"。
这是 `compound_ratio = 1.0` 的结果：**每条腿用掉 100% 权益**。
3 个持仓时总名义 ≈ $900，对 $300 账户是 3 倍总杠杆。

用户明确说过"加杠杆测试、放大本金、不要怕亏"（模拟仓不花钱），
所以**不该自作主张降下来**；但必须把真实的尾部风险算清楚给他看。

# 算什么

用**账本的真实逐笔净额**重建权益曲线，给出：
  · 最大回撤（金额 / 百分比）
  · 单笔最差、p1 / p5 分位
  · 波动率（每笔标准差 → 折算到每小时，按实测笔/小时）
  · 当前 sizing 下的"最坏单周期"与 40bp 止损上界的关系

# 与止损上界的关系（关键）

`stop_loss_bp = 40` 是**每周期亏损上界**：
    上界美元 = 40bp × 单腿名义 = 40bp × (compound_ratio × 权益)
  ⇒ compound_ratio 每翻一倍，这个硬上界也翻一倍。
所以"杠杆"与"止损上界"是绑定的：**不能只调一个**。

用法：
    .venv\\Scripts\\python.exe scripts\\h155_sizing_risk.py
    .venv\\Scripts\\python.exe scripts\\h155_sizing_risk.py --since 2026-09-21T12:28:25
"""
from __future__ import annotations

import argparse
import statistics as st
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"


def dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env.get("DATABASE_URL", "")
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-09-21T12:28:25")
    ap.add_argument("--equity0", type=float, default=300.0)
    a = ap.parse_args()

    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT ts, net_bp * notional / 1e4 AS usd
                FROM lane_ledger
                WHERE lane_id=%s AND event='fill' AND ts >= %s
                ORDER BY ts
            """, (LANE, a.since))
            rows = cur.fetchall()
            cur.execute("""SELECT count(*) FROM lane_ledger
                           WHERE lane_id=%s AND event='fill' AND ts >= %s""",
                        (LANE, a.since))
            meta_since = a.since

    if not rows:
        print("  该窗口没有成交")
        return 1

    usd = [float(r[1] or 0.0) for r in rows]
    n = len(usd)

    # 权益曲线（从 equity0 起）
    eq = [a.equity0]
    for x in usd:
        eq.append(eq[-1] + x)
    peak = eq[0]
    mdd = 0.0
    mdd_at = None
    for i, v in enumerate(eq):
        peak = max(peak, v)
        dd = peak - v
        if dd > mdd:
            mdd, mdd_at = dd, i

    s = sorted(usd)
    def q(p):
        return s[min(n - 1, int(p * n))]

    print("=" * 88)
    print("H155  仓位大小的风险量化（基于账本真实逐笔净额）")
    print("=" * 88)
    print(f"  窗口起点 {meta_since}    成交 {n} 笔")
    print(f"  起始权益 ${a.equity0:.2f}   期末权益 ${eq[-1]:.2f}"
          f"   净变动 {eq[-1]-eq[0]:+.4f}")

    print(f"\n  ── 逐笔净额分布（USD）──")
    print(f"    最好   {max(usd):+.4f}")
    print(f"    p95    {q(0.95):+.4f}")
    print(f"    中位   {st.median(usd):+.4f}")
    print(f"    p5     {q(0.05):+.4f}")
    print(f"    p1     {q(0.01):+.4f}")
    print(f"    最差   {min(usd):+.4f}")
    print(f"    标准差 {st.pstdev(usd):.4f}")

    print(f"\n  ── 回撤 ──")
    print(f"    最大回撤 **${mdd:.4f}**（{(mdd/a.equity0)*100:.2f}% 起始权益）")
    if mdd_at is not None:
        print(f"    发生在第 {mdd_at} 笔后（{rows[min(mdd_at, n-1)][0]}）")

    # 折算到每小时（用实测笔/小时）
    import json
    try:
        jj = json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
        fph = jj.get("fills_per_hour") or 0.0
    except Exception:
        fph = 0.0
    if fph:
        hourly_sd = st.pstdev(usd) * (fph ** 0.5)
        print(f"\n  ── 波动率（按实测 {fph:.0f} 笔/小时折算）──")
        print(f"    每小时净额标准差 ≈ ${hourly_sd:.3f}")
        print(f"    （注意：这是**同一 sizing 下**的波动；换 sizing 会等比变化）")

    print(f"\n  ── 与止损上界的关系 ──")
    print(f"    当前 compound_ratio 决定单腿名义 = ratio × 权益")
    print(f"    每周期亏损硬上界 = 40bp × 单腿名义")
    print(f"    {'ratio':>7} {'单腿$':>10} {'上界/周期$':>12} {'占权益':>8} "
          f"{'3腿总名义$':>12}")
    print("    " + "-" * 54)
    for r in (0.25, 0.5, 1.0, 2.0, 3.0):
        leg = r * a.equity0
        print(f"    {r:>7} {leg:>10,.2f} {0.0040*leg:>12,.3f} "
              f"{0.0040*leg/a.equity0*100:>7.2f}% {3*leg:>12,.0f}")
    print(f"\n  ⇒ 当前 ratio=1.0 ⇒ 单腿上界 ${0.0040*a.equity0:.3f}、"
          f"3 腿总名义 ${3*a.equity0:,.0f}（**3 倍总杠杆**）")
    print(f"     ratio=0.5 ⇒ 单腿上界 ${0.0040*0.5*a.equity0:.3f}、"
          f"总名义 ${3*0.5*a.equity0:,.0f}（1.5 倍）")
    print(f"\n  ⚠️ 本窗口只有 {n} 笔，回撤统计**样本不足**，不要据此下结论；")
    print("     要看的是【上界随 ratio 等比变化】这个结构关系。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
