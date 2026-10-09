"""按策略 + 阶段拆分模拟账户流水，定位亏损究竟来自哪一类腿。

为什么需要：早先我用一条 `metadata_json LIKE '%"phase": "flatten"%'` 的查询
得出「6 笔平仓腿吃掉 66% 亏损」，但那条查询**没有按 strategy_type 过滤**，
可能混入了同账户下 S3/S8/SDN 等其它策略的行。口径不干净就不能下结论。

用法：
    .venv\\Scripts\\python.exe scripts\\diag_hft_ledger_breakdown.py [--hours 24]
"""
from __future__ import annotations

import argparse
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

DSN = os.getenv("DATABASE_URL") or ""
for _p in ("+psycopg2", "+psycopg", "+asyncpg"):
    DSN = DSN.replace(_p, "")
if not DSN:
    raise SystemExit("DATABASE_URL 缺失")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--account-id", type=int, default=101)
    args = ap.parse_args()

    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(DSN)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    iv = f"{args.hours} hours"

    print(f"账户 {args.account_id}  近 {args.hours}h\n")

    print("[1] 按 strategy_type × action")
    cur.execute(
        """
        SELECT COALESCE(strategy_type,'(null)') st, action,
               COUNT(*) n, SUM(amount_usd) amt
          FROM arbitrage_paper_ledgers
         WHERE account_id = %s AND created_at > now() - %s::interval
         GROUP BY 1,2 ORDER BY 1,2
        """,
        (args.account_id, iv),
    )
    print("    %-9s %-14s %6s %16s" % ("strategy", "action", "n", "amount_usd"))
    for r in cur.fetchall():
        print("    %-9s %-14s %6d %+16.6f" % (r["st"], r["action"], r["n"], float(r["amt"] or 0)))

    print("\n[2] 按 metadata.phase（全部策略）")
    cur.execute(
        """
        SELECT COALESCE(metadata_json::json->>'phase','(null)') ph,
               COUNT(*) n, SUM(amount_usd) amt
          FROM arbitrage_paper_ledgers
         WHERE account_id = %s AND created_at > now() - %s::interval
         GROUP BY 1 ORDER BY 1
        """,
        (args.account_id, iv),
    )
    for r in cur.fetchall():
        print("    %-10s %6d %+16.6f" % (r["ph"], r["n"], float(r["amt"] or 0)))

    print("\n[3] 按 strategy_type × phase（**这才是能下结论的口径**）")
    cur.execute(
        """
        SELECT COALESCE(strategy_type,'(null)') st,
               COALESCE(metadata_json::json->>'phase','(null)') ph,
               COUNT(*) n, SUM(amount_usd) amt,
               SUM(CASE WHEN amount_usd > 0 THEN amount_usd ELSE 0 END) win,
               SUM(CASE WHEN amount_usd < 0 THEN amount_usd ELSE 0 END) los
          FROM arbitrage_paper_ledgers
         WHERE account_id = %s AND created_at > now() - %s::interval
           AND action = 'paper_pnl'
         GROUP BY 1,2 ORDER BY 1,2
        """,
        (args.account_id, iv),
    )
    print("    %-9s %-9s %6s %14s %12s %12s %8s" % ("strategy", "phase", "n", "net$", "盈利腿$", "亏损腿$", "胜率"))
    for r in cur.fetchall():
        n = int(r["n"] or 0)
        win = float(r["win"] or 0)
        los = float(r["los"] or 0)
        wr = (100.0 * win / (win + abs(los))) if (win + abs(los)) > 0 else 0.0
        print("    %-9s %-9s %6d %+14.6f %+12.6f %+12.6f %7.1f%%"
              % (r["st"], r["ph"], n, float(r["amt"] or 0), win, los, wr))

    print("\n[4] 全部平仓腿明细（strategy_type 标注）")
    cur.execute(
        """
        SELECT created_at, strategy_type st, amount_usd, metadata_json
          FROM arbitrage_paper_ledgers
         WHERE account_id = %s AND created_at > now() - %s::interval
           AND action = 'paper_pnl'
           AND metadata_json::json->>'phase' = 'flatten'
         ORDER BY created_at
        """,
        (args.account_id, iv),
    )
    import json
    rows = cur.fetchall()
    for r in rows:
        try:
            md = json.loads(r["metadata_json"] or "{}")
        except Exception:
            md = {}
        notional = abs(float(md.get("qty") or 0) * float(md.get("px") or 0))
        amt = float(r["amount_usd"] or 0)
        bp = (amt / notional * 1e4) if notional > 0 else 0.0
        print("    %s  %-8s %-6s 名义%9.4f  %+12.6f  %+9.3fbp"
              % (str(r["created_at"])[11:19], r["st"], md.get("symbol") or "-",
                 notional, amt, bp))
    tot = sum(float(r["amount_usd"] or 0) for r in rows)
    print("    ---- 平仓腿合计 %+.6f USD（占全部亏损比例见 [3]）----" % tot)

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
