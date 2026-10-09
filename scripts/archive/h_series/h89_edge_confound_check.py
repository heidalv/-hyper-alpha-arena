"""H89：H88 的 edge 分档是不是**币种混杂**造成的？（上 A/B 之前的必要检查）

# 为什么必须先查这个

H88（561 个真实周期）给出显著结果：
```
edge∈(-inf, 0.1071)  强平率 64.3%   每周期 −0.08610 USD   ← 最差
edge∈[0.1071, 0.320) 强平率 10.1%   每周期 +0.00410 USD   ← 最好
差 +0.09020 USD/周期，3.21 个标准误
```

**但这是观测数据。** 一个明显的混杂：**币种**。
`edge_bp = spread_mult × 半价差`，而半价差是**币种属性**：
  · 窄价差币（BTC 0.012bp、ETH 0.039bp）⇒ edge 极小 ⇒ 落进最差档
  · 宽价差币（VIRTUAL 20bp、SEI 19bp）⇒ edge 大 ⇒ 落进别的档

⇒ 若"最差档"其实就是"BTC/ETH 这些窄价差币"，
那么结论就不是"edge 太小不好"，而是"**这些币整体不好**"，
两者的对策**完全不同**（前者调 `spread_mult`，后者调**币种宇宙**）。

# 本脚本怎么区分

对每个币分别看：**该币的 edge 与结果是否也有同样的关系**。
  · 若**币内**仍有 edge→结果 的单调关系 ⇒ edge 是独立因素（可调参）
  · 若币内关系消失、只剩币间差异 ⇒ **是币种混杂** ⇒ 该调宇宙而不是调宽度

同时报：每个币在 H88 五档里的分布（看是否某个币独占最差档）。

判据（事先定死）：
  · 若最差档中**单一币占比 > 60%** ⇒ 币种混杂为主 ⇒ **先调宇宙**
  · 若最差档**跨多个币**且币内仍有关系 ⇒ edge 是独立因素 ⇒ **可调 spread_mult**

用法：
    .venv\\Scripts\\python.exe scripts\\h89_edge_confound_check.py
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

    print("=" * 100)
    print("H89  H88 的 edge 分档是否被币种混杂？")
    print("=" * 100)

    rows = load()
    eps = derive(rows)
    by_key = defaultdict(list)
    for r in rows:
        by_key[(r.get("symbol"), r.get("ts"))].append(r)

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

    recs = []
    for e in eps:
        firsts = by_key.get((e["sym"], e.get("ts0"))) or []
        if not firsts:
            continue
        edge = float(firsts[0].get("edge_bp") or 0.0)
        half0 = None
        m0 = float(firsts[0].get("engine_mid") or 0)
        pnl, n = 0.0, 0
        for t in {t for t in e["fillts"] if t}:
            for ds in (0, 1, -1, 2):
                got = led_by.get((e["sym"], int(t) + ds))
                if got:
                    pnl += sum(got); n += len(got); break
        if n == 0:
            continue
        # 反推该周期入场时的半价差：edge = spread_mult × half  ⇒ half = edge / spread_mult
        # （用 0.9 作近似；只用于看"edge 小是否等价于价格差币"）
        recs.append({"edge": edge, "pnl": pnl, "flat": e["flat"],
                     "sym": e["sym"], "n": e["n"],
                     "half_est": edge / 0.9 if edge > 0 else 0.0})
    if len(recs) < 50:
        print(f"  可用周期仅 {len(recs)}")
        return 0

    edges = np.array([r["edge"] for r in recs])
    pnls = np.array([r["pnl"] for r in recs])
    flats = np.array([1 if r["flat"] else 0 for r in recs])
    syms = np.array([r["sym"] for r in recs])
    print(f"  周期 {len(recs)}   币种 {len(set(syms))}")

    qs = np.percentile(edges, [20, 40, 60, 80])
    lo0 = -1e9
    worst = (edges < qs[0])
    print(f"\n  最差档定义：edge < {qs[0]:.4f}（{int(worst.sum())} 个周期）")

    # ── 1) 最差档的币种构成 ──
    print("\n" + "=" * 100)
    print("1) 最差档的币种构成（判据：单一币 >60% ⇒ 币种混杂）")
    print("=" * 100)
    cnt = defaultdict(int)
    for s in syms[worst]:
        cnt[s] += 1
    tot = int(worst.sum())
    print(f"\n  {'币':<10} {'最差档周期':>10} {'占比':>8}")
    print("  " + "-" * 32)
    for s, c in sorted(cnt.items(), key=lambda x: -x[1]):
        print(f"  {s:<10} {c:>10} {c/max(tot,1)*100:>7.1f}%")
    top_share = max(cnt.values()) / max(tot, 1) if cnt else 0
    print(f"\n  ⇒ 最大单一币占比 {top_share*100:.1f}%")

    # ── 2) 全部币的 edge 中位（看 edge 是否就是币种属性）──
    print("\n" + "=" * 100)
    print("2) 各币的 edge 中位与结果（edge 是否只是币种属性？）")
    print("=" * 100)
    print(f"\n  {'币':<10} {'周期':>6} {'edge中位':>10} {'强平率':>8} {'每周期净额':>12}")
    print("  " + "-" * 52)
    rowsout = []
    for s in sorted(set(syms)):
        m = syms == s
        if m.sum() < 5:
            continue
        rowsout.append((s, int(m.sum()), float(np.median(edges[m])),
                        float(flats[m].mean()), float(pnls[m].mean())))
        print(f"  {s:<10} {int(m.sum()):>6} {np.median(edges[m]):>10.4f} "
              f"{flats[m].mean()*100:>7.1f}% {pnls[m].mean():>+12.5f}")

    # ── 3) 币内检验：固定一个币，看 edge 与结果的关系 ──
    print("\n" + "=" * 100)
    print("3) **币内**检验：同一个币里，edge 高低是否仍与结果相关")
    print("=" * 100)
    print(f"\n  {'币':<10} {'低edge组净额':>14} {'高edge组净额':>14} {'低组强平':>9} "
          f"{'高组强平':>9} {'方向一致?':>10}")
    print("  " + "-" * 74)
    consistent, total_checked = 0, 0
    for s, n, med, fr, pn in rowsout:
        m = syms == s
        if m.sum() < 12:
            continue
        e_ = edges[m]; p_ = pnls[m]; f_ = flats[m]
        med_e = np.median(e_)
        lo_m = e_ <= med_e
        hi_m = e_ > med_e
        if lo_m.sum() < 4 or hi_m.sum() < 4:
            continue
        d = p_[hi_m].mean() - p_[lo_m].mean()
        okk = "✓" if d > 0 else "✗"
        if d > 0:
            consistent += 1
        total_checked += 1
        print(f"  {s:<10} {p_[lo_m].mean():>+14.5f} {p_[hi_m].mean():>+14.5f} "
              f"{f_[lo_m].mean()*100:>8.1f}% {f_[hi_m].mean()*100:>8.1f}% {okk:>10}")

    print("\n" + "=" * 100)
    print("判据")
    print("=" * 100)
    print(f"\n  最差档最大单一币占比：{top_share*100:.1f}%")
    print(f"  币内方向一致（高 edge 更好）的币：{consistent}/{total_checked}")
    if top_share > 0.60:
        print("\n  ⇒ 最差档由单一币主导 ⇒ **币种混杂为主**")
        print("     ⇒ 对策是**调币种宇宙**（去掉那个币），而不是调 `spread_mult`")
    elif total_checked and consistent >= total_checked * 0.6:
        print("\n  ⇒ 最差档跨多个币，且**币内**多数也呈现「edge 太低更差」")
        print("     ⇒ **edge 是独立因素** ⇒ 可以调 `spread_mult` / 加最小 edge 下限")
    else:
        print("\n  ⇒ 证据混杂，不足以区分两种解释")
        print("     ⇒ **不调参**；先做 A/B 实测（随机化才能分离）")
    print(f"\n  ⚠️ 提醒：无论如何，这仍是**观测**证据。")
    print(f"     要确定因果，唯一办法是**随机化 A/B**（不同币/时段随机用不同 spread_mult）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
