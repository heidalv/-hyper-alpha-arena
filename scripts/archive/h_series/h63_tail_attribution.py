"""H63：左尾来自哪条腿？入场 vs 出场 vs 强平（用账本 metadata 的 phase）

# 要验证的假设

实盘账本（H62，1,877 行）：
    均值 −3.1910bp   中位 **+1.2317bp**   胜率 0.7310
    最小 −164.84bp   p5 −27.41   p95 仅 +2.24
    最亏 5%（93 笔）占总亏损 **82.9%**
    剔除最亏 5% 后：每笔 **−0.5747bp**

⇒ **中位为正、胜率 73%，但被极少数巨亏笔吃光。**
**⇒ 我此前"挂宽 → 逆选择 → 均值漂移"的框架是错的（至少不是主因）。**

# 与引擎参数的对照（registry 实况）

| 参数 | 值 | 含义 |
|---|---|---|
| `stop_loss_bp` | **60.0** | 要亏到 −60bp 才触发止损 |
| `min_hold_seconds` | **30.0** | **前 30 秒禁止引擎主动平仓**（止损/超时/OFI 全被拦） |
| `max_one_side_seconds` | **300.0** | 持有上限 5 分钟 |

⇒ **假设**：巨亏笔是"止损腿"，而止损被 `min_hold_seconds=30` 延迟、
且阈值高达 60bp ⇒ 尾部被人为放大。

**注意**：`min_hold_seconds` 当初是我为了防"刚建仓就白付出场成本"加的
（F258，用户给定 30s–5min 窗口）。它在**正常行情**下是对的，
但在**尾部事件**下会把亏损放大一个量级 —— 这正是"用均值口径设计的参数被尾部反噬"。

# 本脚本怎么验

账本 `metadata_json` 里有 `phase` 字段（入场/出场/强平）。按 phase 拆：
  · 若巨亏笔**集中在出场/强平腿** ⇒ 出场是被动挨打，对策是"提前止损 + 事件回避"
  · 若巨亏笔**在入场腿就很大** ⇒ 入场判定本身有问题（报价位置/穿越）
  · 若两腿都有 ⇒ 是持仓期间的价格漂移，对策是"缩短暴露"

用法：
    .venv\\Scripts\\python.exe scripts\\h63_tail_attribution.py
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")


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

    cn = psycopg2.connect(_dsn("alpha_arena"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # 先看 metadata 里有哪些字段
    cur.execute(
        "SELECT metadata_json FROM arbitrage_paper_ledgers"
        " WHERE account_id=101 AND action='paper_pnl'"
        "   AND metadata_json::jsonb->>'lane_id'='mm_asterdex'"
        "   AND created_at >= '2026-09-20 00:00:00' LIMIT 300")
    keys = defaultdict(int)
    for r in cur.fetchall():
        for k in r["metadata_json"]:
            keys[k] += 1
    print("=" * 96)
    print("metadata_json 字段（09-20 起 mm_asterdex）")
    print("=" * 96)
    for k, v in sorted(keys.items(), key=lambda x: -x[1]):
        print(f"  {k:<24} {v}")

    # 按 phase 拆
    cur.execute(
        "SELECT COALESCE(metadata_json::jsonb->>'phase','(none)') ph,"
        "       count(*) n, COALESCE(SUM(amount_usd),0) s,"
        "       min(amount_usd)::float mn, max(amount_usd)::float mx"
        "  FROM arbitrage_paper_ledgers"
        " WHERE account_id=101 AND action='paper_pnl'"
        "   AND metadata_json::jsonb->>'lane_id'='mm_asterdex'"
        "   AND created_at >= '2026-09-20 00:00:00'"
        " GROUP BY 1 ORDER BY s")
    rows = cur.fetchall()
    print("\n" + "=" * 96)
    print("按 phase 拆（关键表）")
    print("=" * 96)
    print(f"\n  {'phase':<20} {'笔数':>8} {'合计USD':>12} {'单笔最小USD':>14} {'单笔最大USD':>14}")
    print("  " + "-" * 72)
    for r in rows:
        print(f"  {str(r['ph']):<20} {r['n']:>8,} {float(r['s']):>+12.4f} "
              f"{float(r['mn']):>+14.5f} {float(r['mx']):>+14.5f}")

    # 按 phase 算每笔 bp 的分布与尾部贡献
    NOTIONAL = 28.3195
    print("\n" + "=" * 96)
    print(f"按 phase 的每笔净额分布（notional=${NOTIONAL}）")
    print("=" * 96)
    for r in rows:
        ph = r["ph"]
        cur.execute(
            "SELECT amount_usd::float a FROM arbitrage_paper_ledgers"
            " WHERE account_id=101 AND action='paper_pnl'"
            "   AND metadata_json::jsonb->>'lane_id'='mm_asterdex'"
            "   AND created_at >= '2026-09-20 00:00:00'"
            "   AND COALESCE(metadata_json::jsonb->>'phase','(none)')=%s", (ph,))
        bp = np.array([x["a"] for x in cur.fetchall()]) / NOTIONAL * 1e4
        if len(bp) == 0:
            continue
        o = np.argsort(bp)
        k5 = max(1, int(len(bp) * 0.05))
        tot = bp.sum()
        print(f"\n  ── phase = {ph}   n={len(bp):,}")
        print(f"     均值 {bp.mean():>+9.4f}bp  中位 {np.median(bp):>+9.4f}bp  "
              f"胜率 {(bp>0).mean():.4f}")
        print(f"     p5 {np.percentile(bp,5):>+9.4f}  p95 {np.percentile(bp,95):>+9.4f}  "
              f"最小 {bp.min():>+10.4f}  最大 {bp.max():>+9.4f}")
        print(f"     最亏 5% ({k5} 笔) 合计 {bp[o[:k5]].sum():>+10.2f}bp"
              f"  占该组 {bp[o[:k5]].sum()/tot*100 if tot else float('nan'):>6.1f}%")

    # 全量：巨亏笔的 symbol / side 分布
    cur.execute(
        "SELECT amount_usd::float a,"
        "       metadata_json::jsonb->>'symbol' sym,"
        "       metadata_json::jsonb->>'side' sd,"
        "       COALESCE(metadata_json::jsonb->>'phase','(none)') ph,"
        "       created_at"
        "  FROM arbitrage_paper_ledgers"
        " WHERE account_id=101 AND action='paper_pnl'"
        "   AND metadata_json::jsonb->>'lane_id'='mm_asterdex'"
        "   AND created_at >= '2026-09-20 00:00:00'"
        " ORDER BY amount_usd LIMIT 40")
    print("\n" + "=" * 96)
    print("最亏的 40 笔（逐笔）")
    print("=" * 96)
    print(f"\n  {'时间':<22} {'币':<10} {'方向':<6} {'phase':<12} {'bp':>10} {'USD':>11}")
    print("  " + "-" * 76)
    for r in cur.fetchall():
        bp = float(r["a"]) / NOTIONAL * 1e4
        print(f"  {str(r['created_at'])[:21]:<22} {str(r['sym']):<10} {str(r['sd']):<6} "
              f"{str(r['ph']):<12} {bp:>+10.3f} {float(r['a']):>+11.5f}")

    cn.close()
    print("\n" + "=" * 96)
    print("判读：")
    print("  · 巨亏集中在出场/强平腿 ⇒ 出场被动挨打 + min_hold 延迟止损 ⇒ 对策：尾部止损例外")
    print("  · 巨亏在入场腿就大 ⇒ 报价位置判定有问题")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
