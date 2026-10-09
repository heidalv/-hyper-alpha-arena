# -*- coding: utf-8 -*-
"""侦察 3：position_id 结构 / flatten meta / 库存可重建性。只读。"""
from __future__ import annotations

import pathlib
from datetime import datetime

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

W0 = datetime(2026, 9, 21, 18, 0)
W1 = datetime(2026, 9, 22, 9, 15)
OUT = []


def p(*a):
    s = " ".join(str(x) for x in a)
    OUT.append(s)


conn = psycopg.connect(dsn())
conn.autocommit = True
cur = conn.cursor()

p("== position_id 分布（窗口内） ==")
cur.execute("""
select position_id, symbol, count(*), min(ts), max(ts)
from lane_ledger where lane_id='mm_asterdex' and ts >= %s and ts < %s
group by 1,2 order by 1 limit 40
""", (W0, W1))
for r in cur.fetchall():
    p("  ", r)

p("\n== position_id_state 列内容（样本） ==")
cur.execute("""
select position_id, position_id_state, count(*) from lane_ledger
where lane_id='mm_asterdex' and ts >= %s group by 1,2 order by 3 desc limit 15
""", (W0,))
for r in cur.fetchall():
    p("  ", r)

p("\n== flatten 腿的 meta（近 12 条） ==")
cur.execute("""
select ts, symbol, notional, net_bp, spread_bp, price_bp, fee_bp, meta_json
from lane_ledger
where lane_id='mm_asterdex' and ts >= %s
  and lower(coalesce(meta_json->>'flatten','false')) in ('true','1')
order by ts desc limit 12
""", (W0,))
for ts, sym, not_, nbp, sbp, pbp, fbp, m in cur.fetchall():
    m2 = dict(m or {})
    for k in ("seg_low", "seg_high", "mid_px", "fill_px"):
        m2.pop(k, None)
    p(f"  {ts:%m-%d %H:%M:%S} {sym:<7} notional={float(not_):8.1f} net_bp={float(nbp):+8.2f} "
      f"(sp {float(sbp):+6.2f} px {float(pbp):+7.2f} fee {float(fbp):+5.2f}) {m2}")

p("\n== qty 字段可用性（能否重建库存） ==")
cur.execute("""
select count(*), count(meta_json->>'qty'), count(meta_json->>'side'),
       sum(case when (meta_json->>'qty') ~ '^[0-9.eE+-]+$' then 1 else 0 end)
from lane_ledger where lane_id='mm_asterdex' and ts >= %s
""", (W0,))
p("   rows / qty非空 / side非空 / qty可转数字:", cur.fetchone())

p("\n== 前夜同口径对照（2026-09-20 18:00 → 09-21 09:15） ==")
cur.execute("""
select date_trunc('hour', ts) h, count(*),
       count(*) filter (where lower(coalesce(meta_json->>'flatten','false')) in ('true','1')),
       sum(net_bp*notional/1e4),
       sum(net_bp*notional/1e4) filter (where lower(coalesce(meta_json->>'flatten','false')) not in ('true','1')),
       sum(fee_bp*notional/1e4)
from lane_ledger where lane_id='mm_asterdex' and ts >= '2026-09-20 18:00' and ts < '2026-09-21 09:15'
group by 1 order by 1
""")
tot = [0, 0, 0.0, 0.0, 0.0]
for h, n, f, u, mu, fee in cur.fetchall():
    tot[0] += n; tot[1] += f; tot[2] += float(u or 0); tot[3] += float(mu or 0); tot[4] += float(fee or 0)
    p(f"  {h:%m-%d %H:%M} n={n:5d} flat={f:4d} usd={float(u or 0):9.3f} maker_usd={float(mu or 0):9.3f} fee={float(fee or 0):8.3f}")
p(f"  合计: n={tot[0]} flat={tot[1]} usd={tot[2]:.3f} maker={tot[3]:.3f} fee={tot[4]:.3f}")

p("\n== 更早对照：09-19 18:00 → 09-20 09:15 ==")
cur.execute("""
select count(*),
       count(*) filter (where lower(coalesce(meta_json->>'flatten','false')) in ('true','1')),
       sum(net_bp*notional/1e4),
       sum(net_bp*notional/1e4) filter (where lower(coalesce(meta_json->>'flatten','false')) not in ('true','1')),
       sum(fee_bp*notional/1e4)
from lane_ledger where lane_id='mm_asterdex' and ts >= '2026-09-19 18:00' and ts < '2026-09-20 09:15'
""")
p("  ", cur.fetchone())

p("\n== 全历史按天 ==")
cur.execute("""
select date_trunc('day', ts)::date d, count(*),
       count(*) filter (where lower(coalesce(meta_json->>'flatten','false')) in ('true','1')) fl,
       round(sum(net_bp*notional/1e4)::numeric,3) usd,
       round(sum(net_bp*notional/1e4) filter (where lower(coalesce(meta_json->>'flatten','false')) not in ('true','1'))::numeric,3) maker,
       round(sum(fee_bp*notional/1e4)::numeric,3) fee
from lane_ledger where lane_id='mm_asterdex' group by 1 order by 1
""")
for r in cur.fetchall():
    p("  ", r)

conn.close()
ROOT.joinpath("logs/_tmp_timeline/probe3.txt").write_text("\n".join(OUT), encoding="utf-8")
print("saved")
