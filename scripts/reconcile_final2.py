# -*- coding: utf-8 -*-
"""精确定位抽资行，从其之后对账。只读。"""
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

# 找余额在 300~310 之间的最早行（抽资落点）
cur.execute("""
    SELECT created_at, action, amount_usd, balance_after
    FROM arbitrage_paper_ledgers
    WHERE account_id=101 AND balance_after BETWEEN 300 AND 310
      AND created_at >= '2026-09-23'
    ORDER BY created_at ASC LIMIT 3
""")
rows = cur.fetchall()
print("抽资落点候选：")
for r in rows:
    print(f"  {r[0]:%m-%d %H:%M:%S} {r[1]:<18} amt={float(r[2] or 0):+.2f} bal={float(r[3]):.2f}")

if rows:
    t0 = rows[0][0]
    b0 = float(rows[0][3])
    cur.execute("""
        SELECT SUM(amount_usd) FILTER (WHERE action='paper_pnl'),
               SUM(amount_usd) FILTER (WHERE action='paper_fee'), COUNT(*)
        FROM arbitrage_paper_ledgers
        WHERE account_id=101 AND created_at >= %s
    """, (t0,))
    pnl, fee, n = [float(x or 0) for x in cur.fetchone()]
    cur.execute("SELECT available_balance FROM arbitrage_paper_accounts WHERE id=101")
    b1 = float(cur.fetchone()[0])
    print(f"\n自 {t0:%m-%d %H:%M}（基线 {b0:.2f}）以来：")
    print(f"  流水 pnl={pnl:+.4f} fee={fee:+.4f} 合计={pnl+fee:+.4f}（{n} 条）")
    print(f"  余额 {b0:.4f} → {b1:.4f} = {b1-b0:+.4f}")
    print(f"  残差 = {(b1-b0)-(pnl+fee):+.4f}")
