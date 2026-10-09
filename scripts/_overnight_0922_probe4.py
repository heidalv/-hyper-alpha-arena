# -*- coding: utf-8 -*-
"""侦察 4：账户权益口径 / 权威持仓 / 全历史库存重建。只读。"""
from __future__ import annotations

import pathlib
from collections import defaultdict
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

p("== arbitrage_paper_accounts ==")
cur.execute("""select column_name from information_schema.columns
               where table_name='arbitrage_paper_accounts' order by ordinal_position""")
cols = [r[0] for r in cur.fetchall()]
p("  列:", cols)
cur.execute("select * from arbitrage_paper_accounts order by id")
names = [d[0] for d in cur.description]
for row in cur.fetchall():
    d = dict(zip(names, row))
    keep = {k: d.get(k) for k in ("id", "name", "strategy_id", "total_equity", "realized_pnl",
                                  "available_balance", "frozen_balance", "updated_at", "created_at")
            if k in d}
    p("  ", keep)

p("\n== arbitrage_paper_ledgers 最近 10 条 ==")
try:
    cur.execute("""select column_name from information_schema.columns
                   where table_name='arbitrage_paper_ledgers' order by ordinal_position""")
    p("  列:", [r[0] for r in cur.fetchall()])
    cur.execute("select * from arbitrage_paper_ledgers order by id desc limit 10")
    names = [d[0] for d in cur.description]
    for row in cur.fetchall():
        p("  ", {k: str(v)[:60] for k, v in zip(names, row)})
except Exception as e:
    p("  err:", e)

# 用系统自己的函数取权威持仓
p("\n== lane_ledger.open_positions(mm_asterdex, days=30) 权威视图 ==")
try:
    import sys
    sys.path.insert(0, str(ROOT))
    from backend.services import lane_ledger
    for r in lane_ledger.open_positions(lane_id="mm_asterdex", days=30.0):
        p(f"  {r['symbol']:<8} qty={r['qty']:<14} side={r['side']:<6} notional=${r['notional_usd']:<10} "
          f"avg_px={r['avg_px']:<14} fills={r['fills']:<6} net_bp={r['net_bp']:<10} realized={r['realized_usd']}")
except Exception as e:
    p("  err:", e)

# 全历史库存重建
p("\n== 全历史库存重建（自 2026-09-10 起，逐条腿累加） ==")
cur.execute("""
select ts, symbol, (meta_json->>'qty')::float, lower(meta_json->>'side'),
       (meta_json->>'mid_px')::float
from lane_ledger
where lane_id='mm_asterdex' and ts >= '2026-09-10 00:00' and event='fill'
order by ts
""")
inv = defaultdict(float)
mids = {}
hourly = {}
for ts, sym, qty, side, mid in cur.fetchall():
    inv[sym] += (1.0 if side == "buy" else -1.0) * float(qty or 0.0)
    if mid:
        mids[sym] = float(mid)
    if ts >= datetime(2026, 9, 21, 12, 0, tzinfo=timezone(timedelta(hours=8))):
        gross = sum(abs(v) * mids.get(k, 0.0) for k, v in inv.items())
        net = sum(v * mids.get(k, 0.0) for k, v in inv.items())
        h = ts.replace(minute=0, second=0, microsecond=0)
        old = hourly.get(h)
        if old is None or abs(net) > abs(old[2]):
            hourly[h] = (gross, {k: round(v, 4) for k, v in inv.items()}, net)
p("  期末库存:", {k: round(v, 6) for k, v in inv.items()})
p(f"\n  {'小时':<13}{'gross$':>9}{'net$':>9}   各币 qty")
for h in sorted(hourly):
    g, per, net = hourly[h]
    p(f"  {h:%m-%d %H:%M}{g:>9.0f}{net:>+9.0f}   {per}")

# 窗口内 peak gross/net（用 5 分钟采样更准）
p("\n== 窗口内每分钟敞口峰值（2026-09-21 18:00 起） ==")
inv.clear(); mids.clear()
cur.execute("""
select ts, symbol, (meta_json->>'qty')::float, lower(meta_json->>'side'),
       (meta_json->>'mid_px')::float
from lane_ledger where lane_id='mm_asterdex' and event='fill' and ts >= '2026-09-10' order by ts
""")
series = []
for ts, sym, qty, side, mid in cur.fetchall():
    inv[sym] += (1.0 if side == "buy" else -1.0) * float(qty or 0.0)
    if mid:
        mids[sym] = float(mid)
    if ts >= datetime(2026, 9, 21, 18, 0, tzinfo=timezone(timedelta(hours=8))):
        gross = sum(abs(v) * mids.get(k, 0.0) for k, v in inv.items())
        net = sum(v * mids.get(k, 0.0) for k, v in inv.items())
        series.append((ts, gross, net, dict(inv)))
if series:
    gm = max(series, key=lambda x: x[1])
    nm = max(series, key=lambda x: abs(x[2]))
    p(f"  峰值总敞口 ${gm[1]:,.0f} @ {gm[0]:%m-%d %H:%M}  净敞口 ${gm[2]:+,.0f}")
    p(f"  峰值净敞口 ${nm[2]:+,.0f} @ {nm[0]:%m-%d %H:%M}  总敞口 ${nm[1]:,.0f}")
    # 时间加权
    tot_w, tot_g, tot_n = 0.0, 0.0, 0.0
    for i in range(1, len(series)):
        dt = (series[i][0] - series[i - 1][0]).total_seconds()
        if dt > 600:
            continue
        tot_w += dt; tot_g += series[i][1] * dt; tot_n += abs(series[i][2]) * dt
    p(f"  时间加权平均总敞口 ${tot_g/max(tot_w,1):,.0f}  平均净敞口 ${tot_n/max(tot_w,1):,.0f}"
      f"  （覆盖 {tot_w/3600:.2f} 小时）")
    eq = 267.9
    p(f"  ⇒ 峰值总敞口/权益 = {gm[1]/eq:.1f}x   时间加权总敞口/权益 = {tot_g/max(tot_w,1)/eq:.1f}x")

conn.close()
ROOT.joinpath("logs/_tmp_timeline/probe4.txt").write_text("\n".join(OUT), encoding="utf-8")
print("saved")

