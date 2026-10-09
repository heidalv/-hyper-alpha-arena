"""H91：H88 的分档到底是"绝对 edge"还是"相对自身正常价差"？—— 决定 F288 该不该上。

# H90 暴露的矛盾（关键）

用车道 11 个币的真实价差算 `edge = 0.9 × 半价差`：

| 币 | 价差bp | edge_bp | 落在 H88 哪一档 |
|---|---|---|---|
| SOL | 0.92 | 0.4160 | [0.320, 0.512) |
| DOGE | 1.17 | 0.5285 | [0.512, 0.641) |
| ASTER | 1.37 | 0.6167 | [0.512, 0.641) |
| XRP | 1.45 | 0.6502 | **[0.641, ∞)** ← 第二差的档 |
| UNI…VIRTUAL | 5.8~20.2 | 2.6~9.1 | **[0.641, ∞)** |
| ⇒ **8/11 个币都在最宽档** |

**⇒ `edge_bp = spread_mult × 半价差` 是"币种属性"的线性函数**：
宽价差币的 edge 天然就大。于是 H88 的**绝对**分档主要是在区分**币种**，
而不是区分"我们把报价挂得离 mid 多远"。

**H89 的币内检验用的是"每币中位数二分"**，那只证明"同一币内、相对自身正常的
宽/窄有区别"，**并不能支撑"设一个绝对下限（如 2.2bp）"**。

而 H90 显示 `F=1.2` 会抬升 **11/11** 个币 —— 那已经不是"抬地板"，
而是**整体放宽**，与 H88"最宽档也差"的结论直接冲突。

# 本脚本要回答的问题

**edge 的效应是"绝对水平"还是"相对该币自身正常水平"？**

  A. 把每个币的 edge **按自身分布标准化**（z 分数或分位），再分档看结果
     · 若标准化后仍单调 ⇒ 效应是**相对**的 ⇒ 应该用"相对自身"的参数，
       **不该设绝对 bp 下限**
  B. 用**同一币内**的绝对 edge 分档（不看跨币）
     · 若币内绝对 edge 与结果无关 ⇒ 进一步支持"相对"解释

判据（事先定死）：
  · 若效应是"相对自身" ⇒ **F288 不按绝对下限上线**；改为"相对自身中位的倍数"
  · 若效应是"绝对"且在币内也成立 ⇒ F288 可用绝对下限
  · 若两者都不明显 ⇒ **不上 F288**（如实作废，不留半成品）

用法：
    .venv\\Scripts\\python.exe scripts\\h91_edge_absolute_or_relative.py
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
    print("H91  edge 效应：「绝对水平」还是「相对自身正常价差」？")
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
        pnl, n = 0.0, 0
        for t in {t for t in e["fillts"] if t}:
            for ds in (0, 1, -1, 2):
                got = led_by.get((e["sym"], int(t) + ds))
                if got:
                    pnl += sum(got); n += len(got); break
        if n == 0:
            continue
        recs.append({"edge": edge, "pnl": pnl, "flat": e["flat"], "sym": e["sym"]})
    if len(recs) < 80:
        print(f"  可用周期仅 {len(recs)}")
        return 0

    import numpy as np
    edges = np.array([r["edge"] for r in recs])
    pnls = np.array([r["pnl"] for r in recs])
    flats = np.array([1 if r["flat"] else 0 for r in recs])
    syms = np.array([r["sym"] for r in recs])
    print(f"  周期 {len(recs)}   币种 {len(set(syms))}")

    # ── A. 相对自身标准化后的分档 ──
    print("\n" + "=" * 100)
    print("A. 把 edge 按**每个币自身分布**标准化（z 分数），再分档")
    print("=" * 100)
    z = np.full(len(edges), np.nan)
    for s in set(syms):
        m = syms == s
        if m.sum() < 8 or edges[m].std() == 0:
            continue
        z[m] = (edges[m] - edges[m].mean()) / edges[m].std()
    okz = np.isfinite(z)
    if okz.sum() < 60:
        print("  标准化样本不足")
    else:
        zz, pp, ff = z[okz], pnls[okz], flats[okz]
        qs = np.percentile(zz, [20, 40, 60, 80])
        print(f"\n  {'z 区间（相对自身）':>24} {'周期':>6} {'强平率':>8} {'每周期净额':>12}")
        print("  " + "-" * 54)
        prev = None
        mono = True
        vals = []
        for lo, hi in [(-1e9, qs[0]), (qs[0], qs[1]), (qs[1], qs[2]),
                       (qs[2], qs[3]), (qs[3], 1e9)]:
            m = (zz >= lo) & (zz < hi)
            if m.sum() < 5:
                continue
            v = pp[m].mean(); vals.append(v)
            lab = f"[{lo:+.2f}, {hi:+.2f})" if lo > -1e8 else f"(-inf, {hi:+.2f})"
            print(f"  {lab:>24} {int(m.sum()):>6} {ff[m].mean()*100:>7.1f}% {v:>+12.5f}")
        if len(vals) >= 3:
            mono = all(vals[i] <= vals[i+1] + 0.02 for i in range(len(vals)-1))
            print(f"\n  ⇒ 相对分档单调性（允许 0.02 抖动）：{'✓ 单调' if mono else '✗ 非单调'}")
            print(f"     最差档 {min(vals):+.5f}   最好档 {max(vals):+.5f}   "
                  f"跨度 {max(vals)-min(vals):+.5f}")

    # ── B. 币内绝对 edge 分档 ──
    print("\n" + "=" * 100)
    print("B. **币内**绝对 edge 分档（只看同一币里的绝对高低）")
    print("=" * 100)
    print(f"\n  {'币':<10} {'周期':>6} {'低edge净额':>12} {'高edge净额':>12} "
          f"{'低edge':>9} {'高edge':>9} {'方向':>6}")
    print("  " + "-" * 70)
    cons, tot = 0, 0
    for s in sorted(set(syms)):
        m = syms == s
        if m.sum() < 12:
            continue
        e_, p_, f_ = edges[m], pnls[m], flats[m]
        med = np.median(e_)
        lo_m, hi_m = e_ <= med, e_ > med
        if lo_m.sum() < 4 or hi_m.sum() < 4:
            continue
        d = p_[hi_m].mean() - p_[lo_m].mean()
        cons += 1 if d > 0 else 0
        tot += 1
        print(f"  {s:<10} {int(m.sum()):>6} {p_[lo_m].mean():>+12.5f} "
              f"{p_[hi_m].mean():>+12.5f} {np.median(e_[lo_m]):>9.4f} "
              f"{np.median(e_[hi_m]):>9.4f} {'✓' if d>0 else '✗':>6}")
    print(f"\n  ⇒ 币内方向一致：{cons}/{tot}")

    # ── 判据 ──
    print("\n" + "=" * 100)
    print("判据")
    print("=" * 100)
    rel_ok = (okz.sum() >= 60) and mono if okz.sum() >= 60 else False
    abs_ok = tot > 0 and cons >= tot * 0.6
    print(f"\n  A（相对自身标准化后单调）：{'成立' if rel_ok else '不成立'}")
    print(f"  B（币内绝对 edge 同向）：{cons}/{tot} ⇒ {'成立' if abs_ok else '不成立'}")
    if abs_ok and not rel_ok:
        print("\n  ⇒ 效应是**绝对**的（币内成立、相对标准化后不单调）")
        print("     ⇒ **F288 可用绝对下限**")
    elif rel_ok:
        print("\n  ⇒ 效应是**相对自身**的")
        print("     ⇒ **F288 不该按绝对 bp 下限上线**；应改为「相对自身中位的倍数」")
        print("     ⇒ 而这恰好说明 H90 的发现（8/11 币在最宽档）不是问题：")
        print("        宽价差币的 edge 大是**正常的**，不代表挂得太宽")
    else:
        print("\n  ⇒ 两种解释都不明显 ⇒ **不上 F288**（如实作废）")
    print(f"\n  ⚠️ 无论结论如何，这都是观测证据；上线需随机化 A/B。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
