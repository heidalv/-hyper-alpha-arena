# -*- coding: utf-8 -*-
"""终检：自最后一次资金注入（09-24 11:00）以来的流水 vs 余额变化。只读。"""
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
    SELECT created_at, balance_after FROM arbitrage_paper_ledgers
    WHERE account_id=101 AND created_at >= '2026-09-24 11:30:00+08'
    ORDER BY created_at ASC LIMIT 1
""")
row = cur.fetchone()
t0, b0 = row[0], float(row[1])
cur.execute("""
    SELECT SUM(amount_usd) FILTER (WHERE action='paper_pnl'),
           SUM(amount_usd) FILTER (WHERE action='paper_fee'),
           COUNT(*)
    FROM arbitrage_paper_ledgers
    WHERE account_id=101 AND created_at >= %s
""", (t0,))
pnl, fee, n = [float(x or 0) for x in cur.fetchone()]
cur.execute("SELECT available_balance FROM arbitrage_paper_accounts WHERE id=101")
b1 = float(cur.fetchone()[0])
print(f"自 {t0:%m-%d %H:%M}（最后一次资金变动后首个快照，基线 {b0:.4f}）以来：")
print(f"  流水：pnl={pnl:+.4f}  fee={fee:+.4f}  合计={pnl+fee:+.4f}（{n} 条）")
print(f"  余额：{b0:.4f} → {b1:.4f}  变化={b1-b0:+.4f}")
print(f"  残差 = {(b1-b0)-(pnl+fee):+.4f}  （≈0 即账目自洽）")

print("\n== 结论：真实盈亏口径 ==")
print(f"  从最近一次资金变动算起：{b1-b0:+.2f} USD")
print(f"  GUI 展示的 '起始 $300' 与实际基线 {b0:.2f} 不一致；")
print(f"  GUI 的 realized_pnl 字段 = -17.66 只覆盖当前统计时代（07:27 起），")
print(f"  而余额 264.18 承载了全部历史（含 era 之前的亏损）。")
