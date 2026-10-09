# -*- coding: utf-8 -*-
"""昨晚(2026-09-21 夜 ~ 2026-09-22 晨)高频做市车道侦察。

只读。输出用于确定分析口径。
"""
from __future__ import annotations

import pathlib
from datetime import datetime, timedelta

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


A = datetime(2026, 9, 21, 12, 0)
B = datetime(2026, 9, 22, 9, 15)

import psycopg  # noqa: E402

with psycopg.connect(dsn()) as c:
    cur = c.cursor()
    cur.execute("select current_setting('TimeZone'), now()")
    print("session tz / now:", cur.fetchone())

    print("\n== 各 lane 在窗口内的行数/时间范围 ==")
    cur.execute("""
        select lane_id, count(*), min(ts), max(ts)
        from lane_ledger where ts >= %s group by 1 order by 2 desc
    """, (A,))
    for r in cur.fetchall():
        print("  ", r)

    print("\n== lane_ledger 列 ==")
    cur.execute("""
        select column_name, data_type from information_schema.columns
        where table_name='lane_ledger' order by ordinal_position
    """)
    print("  ", [r[0] for r in cur.fetchall()])

    print("\n== mm_asterdex 按小时（原始口径）==")
    cur.execute("""
        select date_trunc('hour', ts) h, count(*) n,
               sum(net_bp*notional/1e4) usd,
               sum(fee_bp*notional/1e4) fee_usd,
               count(*) filter (where lower(coalesce(meta_json->>'flatten','')) in ('true','1')) flat_n,
               count(distinct symbol) syms
        from lane_ledger
        where lane_id='mm_asterdex' and ts >= %s and ts < %s
        group by 1 order by 1
    """, (A, B))
    for h, n, usd, fee, flat, syms in cur.fetchall():
        print(f"  {h:%m-%d %H:%M}  n={n:6d}  usd={float(usd or 0):9.3f}  fee={float(fee or 0):8.3f}  flat={flat:4d}  syms={syms}")

    print("\n== 事件类型分布 ==")
    cur.execute("""
        select event, count(*), sum(net_bp*notional/1e4)
        from lane_ledger where lane_id='mm_asterdex' and ts >= %s group by 1 order by 2 desc
    """, (A,))
    for r in cur.fetchall():
        print("  ", r)

    print("\n== meta_json 键（样本） ==")
    cur.execute("""
        select meta_json from lane_ledger
        where lane_id='mm_asterdex' and ts >= %s and meta_json is not null
        order by ts desc limit 3
    """, (A,))
    for (m,) in cur.fetchall():
        print("  ", m)

    print("\n== 其它相关表（名字含 mm/lane/paper 的） ==")
    cur.execute("""
        select table_name from information_schema.tables
        where table_schema='public' and (table_name like '%%mm%%' or table_name like '%%lane%%'
              or table_name like '%%paper%%' or table_name like '%%rebate%%')
        order by 1
    """)
    print("  ", [r[0] for r in cur.fetchall()])
