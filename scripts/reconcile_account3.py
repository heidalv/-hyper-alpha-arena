# -*- coding: utf-8 -*-
"""账户 101 流水 + 与账本净额对账（定位 $18 差额来源）。只读。"""
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

print("== arbitrage_paper_ledgers（account 101）按 action 汇总 ==")
cur.execute("""
    SELECT action, COUNT(*), ROUND(SUM(amount_usd)::numeric,4),
           MIN(created_at), MAX(created_at)
    FROM arbitrage_paper_ledgers WHERE account_id=101
    GROUP BY 1 ORDER BY 3
""")
for r in cur.fetchall():
    print(f"  {r[0]:<28} n={r[1]:>5} Σ=${r[2]:>+10} {r[3]:%m-%d %H:%M} → {r[4]:%m-%d %H:%M}")

print("\n== 最近 15 条流水 ==")
cur.execute("""
    SELECT created_at, action, amount_usd, balance_after, note
    FROM arbitrage_paper_ledgers WHERE account_id=101
    ORDER BY created_at DESC LIMIT 15
""")
for r in cur.fetchall():
    print(f"  {r[0]:%m-%d %H:%M:%S} {r[1]:<24} amt=${float(r[2] or 0):>+9.4f} "
          f"bal=${float(r[3] or 0):>9.3f} {(r[4] or '')[:44]}")

print("\n== 与账户表对账 ==")
cur.execute("""SELECT total_equity, available_balance, realized_pnl, updated_at
               FROM arbitrage_paper_accounts WHERE id=101""")
te, ab, rp, upd = cur.fetchone()
print(f"  total_equity={float(te):.2f}（起始，从未更新） available={float(ab):.4f} "
      f"realized={float(rp):.4f} updated={upd:%m-%d %H:%M}")
print(f"  300 − available = {300 - float(ab):+.3f}（权益实际跌幅）")
print(f"  账面 realized_pnl = {float(rp):+.3f} ⇒ 缺口 = {300 - float(ab) + float(rp):+.3f}")

print("\n== 账本分时代净额（与账户跌幅对照）==")
cur.execute("""
    SELECT date_trunc('day', ts) AS d, COUNT(*),
           ROUND(SUM(net_bp*notional/1e4)::numeric,2)
    FROM lane_ledger WHERE event='fill' AND ts >= now() - interval '7 days'
    GROUP BY 1 ORDER BY 1
""")
tot = 0.0
for d, n, s in cur.fetchall():
    tot += float(s or 0)
    print(f"  {d:%m-%d}  n={n:>5}  Σ=${s:>+9}  （7日累计 {tot:+.2f}）")
print(f"\n  近 7 日账本净额合计 = {tot:+.2f} USD")
print(f"  账户权益实际跌幅     = {300 - float(ab):+.2f} USD")
