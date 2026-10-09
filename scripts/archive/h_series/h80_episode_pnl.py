"""H80：往返级真实成本（F285 之后首次可算）。

# 为什么这是新东西

F285 之前，账本 `related_position_id` 只有币名（11 个不同值）⇒ **往返配对不可能**。
F285 修好后，`position_id` 形如 `mm:SOL:3` ⇒ 可以按周期精确求和。

**这是本项目第一次能用"一次往返"（而不是"一条腿"或"一条 ledger 行"）来算成本。**

# 口径（本次明确写死，避免第 33 条那类错误）

  · **一次往返（episode）** = 同一个 `position_id` 下的全部 ledger 行
  · `phase=fill` = 做市成交腿；`phase=flatten` = 引擎主动市价平仓腿
  · 周期净额 = 该周期全部 `amount_usd` 之和
  · **不排除任何周期**（含只有 flatten 没有 fill 的、以及只有 fill 无 flatten 的）
  · 分母同时报"按周期"与"按行"两个口径，并说明差别

# 判据（事先定死）

  · 若"含强平周期"与"无强平周期"的净额差 ≥ $0.10/周期 ⇒ 强平是主因（与 H72 方向一致）
  · 若样本 < 30 个周期 ⇒ **只报方向，不报显著性**（避免从 11 个点下结论）

用法：
    .venv\\Scripts\\python.exe scripts\\h80_episode_pnl.py
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
        "SELECT related_position_id r, metadata_json::jsonb->>'phase' ph,"
        "       amount_usd::float a, created_at,"
        "       metadata_json::jsonb->>'symbol' sym,"
        "       (metadata_json::jsonb->>'qty')::float qty,"
        "       (metadata_json::jsonb->>'px')::float px"
        "  FROM arbitrage_paper_ledgers"
        " WHERE account_id=101 AND action='paper_pnl'"
        "   AND metadata_json::jsonb->>'lane_id'='mm_asterdex'"
        "   AND related_position_id ~ ':[0-9]+$'"
        " ORDER BY created_at")
    rows = cur.fetchall()
    cn.close()
    if not rows:
        print("无带周期号的记录 —— F285 之后还没积累数据")
        return 1

    print("=" * 100)
    print("H80  往返级真实成本（F285 之后首次可算）")
    print("=" * 100)
    span = (str(rows[0]["created_at"])[:19], str(rows[-1]["created_at"])[:19])
    print(f"  记录 {len(rows):,} 条   时间跨度 {span[0]} ~ {span[1]}")

    ep = defaultdict(lambda: {"fill": 0, "flatten": 0, "sum": 0.0, "n": 0,
                              "sym": None, "notional": 0.0, "t0": None, "t1": None})
    for r in rows:
        e = ep[r["r"]]
        e["n"] += 1
        e["sum"] += r["a"]
        e[r["ph"]] = e.get(r["ph"], 0) + 1
        e["sym"] = e["sym"] or r["sym"]
        if r["qty"] and r["px"]:
            e["notional"] += abs(r["qty"] * r["px"])
        e["t0"] = e["t0"] or r["created_at"]
        e["t1"] = r["created_at"]

    flat = [v for v in ep.values() if v.get("flatten")]
    noflat = [v for v in ep.values() if not v.get("flatten")]
    print(f"\n  周期总数 {len(ep)}   含强平 {len(flat)}   无强平 {len(noflat)}")
    if len(ep) < 30:
        print(f"  ⚠️ 周期数 {len(ep)} < 30 ⇒ **只报方向，不报显著性**")

    def blk(name, vs):
        if not vs:
            print(f"\n  ── {name}: 无样本")
            return None
        s = np.array([v["sum"] for v in vs])
        # 每周期 bp：用该周期名义的均值
        nt = np.array([v["notional"] / max(v["n"], 1) for v in vs])
        bp = s / np.maximum(nt, 1e-9) * 1e4
        dur = [(v["t1"] - v["t0"]).total_seconds() for v in vs]
        print(f"\n  ── {name}  n={len(vs)}")
        print(f"     周期净额 USD:  均值 {s.mean():>+9.5f}  中位 {np.median(s):>+9.5f}"
              f"  合计 {s.sum():>+9.5f}")
        print(f"     周期净额 bp:   均值 {bp.mean():>+9.2f}  中位 {np.median(bp):>+9.2f}")
        print(f"     周期时长 s:    中位 {np.median(dur):>8.0f}  最大 {max(dur):>8.0f}")
        print(f"     正值周期 {int((s>0).sum())}/{len(s)}")
        return s

    sf = blk("含强平的周期", flat)
    snf = blk("无强平的周期", noflat)

    print("\n" + "=" * 100)
    print("判据")
    print("=" * 100)
    if sf is not None and snf is not None:
        diff = sf.mean() - snf.mean()
        print(f"  含强平 − 无强平 = {diff:>+.5f} USD/周期")
        if abs(diff) >= 0.10:
            print("  ⇒ 差值 ≥ $0.10/周期 ⇒ **强平是主因**（与 H72/H76 方向一致）✓")
        else:
            print("  ⇒ 差值 < $0.10/周期 ⇒ 样本太小或强平不是主因")
    tot = sum(v["sum"] for v in ep.values())
    print(f"\n  全部周期合计 {tot:>+.5f} USD（{len(ep)} 个周期）")

    # 逐周期明细
    print("\n  ── 逐周期明细（按净额升序）──")
    print(f"     {'position_id':<16} {'币':<8} {'行':>3} {'fill':>4} {'flat':>4} "
          f"{'净额USD':>11} {'名义USD':>10}")
    print("     " + "-" * 66)
    for k, v in sorted(ep.items(), key=lambda kv: kv[1]["sum"]):
        print(f"     {k:<16} {str(v['sym']):<8} {v['n']:>3} {v.get('fill',0):>4} "
              f"{v.get('flatten',0):>4} {v['sum']:>+11.5f} {v['notional']:>10.2f}")

    print("\n" + "=" * 100)
    print("诚实说明")
    print("=" * 100)
    print(f"  · 这是 **{len(ep)} 个周期 / {len(rows)} 条记录 / 约 7 分钟** 的样本")
    print(f"  · 不足以判定任何显著性；只用于确认**往返归因链路可用**，")
    print(f"    以及给出第一版往返成本的量级")
    print(f"  · 累积到 ≥100 个周期后再用本脚本做判据级结论")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
