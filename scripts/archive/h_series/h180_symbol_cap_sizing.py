# -*- coding: utf-8 -*-
"""[H180 2026-09-21] 单币上限该定多少 —— 先量"每个仓位实际堆了几条腿"。

# 为什么不能直接设 1.5

`max_net_directional_ratio` 是**单币名义上限 = ratio × 权益**。
而单腿名义 = `compound_ratio × 权益` = **2.0 × 权益**（当前 compound_ratio=2.0）。

| ratio | 单币上限 | 单腿 $599 能否装下 |
|---|---|---|
| **1.5** | $449 | **装不下 ⇒ 加仓侧被永久封死 ⇒ 做市停摆** ✗ |
| 2.0 | $599 | 刚好一条腿 |
| 2.5 | $748 | 一条腿 + 一次追加 |
| 6.0（现状） | $1,797 | 3 条腿 ← 用户观察到的问题 |

⇒ **上限必须与单腿尺寸对齐**，否则要么封死、要么失控。

# 本脚本量什么

  · 本时代每个周期的**峰值持仓 / 单腿名义** = "堆了几条腿"的分布
  · 若绝大多数 ≤ 1 条腿 ⇒ 上限设 2.5~3.0 足够（既允许正常往返、又禁止累积）
  · 若经常 > 2 条腿 ⇒ 说明累积是常态，上限要更接近 2.0

# 判据（事先定死）

  · 取"p90 峰值腿数 + 0.5"作为**最小可用上限**（保证 p90 的周期不被封）
  · 上限 = 该值 × compound_ratio（换算回 ratio 口径）

用法：
    .venv\\Scripts\\python.exe scripts\\h180_symbol_cap_sizing.py
"""
from __future__ import annotations

import json
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from h84_derive_episodes import derive, load  # noqa: E402
import psycopg  # noqa: E402


def stats_since() -> str:
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
    with psycopg.connect(url) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json->>'stats_since' FROM lane_registry"
                        " WHERE lane_id='mm_asterdex'")
            return (cur.fetchone() or [None])[0] or ""


def main() -> int:
    j = json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
    lim, par = j.get("limits") or {}, j.get("params") or {}
    eq = float(j.get("equity") or 0.0)
    cr = float(par.get("compound_ratio") or 0.0)
    leg = cr * eq
    cur_cap = float(lim.get("max_net_directional_ratio") or 0.0)

    print("=" * 96)
    print("H180  单币上限该定多少")
    print("=" * 96)
    print(f"  权益 ${eq:,.2f}   compound_ratio={cr} ⇒ 单腿名义 **${leg:,.2f}**")
    print(f"  当前单币上限 ratio={cur_cap} ⇒ ${cur_cap*eq:,.2f}"
          f" = **{cur_cap*eq/leg if leg else 0:.2f} 条腿**")

    since = stats_since()
    eps = [e for e in derive(load()) if (e.get("t0") or "") >= since]
    eps = [e for e in eps if e.get("notional")]
    print(f"\n  本时代周期 {len(eps)} 个（stats_since={since}）")
    if not eps:
        print("  样本不足")
        return 1

    # 峰值名义 / 单腿 = 峰值腿数
    legs = sorted(e["notional"] / leg for e in eps if leg > 0)
    n = len(legs)
    print(f"\n  ── 峰值持仓折算成「单腿数」的分布（n={n}）──")
    for q in (0.5, 0.75, 0.9, 0.95, 0.99):
        print(f"    p{int(q*100):<3} {legs[min(n-1, int(q*n))]:>6.2f} 条腿")
    print(f"    最大 {legs[-1]:>6.2f} 条腿")

    over = {x: sum(1 for v in legs if v > x) / n * 100 for x in (1.0, 1.5, 2.0, 3.0)}
    print(f"\n  ── 超过各档的占比 ──")
    for x, pct in over.items():
        print(f"    > {x:.1f} 条腿：**{pct:>5.1f}%** 的周期")

    p90 = legs[min(n - 1, int(0.90 * n))]
    need_legs = p90 + 0.5
    need_ratio = need_legs * cr
    print(f"\n  ── 建议 ──")
    print(f"    p90 峰值 = {p90:.2f} 条腿 ⇒ 最小可用上限 = {need_legs:.2f} 条腿")
    print(f"    换算成 ratio 口径：**{need_ratio:.2f}**"
          f"（= {need_legs:.2f} × compound_ratio {cr}）")
    print(f"    这会封住约 **{over.get(2.0, 0):.1f}%** 的周期（>2 条腿的那些）")

    print(f"\n  ── 候选方案对照 ──")
    print(f"    {'ratio':>6} {'单币上限$':>11} {'折合腿数':>9} {'封住的周期%':>12} 说明")
    print("    " + "-" * 66)
    for r in (1.5, 2.0, 2.5, 3.0, 4.0, 6.0):
        cap = r * eq
        l = cap / leg if leg else 0.0
        blocked = sum(1 for v in legs if v > l) / n * 100
        note = ""
        if l < 1.0:
            note = "✗ 装不下一条腿 ⇒ 加仓侧永久封死"
        elif l < 1.5:
            note = "⚠️ 仅容一条腿（正常往返可以，累积被禁）"
        elif abs(r - need_ratio) < 0.35:
            note = "✓ 建议值（≥p90 峰值 + 0.5）"
        elif r >= 6.0:
            note = "← 现状（允许堆 3 条腿）"
        print(f"    {r:>6} {cap:>11,.0f} {l:>9.2f} {blocked:>11.1f}%  {note}")

    print(f"\n  ⚠️ 注意：收紧上限**不会卡死出库** —— `check_side_allowed` 第 1220 行")
    print(f"     对减仓方向 `return True, \"reduce\"`，永远允许。收紧只拦**加仓侧**。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
