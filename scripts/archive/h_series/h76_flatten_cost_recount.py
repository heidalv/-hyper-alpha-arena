"""H76：强平成本到底怎么算才对？

# 起因：`position_id` 语义与我的假设不符

`logs/mm_fill_basis.jsonl` 实测：**1,447 条记录只有 40 个不同 position_id**，
最高频的是 `mm:ASTER:1` 占 392 条。
⇒ `position_id` 是**每币累计序号**（一个连续序列），**不是一次往返**。

这条影响我 H72 的口径：我按 `phase` 聚合出
"flatten n=65，合计 −$11.09，每笔 −12.73bp"，
并据此声称"11.5% 的笔数贡献 88% 的亏损"。

**若 65 条 flatten 其实只属于少数几个仓位周期，那么"每笔强平成本"这个说法
就是错的**（分母错了）。这正是本项目已犯过多次的"分母/口径"错误，必须先查清。

# 本脚本查什么

  1. 账本里 65 条 flatten 分布在多少个 `position_id` 上？
  2. 每个 position_id 的**周期净额**（把该 id 的所有 fill+flatten 加起来）
  3. 按"周期"重算：强平周期占比、每周期净额、非强平周期净额
  4. 给出**两种口径**的结论（按笔 vs 按周期），并说明哪个用于决策

判据：
  · 若按周期算，非强平周期为正、强平周期为负 ⇒ 结论与 H72 方向一致，只是分母要改
  · 若非强平周期也为负 ⇒ H72 的"被动出库是赚的"结论错误，必须撤回

用法：
    .venv\\Scripts\\python.exe scripts\\h76_flatten_cost_recount.py
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

    cn = psycopg2.connect(_dsn("alpha_arena"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT created_at, amount_usd::float a,"
        "       metadata_json::jsonb->>'symbol' sym,"
        "       COALESCE(metadata_json::jsonb->>'phase','?') ph,"
        "       COALESCE(metadata_json::jsonb->>'position_id','(none)') pid"
        "  FROM arbitrage_paper_ledgers"
        " WHERE account_id=101 AND action='paper_pnl'"
        "   AND metadata_json::jsonb->>'lane_id'='mm_asterdex'"
        "   AND created_at >= '2026-09-21 01:38:00'"
        " ORDER BY created_at")
    rows = cur.fetchall()
    cn.close()
    if not rows:
        print("无数据")
        return 1

    print("=" * 96)
    print("H76  强平成本重算：按笔 vs 按仓位周期")
    print("=" * 96)
    print(f"  窗口 01:38 起   总记录 {len(rows):,}")

    # 先看 position_id 的可用性
    pids = [r["pid"] for r in rows]
    n_none = sum(1 for p in pids if p == "(none)")
    print(f"  position_id 非空率 {(len(pids)-n_none)/len(pids)*100:.1f}%"
          f"   不同 id 数 {len(set(pids))}")
    if n_none == len(pids):
        print("  ✗ 账本 metadata 里没有 position_id，无法按周期聚合")
        print("    ⇒ 只能用**按笔**口径，但必须说明这一点")
        return 1

    # ── 口径 A：按笔（H72 用的）──
    fills = [r for r in rows if r["ph"] == "fill"]
    flats = [r for r in rows if r["ph"] == "flatten"]
    sf = sum(r["a"] for r in fills)
    sl = sum(r["a"] for r in flats)
    print(f"\n  ── 口径 A：按笔（每条 ledger 行一笔）──")
    print(f"     fill    n={len(fills):>4}  sum={sf:>+9.4f}"
          f"   avg={sf/max(len(fills),1):>+8.5f}")
    print(f"     flatten n={len(flats):>4}  sum={sl:>+9.4f}"
          f"   avg={sl/max(len(flats),1):>+8.5f}")
    print(f"     强平笔数占比 {len(flats)/max(len(rows),1)*100:.1f}%"
          f"   强平亏损占比 {sl/min(sf+sl,-1e-9)*100:.1f}%")

    # ── 口径 B：按仓位周期 ──
    ep = defaultdict(list)
    for r in rows:
        ep[r["pid"]].append(r)
    print(f"\n  ── 口径 B：按仓位周期（同 position_id 归一个周期）──")
    print(f"     周期数 {len(ep)}")
    ep_rows = []
    for pid, rs in ep.items():
        s = sum(x["a"] for x in rs)
        has_flat = any(x["ph"] == "flatten" for x in rs)
        ep_rows.append({"pid": pid, "sum": s, "n": len(rs),
                        "flat": has_flat, "sym": rs[0]["sym"]})
    flat_ep = [e for e in ep_rows if e["flat"]]
    pass_ep = [e for e in ep_rows if not e["flat"]]
    print(f"     含强平的周期 {len(flat_ep):>4}  "
          f"sum={sum(e['sum'] for e in flat_ep):>+9.4f}"
          f"   avg={np.mean([e['sum'] for e in flat_ep]) if flat_ep else 0:>+8.5f}")
    print(f"     无强平的周期 {len(pass_ep):>4}  "
          f"sum={sum(e['sum'] for e in pass_ep):>+9.4f}"
          f"   avg={np.mean([e['sum'] for e in pass_ep]) if pass_ep else 0:>+8.5f}")
    print(f"     含强平周期占比 {len(flat_ep)/max(len(ep_rows),1)*100:.1f}%")

    print(f"\n  ── 每个含强平周期的明细（按亏损排序）──")
    print(f"     {'pid':<22} {'币':<8} {'笔数':>5} {'周期净额':>11}")
    print("     " + "-" * 50)
    for e in sorted(flat_ep, key=lambda x: x["sum"])[:15]:
        print(f"     {e['pid']:<22} {str(e['sym']):<8} {e['n']:>5} {e['sum']:>+11.5f}")

    print("\n" + "=" * 96)
    print("判读")
    print("=" * 96)
    m_pass = np.mean([e["sum"] for e in pass_ep]) if pass_ep else float("nan")
    m_flat = np.mean([e["sum"] for e in flat_ep]) if flat_ep else float("nan")
    print(f"  无强平周期平均 {m_pass:+.5f} USD   含强平周期平均 {m_flat:+.5f} USD")
    if np.isfinite(m_pass) and m_pass > 0 and np.isfinite(m_flat) and m_flat < 0:
        print("  ⇒ **方向与 H72 一致**：被动出库的周期是赚的，含强平的周期是亏的")
        print("     但**分母应改为「周期」**：H72 的「每笔强平 −12.73bp」是按行算的，")
        print("     若一个周期含多条 flatten 行，则每周期成本 = 该周期净额 ÷ 腿量。")
    elif np.isfinite(m_pass) and m_pass <= 0:
        print("  ⇒ **H72 的「被动出库是赚的」结论错误** ⇒ 必须撤回，亏损不只在强平腿")
    else:
        print("  ⇒ 需进一步看分布")

    # 用腿量把周期净额换成 bp
    N = 134.0
    if flat_ep:
        bp_flat = np.array([e["sum"] for e in flat_ep]) / N * 1e4
        print(f"\n  含强平周期净额（bp，按 ${N:.0f} 腿量）："
              f"平均 {bp_flat.mean():+.2f}bp  中位 {np.median(bp_flat):+.2f}bp")
    if pass_ep:
        bp_pass = np.array([e["sum"] for e in pass_ep]) / N * 1e4
        print(f"  无强平周期净额（bp）：平均 {bp_pass.mean():+.2f}bp  "
              f"中位 {np.median(bp_pass):+.2f}bp")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
