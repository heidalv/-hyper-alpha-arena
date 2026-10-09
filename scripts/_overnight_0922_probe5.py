# -*- coding: utf-8 -*-
"""侦察 5：账本口径 vs 账户口径对账。只读。"""
from __future__ import annotations

import pathlib
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]


def dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env.get("DATABASE_URL", "")
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


import psycopg  # noqa: E402

OUT = []


def p(*a):
    OUT.append(" ".join(str(x) for x in a))


conn = psycopg.connect(dsn())
conn.autocommit = True
cur = conn.cursor()

p("== 账户101 全部流水概览 ==")
cur.execute("""
select action, count(*), round(sum(amount_usd)::numeric,4), min(created_at), max(created_at)
from arbitrage_paper_ledgers where account_id=101 group by 1 order by 2 desc
""")
for r in cur.fetchall():
    p("  ", r)

p("\n== 账户101 按天净额 ==")
cur.execute("""
select created_at::date d, count(*), round(sum(amount_usd)::numeric,4)
from arbitrage_paper_ledgers where account_id=101 group by 1 order by 1
""")
for r in cur.fetchall():
    p("  ", r)

p("\n== 窗口内 账户口径 vs 账本口径 按小时 ==")
cur.execute("""
select to_char(date_trunc('hour', created_at),'MM-DD HH24:00') h, count(*), round(sum(amount_usd)::numeric,4)
from arbitrage_paper_ledgers
where account_id=101 and created_at >= '2026-09-21 18:00'
group by 1 order by 1
""")
acct_h = {r[0]: (r[1], float(r[2])) for r in cur.fetchall()}
cur.execute("""
select to_char(date_trunc('hour', ts),'MM-DD HH24:00') h, count(*), round(sum(net_bp*notional/1e4)::numeric,4)
from lane_ledger
where lane_id='mm_asterdex' and ts >= '2026-09-21 18:00'
group by 1 order by 1
""")
led_h = {r[0]: (r[1], float(r[2])) for r in cur.fetchall()}
p(f"  {'小时':<14}{'账户n':>7}{'账户$':>11}{'账本n':>7}{'账本$':>11}{'差$':>10}")
ta = tl = 0.0
for h in sorted(set(acct_h) | set(led_h)):
    an, av = acct_h.get(h, (0, 0.0))
    ln, lv = led_h.get(h, (0, 0.0))
    ta += av; tl += lv
    p(f"  {h}{an:>7}{av:>11.3f}{ln:>7}{lv:>11.3f}{av-lv:>10.3f}")
p(f"  {'合计':<14}{'':>7}{ta:>11.3f}{'':>7}{tl:>11.3f}{ta-tl:>10.3f}")

p("\n== 账户余额曲线（窗口内每小时的末尾余额） ==")
cur.execute("""
select distinct on (date_trunc('hour', created_at)) to_char(date_trunc('hour', created_at),'MM-DD HH24:00') h,
       balance_after, created_at
from arbitrage_paper_ledgers
where account_id=101 and created_at >= '2026-09-21 17:00'
order by date_trunc('hour', created_at), created_at desc
""")
for h, bal, ts in cur.fetchall():
    p(f"  {h}  余额 ${float(bal):.4f}")

p("\n== 窗口首末 ==")
cur.execute("""
(select 'first' k, created_at, amount_usd, balance_after from arbitrage_paper_ledgers
 where account_id=101 and created_at >= '2026-09-21 18:00' order by created_at limit 1)
union all
(select 'last', created_at, amount_usd, balance_after from arbitrage_paper_ledgers
 where account_id=101 and created_at >= '2026-09-21 18:00' order by created_at desc limit 1)
""")
for r in cur.fetchall():
    p("  ", r)

p("\n== 账户101 当前行 ==")
cur.execute("""select total_equity, realized_pnl, available_balance, frozen_balance, updated_at
               from arbitrage_paper_accounts where id=101""")
p("  ", cur.fetchone())

conn.close()
ROOT.joinpath("logs/_tmp_timeline/probe5.txt").write_text("\n".join(OUT), encoding="utf-8")
print("saved")

