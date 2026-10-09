"""H105：整夜交易全景分析 —— 按小时/phase/币种拆解，找出亏损来源。

# 背景

03:39 时 equity = $272.46；09:09 时 **$251.98** ⇒ 5.5 小时亏 **$20.48**。
期间生效的改动：F291（持有 120s，03:20）、F292（出库 0.30，03:34）、F293（校验）

# 本脚本做什么

1. **按小时**：完整口径（pnl+fee）、行数、周期数、强平率
2. **按 phase**：入场腿 vs 强平腿（含 fee）
3. **按币种**：找出亏损集中在哪里
4. **与 H102 的分解对照**：入场腿每行 bp 是否还维持在 +0.08~0.14 量级
5. **闸门分析**：`vol_pause` 触发 2978 次（占 ticks 的很大比例）——
   这是否在系统性地挡掉成交？

判据（事先定死）：
  · 若入场腿每行 bp **转负** ⇒ 问题在入场（可能是 F292 出库改动影响了入场？）
  · 若入场腿仍正、强平腿恶化 ⇒ 问题在出库
  · 若某币单独贡献大部分亏损 ⇒ 币种问题（考虑移出宇宙）

用法：
    .venv\\Scripts\\python.exe scripts\\h105_night_analysis.py
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

    cn = psycopg2.connect(_dsn("alpha_arena"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    print("=" * 104)
    print("H105  整夜交易全景（2026-09-21 00:00 起）")
    print("=" * 104)

    # ── 1) 按小时 ──
    cur.execute(
        "SELECT date_trunc('hour', created_at) h, action, count(*) n,"
        "       COALESCE(SUM(amount_usd),0) s"
        "  FROM arbitrage_paper_ledgers"
        " WHERE account_id=101 AND metadata_json::jsonb->>'lane_id'='mm_asterdex'"
        "   AND created_at >= %s GROUP BY 1,2 ORDER BY 1,2", (DAY,))
    byh = defaultdict(lambda: defaultdict(float))
    cnth = defaultdict(lambda: defaultdict(int))
    for r in cur.fetchall():
        k = str(r["h"])[11:16]
        byh[k][r["action"]] += float(r["s"])
        cnth[k][r["action"]] = int(r["n"])
    print(f"\n  {'小时':>6} {'pnl':>10} {'fee':>10} {'合计':>10} {'行数':>7} {'累计':>10}")
    print("  " + "-" * 60)
    run = 0.0
    for k in sorted(byh):
        p = byh[k].get("paper_pnl", 0.0); f = byh[k].get("paper_fee", 0.0)
        run += p + f
        n = cnth[k].get("paper_pnl", 0) + cnth[k].get("paper_fee", 0)
        print(f"  {k:>6} {p:>+10.4f} {f:>+10.4f} {p+f:>+10.4f} {n:>7} {run:>+10.4f}")
    print(f"\n  ⇒ 今日累计 **{run:+.4f} USD**")

    # ── 2) 按 phase（今日 + 最近 5.5 小时）──
    print("\n" + "=" * 104)
    print("按 phase 拆（全部 vs 03:20 F291 之后）")
    print("=" * 104)
    for lab, since in (("今日全部", DAY), ("F291 之后（03:20+）", "2026-09-21 03:20:00")):
        cur.execute(
            "SELECT action, COALESCE(metadata_json::jsonb->>'phase','(none)') ph,"
            "       count(*) n, COALESCE(SUM(amount_usd),0) s"
            "  FROM arbitrage_paper_ledgers"
            " WHERE account_id=101 AND action<>'create_account'"
            "   AND metadata_json::jsonb->>'lane_id'='mm_asterdex'"
            "   AND created_at >= %s GROUP BY 1,2 ORDER BY 2,1", (since,))
        print(f"\n  ── {lab} ──")
        print(f"  {'action':<12} {'phase':<10} {'行数':>7} {'合计USD':>11} {'每行bp':>10}")
        print("  " + "-" * 56)
        tot = 0.0
        for r in cur.fetchall():
            s = float(r["s"]); n = int(r["n"]); tot += s
            print(f"  {r['action']:<12} {r['ph']:<10} {n:>7} {s:>+11.4f} "
                  f"{s/max(n,1)/NOTIONAL*1e4:>+10.3f}")
        print(f"  {'合计':<12} {'':<10} {'':>7} {tot:>+11.4f}")

    # ── 3) 按币种 ──
    print("\n" + "=" * 104)
    print("按币种（今日全部）")
    print("=" * 104)
    cur.execute(
        "SELECT metadata_json::jsonb->>'symbol' sym,"
        "       COALESCE(metadata_json::jsonb->>'phase','?') ph,"
        "       count(*) n, COALESCE(SUM(amount_usd),0) s"
        "  FROM arbitrage_paper_ledgers"
        " WHERE account_id=101 AND action<>'create_account'"
        "   AND metadata_json::jsonb->>'lane_id'='mm_asterdex'"
        "   AND created_at >= %s GROUP BY 1,2 ORDER BY 1,2", (DAY,))
    bys = defaultdict(lambda: defaultdict(float))
    for r in cur.fetchall():
        bys[r["sym"]][r["ph"]] += float(r["s"])
    print(f"\n  {'币':<10} {'fill':>11} {'flatten':>11} {'合计':>11}")
    print("  " + "-" * 48)
    tot_all = 0.0
    for s in sorted(bys, key=lambda x: sum(bys[x].values())):
        f = bys[s].get("fill", 0.0); fl = bys[s].get("flatten", 0.0)
        tot_all += f + fl
        print(f"  {s:<10} {f:>+11.4f} {fl:>+11.4f} {f+fl:>+11.4f}")
    print(f"  {'合计':<10} {'':>11} {'':>11} {tot_all:>+11.4f}")

    # ── 4) 入场腿每行 bp 的时间演化（关键诊断）──
    print("\n" + "=" * 104)
    print("入场腿每行 bp 按小时（判断入场质量是否退化）")
    print("=" * 104)
    cur.execute(
        "SELECT date_trunc('hour', created_at) h, count(*) n,"
        "       COALESCE(SUM(amount_usd),0) s"
        "  FROM arbitrage_paper_ledgers"
        " WHERE account_id=101 AND action='paper_pnl'"
        "   AND metadata_json::jsonb->>'lane_id'='mm_asterdex'"
        "   AND metadata_json::jsonb->>'phase'='fill'"
        "   AND created_at >= %s GROUP BY 1 ORDER BY 1", (DAY,))
    print(f"\n  {'小时':>6} {'入场腿行数':>10} {'合计USD':>11} {'每行bp':>10}")
    print("  " + "-" * 42)
    for r in cur.fetchall():
        n = int(r["n"]); s = float(r["s"])
        print(f"  {str(r['h'])[11:16]:>6} {n:>10} {s:>+11.4f} "
              f"{s/max(n,1)/NOTIONAL*1e4:>+10.3f}")

    cn.close()
    print("\n" + "=" * 104)
    print("判读要点")
    print("=" * 104)
    print("  · 入场腿每行 bp 若**转负** ⇒ 问题在入场质量")
    print("  · 若入场腿仍正而强平腿占比大 ⇒ 问题在出库（F292 方向是否正确）")
    print("  · 按币种看是否有单一币主导亏损 ⇒ 考虑移出宇宙")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
