# -*- coding: utf-8 -*-
"""核查账户流水的 reset_account 事件（作为面板边界的权威来源）。只读。"""
import sys
import pathlib
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()
cur.execute("""
    SELECT created_at, action, amount_usd, note
    FROM arbitrage_paper_ledgers
    WHERE account_id=101 AND action IN ('reset_account','create_account','apply_preset')
    ORDER BY created_at DESC LIMIT 8
""")
print("账户重置/创建事件：")
for r in cur.fetchall():
    print(f"  {r[0]:%m-%d %H:%M:%S} {r[1]:<16} ${float(r[2] or 0):>8.2f}  {str(r[3])[:40]}")

cur.execute("""
    SELECT MIN(created_at) FROM arbitrage_paper_ledgers
    WHERE account_id=101 AND action='paper_pnl'
      AND created_at >= COALESCE((SELECT MAX(created_at) FROM arbitrage_paper_ledgers
                                  WHERE account_id=101 AND action='reset_account'),
                                 '1970-01-01')
""")
r = cur.fetchone()
print("\n最近一次重置后首笔 pnl：", r[0])
cur.execute("""
    SELECT COUNT(*), ROUND(SUM(amount_usd)::numeric,4)
    FROM arbitrage_paper_ledgers
    WHERE account_id=101 AND action='paper_fee'
      AND created_at >= COALESCE((SELECT MAX(created_at) FROM arbitrage_paper_ledgers
                                  WHERE account_id=101 AND action='reset_account'),
                                 '1970-01-01')
""")
r = cur.fetchone()
print(f"该边界后手续费流水：{r[0]} 条 Σ=${r[1]}")
