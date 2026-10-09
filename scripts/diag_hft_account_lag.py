"""诊断：模拟账户已实现盈亏为何**不随成交实时变动**。

两条线索（2026-09-20 18:58）：
  lane_ledger  24h 68 行、`price_bp+spread_bp+fee_bp` 折算净额 ≈ -$0.22
  模拟账户      paper.realized_usd = -$0.1160

本脚本直接读账户总账 `arbitrage_paper_ledger`（strategy_type=MM），逐笔求和，
与 `lane_ledger` 对照，判断差在哪一类行：
  · 开仓腿（phase='fill'）是否也计入了 pnl_delta？
  · 还是只有平仓腿（phase='flatten'）计入？
若只有平仓腿计入 ⇒ **已实现盈亏只在持仓结束时跳一次**，
持仓持有 1–5 分钟 ⇒ 页面上的"已实现"长时间纹丝不动，
这正是用户反馈"账户数据没有任何变化"的根因。

用法：
    .venv\\Scripts\\python.exe scripts\\diag_hft_account_lag.py [--hours 24]
"""
from __future__ import annotations

import argparse
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

    # ① 账户表本体
    cur.execute(
        "SELECT * FROM arbitrage_paper_accounts WHERE id = %s",
        (args.account_id,),
    )
    acc = cur.fetchone()
    print("[1] arbitrage_paper_accounts")
    if acc:
        for k in sorted(acc.keys()):
            if k in ("created_at", "updated_at") or "equity" in k or "balance" in k \
                    or "pnl" in k or k in ("id", "name", "status", "account_status", "mode"):
                print("    %-24s %s" % (k, acc[k]))
    else:
        print("    (无此账户)")

    # ② 账户总账逐笔
    # 真实 schema（models.py:4066 `ArbitragePaperLedgerDB`）：表名是
    # `arbitrage_paper_ledgers`（复数），金额列是 `amount_usd`（不是 pnl_delta），
    # 时间列是 `created_at`，阶段信息在 `action` + `metadata_json.phase`。
    cur.execute(
        """
        SELECT id, created_at, action, exchange, strategy_type, amount_usd,
               balance_after, related_position_id, note, metadata_json
          FROM arbitrage_paper_ledgers
         WHERE account_id = %s
           AND created_at > now() - make_interval(secs => %s)
         ORDER BY created_at
        """,
        (args.account_id, args.hours * 3600.0),
    )
    rows = cur.fetchall()
    print(f"\n[2] arbitrage_paper_ledgers  account_id={args.account_id}  hours={args.hours}  rows={len(rows)}")

    if rows:
        by_action: dict[str, dict] = defaultdict(lambda: {"n": 0, "amt": 0.0})
        by_phase: dict[str, dict] = defaultdict(lambda: {"n": 0, "amt": 0.0})
        import json as _json
        for r in rows:
            k = str(r["action"] or "?")
            by_action[k]["n"] += 1
            by_action[k]["amt"] += float(r["amount_usd"] or 0.0)
            try:
                ph = str(_json.loads(r["metadata_json"] or "{}").get("phase") or "?")
            except Exception:
                ph = "?"
            by_phase[ph]["n"] += 1
            by_phase[ph]["amt"] += float(r["amount_usd"] or 0.0)

        print("    action           n        amount_usd_sum")
        tot = 0.0
        for k, v in sorted(by_action.items()):
            tot += v["amt"]
            print("    %-14s %4d   %+16.6f" % (k, v["n"], v["amt"]))
        print("    ---- 合计 amount_usd = %+.6f ----" % tot)

        print("\n    按 metadata.phase（开仓腿 vs 平仓腿）")
        print("    phase            n        amount_usd_sum")
        for k, v in sorted(by_phase.items()):
            print("    %-14s %4d   %+16.6f" % (k, v["n"], v["amt"]))

        # 最近 12 笔明细
        print("\n[3] 最近 12 笔流水")
        for r in rows[-12:]:
            try:
                ph = _json.loads(r["metadata_json"] or "{}").get("phase") or "?"
            except Exception:
                ph = "?"
            print("    %s  %-9s %-10s %+12.6f  bal=%s  %s"
                  % (str(r["created_at"])[11:19], r["action"], ph,
                     float(r["amount_usd"] or 0.0),
                     ("%.4f" % float(r["balance_after"])) if r["balance_after"] is not None else "-",
                     (r["note"] or "")[:38]))

    # ③ 与 lane_ledger 对照
    cur.execute(
        """
        SELECT COUNT(*) AS n,
               COALESCE(SUM(net_bp), 0) AS net_bp_sum,
               COALESCE(SUM(notional), 0) AS notional_sum,
               MIN(ts) AS mn, MAX(ts) AS mx
          FROM lane_ledger
         WHERE lane_id = 'mm_asterdex'
           AND ts > now() - make_interval(secs => %s)
        """,
        (args.hours * 3600.0,),
    )
    lr = cur.fetchone()
    print("\n[4] lane_ledger 对照")
    print("    rows=%s  net_bp_sum=%.4f  notional_sum=%.2f" % (lr["n"], lr["net_bp_sum"], lr["notional_sum"]))
    print("    ts range: %s .. %s" % (lr["mn"], lr["mx"]))

    if rows:
        cur.execute(
            """
            SELECT MIN(created_at) AS mn, MAX(created_at) AS mx, COUNT(*) AS n
              FROM arbitrage_paper_ledgers
             WHERE account_id = %s AND created_at > now() - make_interval(secs => %s)
            """,
            (args.account_id, args.hours * 3600.0),
        )
        ar = cur.fetchone()
        print("    account ledger ts range: %s .. %s (n=%s)" % (ar["mn"], ar["mx"], ar["n"]))

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
