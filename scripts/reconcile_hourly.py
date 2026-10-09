# -*- coding: utf-8 -*-
"""逐小时发散扫描：Σ流水(小时) vs balance_after 实际变化，找历史记账断裂点。只读。"""
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

# 每小时：流水合计 + 该小时最后一笔的 balance_after
cur.execute("""
    WITH h AS (
      SELECT date_trunc('hour', created_at) AS hr,
             SUM(amount_usd) FILTER (WHERE action IN ('paper_pnl','paper_fee')) AS flow,
             COUNT(*) AS n
      FROM arbitrage_paper_ledgers
      WHERE account_id=101 AND created_at >= now() - interval '7 days'
      GROUP BY 1
    ), bal AS (
      SELECT date_trunc('hour', created_at) AS hr,
             (ARRAY_AGG(balance_after ORDER BY created_at DESC))[1] AS bal_end
      FROM arbitrage_paper_ledgers
      WHERE account_id=101 AND created_at >= now() - interval '7 days'
      GROUP BY 1
    )
    SELECT h.hr, h.n, ROUND(h.flow::numeric,4), ROUND(b.bal_end::numeric,4)
    FROM h JOIN bal b USING (hr) ORDER BY h.hr
""")
rows = cur.fetchall()
print("逐小时：流水Σ vs 余额（只在两者对不上时列出）")
prev_bal = None
bad = 0
for hr, n, flow, bal_end in rows:
    flow = float(flow or 0)
    bal_end = float(bal_end or 0)
    diff = None if prev_bal is None else round(bal_end - prev_bal - flow, 4)
    if diff is not None and abs(diff) > 0.02:
        print(f"  {hr:%m-%d %H:%M} n={n:>4} flow={flow:>+9.4f} bal {prev_bal:>9.4f}→{bal_end:>9.4f} "
              f"**差 {diff:+.4f}**")
        bad += 1
    prev_bal = bal_end
print(f"\n共 {len(rows)} 个小时，其中对不上的 {bad} 个")

print("\n== 全期总账 ==")
cur.execute("""
    SELECT SUM(amount_usd) FILTER (WHERE action='paper_pnl'),
           SUM(amount_usd) FILTER (WHERE action='paper_fee'),
           SUM(amount_usd) FILTER (WHERE action='create_account')
    FROM arbitrage_paper_ledgers WHERE account_id=101
""")
pnl, fee, cr = [float(x or 0) for x in cur.fetchone()]
print(f"  create={cr:+.2f} pnl={pnl:+.2f} fee={fee:+.2f} ⇒ 理论余额={cr+pnl+fee:+.2f}")
cur.execute("SELECT available_balance, realized_pnl FROM arbitrage_paper_accounts WHERE id=101")
ab, rp = [float(x) for x in cur.fetchone()]
print(f"  实际余额={ab:+.2f}（理论−实际 = {cr+pnl+fee-ab:+.2f}）")
print(f"  realized_pnl 字段={rp:+.2f}（与 (余额−起始) = {ab-cr:+.2f} 差 {rp-(ab-cr):+.2f}）")
