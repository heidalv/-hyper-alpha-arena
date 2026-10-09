# -*- coding: utf-8 -*-
"""窗口级对账：近 2h 的流水金额合计 vs 余额实际变化 vs 账本口径。只读。"""
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

print("== 近 2h 账户流水 vs 余额变化 ==")
cur.execute("""
    SELECT MIN(created_at), MAX(created_at),
           ROUND(SUM(amount_usd) FILTER (WHERE action='paper_pnl')::numeric,4),
           ROUND(SUM(amount_usd) FILTER (WHERE action='paper_fee')::numeric,4),
           COUNT(*) FILTER (WHERE action='paper_pnl'),
           COUNT(*) FILTER (WHERE action='paper_fee')
    FROM arbitrage_paper_ledgers
    WHERE account_id=101 AND created_at >= now() - interval '2 hours'
""")
mn, mx, pnl, fee, npnl, nfee = cur.fetchone()
print(f"  窗口 {mn:%H:%M}→{mx:%H:%M}")
print(f"  Σpaper_pnl={float(pnl):+.4f}（n={npnl}） Σpaper_fee={float(fee):+.4f}（n={nfee}）")
print(f"  流水合计 = {float(pnl)+float(fee):+.4f}")

cur.execute("""
    SELECT balance_after FROM arbitrage_paper_ledgers
    WHERE account_id=101 AND created_at <= now() - interval '2 hours'
    ORDER BY created_at DESC LIMIT 1
""")
b0 = cur.fetchone()
cur.execute("""SELECT available_balance FROM arbitrage_paper_accounts WHERE id=101""")
b1 = cur.fetchone()[0]
print(f"  余额：窗口前 {float(b0[0]) if b0 else 'N/A'} → 现在 {float(b1):.4f} "
      f"（变化 {float(b1) - float(b0[0]):+.4f}）" if b0 else "")

print("\n== 同一时刻成对出现的 pnl（疑似双边重复记账）==")
cur.execute("""
    SELECT created_at, symbol, side, ROUND(amount_usd::numeric,4), note
    FROM arbitrage_paper_ledgers
    WHERE account_id=101 AND action='paper_pnl' AND created_at >= now() - interval '20 minutes'
    ORDER BY created_at DESC LIMIT 24
""")
for r in cur.fetchall():
    print(f"  {r[0]:%H:%M:%S} {str(r[1]):<8} {str(r[2]):<5} ${float(r[3]):>+8.4f} {(r[4] or '')[:26]}")

print("\n== 近 2h 账本口径（lane_ledger）==")
cur.execute("""
    SELECT COUNT(*), ROUND(SUM(net_bp*notional/1e4)::numeric,4),
           ROUND(SUM(fee_bp*notional/1e4)::numeric,4),
           ROUND(SUM(price_bp*notional/1e4)::numeric,4)
    FROM lane_ledger WHERE event='fill' AND ts >= now() - interval '2 hours'
""")
r = cur.fetchone()
print(f"  腿数={r[0]} Σ净额=${float(r[1]):+.4f} Σ费=${float(r[2]):+.4f} Σ价格=${float(r[3]):+.4f}")

print("\n== paper_positions 未平仓（若有）==")
cur.execute("""
    SELECT COUNT(*), ROUND(SUM(unrealized_pnl)::numeric,4)
    FROM paper_positions WHERE account_id=101 AND status='open'
""")
print("  ", cur.fetchone())
