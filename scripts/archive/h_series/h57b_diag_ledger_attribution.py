"""诊断：为什么 arbiter_paper_ledgers 合计 −$101.20，而账户 realized_pnl 只有 −$15.17？

**在解决这个之前，"每笔 −3.24bp" 这个数不能用来做任何决定**（第 19 次口径错误的候选）。

本脚本查清：
  1. 这 5,947 条 paper_pnl 流水分别属于哪些 lane / 哪个 account
  2. realized_pnl 是不是只覆盖 MM 车道
  3. 用 **MM 车道自己的** lane_ledger 独立重算一遍
"""
from __future__ import annotations

import json
import os
import sys
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
    import psycopg2
    import psycopg2.extras

    cn = psycopg2.connect(_dsn("alpha_arena"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    print("=" * 100)
    print("arbitrage_paper_ledgers 的归属拆解（account_id=101）")
    print("=" * 100)

    cur.execute("SELECT column_name FROM information_schema.columns"
                " WHERE table_name='arbitrage_paper_ledgers' ORDER BY ordinal_position")
    cols = [r["column_name"] for r in cur.fetchall()]
    print(f"\n  列：{cols}")

    # 看一行原始数据
    cur.execute("SELECT * FROM arbitrage_paper_ledgers WHERE account_id=101"
                " ORDER BY id DESC LIMIT 3")
    for r in cur.fetchall():
        print("\n  样本行：")
        for k, v in r.items():
            print(f"    {k:<20} {str(v)[:160]}")

    # 按 action + 时间范围
    cur.execute(
        "SELECT action, count(*) n, COALESCE(SUM(amount_usd),0) s,"
        "       min(created_at) mn, max(created_at) mx"
        "  FROM arbitrage_paper_ledgers WHERE account_id=101"
        " GROUP BY action ORDER BY n DESC")
    print("\n  按 action（含时间范围）：")
    for r in cur.fetchall():
        print(f"    {str(r['action']):<20} {r['n']:>7,}  {float(r['s']):>+12.4f}"
              f"   {r['mn']} ~ {r['mx']}")

    # 按 metadata 里的 lane / symbol 拆
    # metadata_json 是 **text** 列（不是 jsonb）—— `->>` 会报
    # "operator does not exist: text ->> unknown" ✗，必须先 cast。
    cur.execute(
        "SELECT COALESCE(metadata_json::jsonb->>'lane_id', '(none)') lane,"
        "       count(*) n, COALESCE(SUM(amount_usd),0) s,"
        "       min(created_at) mn, max(created_at) mx"
        "  FROM arbitrage_paper_ledgers WHERE account_id=101 AND action='paper_pnl'"
        " GROUP BY 1 ORDER BY s")
    print("\n  paper_pnl 按 lane 拆（**这是关键**）：")
    for r in cur.fetchall():
        print(f"    {str(r['lane']):<24} {r['n']:>7,}  {float(r['s']):>+12.4f}"
              f"   {r['mn']} ~ {r['mx']}")

    cur.execute(
        "SELECT COALESCE(metadata_json::jsonb->>'lane_id', '(none)') lane,"
        "       COALESCE(metadata_json::jsonb->>'symbol','(none)') sym,"
        "       count(*) n, COALESCE(SUM(amount_usd),0) s"
        "  FROM arbitrage_paper_ledgers WHERE account_id=101 AND action='paper_pnl'"
        " GROUP BY 1,2 ORDER BY s LIMIT 25")
    print("\n  paper_pnl 按 (lane, symbol)（最亏的 25 个）：")
    for r in cur.fetchall():
        print(f"    {str(r['lane'])[:22]:<24} {str(r['sym'])[:12]:<14} "
              f"{r['n']:>6,}  {float(r['s']):>+11.4f}")

    # 按日拆（看是不是"某一天突然开始亏"）
    cur.execute(
        "SELECT date_trunc('day', created_at) d, count(*) n,"
        "       COALESCE(SUM(amount_usd),0) s"
        "  FROM arbitrage_paper_ledgers WHERE account_id=101 AND action='paper_pnl'"
        " GROUP BY 1 ORDER BY 1")
    print("\n  paper_pnl 按日：")
    for r in cur.fetchall():
        print(f"    {str(r['d'])[:10]:<12} {r['n']:>7,}  {float(r['s']):>+12.4f}")

    # metadata 里到底有哪些 key
    cur.execute("SELECT metadata_json FROM arbitrage_paper_ledgers"
                " WHERE account_id=101 AND metadata_json IS NOT NULL LIMIT 200")
    keys = {}
    for r in cur.fetchall():
        m = r["metadata_json"]
        if isinstance(m, dict):
            for k in m:
                keys[k] = keys.get(k, 0) + 1
        elif isinstance(m, str):
            try:
                for k in json.loads(m):
                    keys[k] = keys.get(k, 0) + 1
            except Exception:
                pass
    print(f"\n  metadata_json 出现过的 key（前 200 行）：")
    for k, v in sorted(keys.items(), key=lambda x: -x[1]):
        print(f"    {k:<28} {v}")

    cn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
