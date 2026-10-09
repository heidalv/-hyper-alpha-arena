# -*- coding: utf-8 -*-
"""找账户重置的审计痕迹（ops_changes 全量 + 账户行 updated_at）。只读。"""
import sys
import json
import pathlib
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()
cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id='mm_asterdex'")
m = cur.fetchone()[0]
print("== ops_changes 全量（找 reset）==")
for e in (m.get("ops_changes") or []):
    s = json.dumps(e, ensure_ascii=False)
    if "reset" in s.lower() or "重置" in s:
        print("  ", s[:200])

print("\n== 账户行 ==")
cur.execute("""SELECT total_equity, available_balance, realized_pnl, updated_at, metadata_json
               FROM arbitrage_paper_accounts WHERE id=101""")
te, ab, rp, upd, mj = cur.fetchone()
print(f"  total_equity={te} available={float(ab):.4f} realized_pnl={float(rp):.4f} updated={upd}")
print("  metadata_json:", str(mj)[:200])

print("\n== 账户流水按小时（近 6h，看余额跳变/重置点）==")
cur.execute("""
    SELECT date_trunc('hour', created_at) AS h, COUNT(*),
           ROUND(MIN(balance_after)::numeric,2), ROUND(MAX(balance_after)::numeric,2)
    FROM arbitrage_paper_ledgers WHERE account_id=101 AND created_at >= now() - interval '6 hours'
    GROUP BY 1 ORDER BY 1
""")
for r in cur.fetchall():
    print(f"  {r[0]:%H:%M}  n={r[1]:>5}  balance {r[2]} ~ {r[3]}")
