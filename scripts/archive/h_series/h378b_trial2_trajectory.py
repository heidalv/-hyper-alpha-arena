# -*- coding: utf-8 -*-
"""#2 试跑时代轨迹：30 分钟桶的净 bp/腿数与分币种累计（定位"先盈后崩"的时点）。"""
import sys
sys.path.insert(0, ".")
from scripts.h356_universe_trial import read_env_dsn  # noqa: E402
import psycopg  # noqa: E402

with psycopg.connect(read_env_dsn()) as c:
    with c.cursor() as cur:
        cur.execute("SELECT meta_json->'h356_trial'->>'started_at' "
                    "FROM lane_registry WHERE lane_id='mm_asterdex'")
        since = cur.fetchone()[0]
        print(f"#2 since: {since}")
        cur.execute("""
            SELECT to_timestamp(floor(extract(epoch from ts) / 1800) * 1800) AT TIME ZONE 'Asia/Shanghai' AS bucket,
                   count(*) AS legs,
                   sum(net_bp)::float8 AS net_bp,
                   sum(net_bp/1e4*notional)::float8 AS net_usd
            FROM lane_ledger
            WHERE lane_id='mm_asterdex' AND ts > %s::timestamptz
            GROUP BY 1 ORDER BY 1
        """, (since,))
        print(f"\n{'桶(30min)':<22} {'腿':>5} {'净bp':>9} {'净USD':>9}")
        tot_bp, tot_usd, tot_legs = 0.0, 0.0, 0
        for r in cur.fetchall():
            print(f"{str(r[0])[:16]:<22} {r[1]:>5} {float(r[2] or 0):>+9.1f} {float(r[3] or 0):>+9.3f}")
            tot_bp += float(r[2] or 0)
            tot_usd += float(r[3] or 0)
            tot_legs += r[1]
        print(f"{'TOTAL':<22} {tot_legs:>5} {tot_bp:>+9.1f} {tot_usd:>+9.3f}")
        cur.execute("""
            SELECT symbol, count(*), sum(net_bp)::float8, sum(net_bp/1e4*notional)::float8
            FROM lane_ledger WHERE lane_id='mm_asterdex' AND ts > %s::timestamptz
            GROUP BY symbol ORDER BY 3
        """, (since,))
        print(f"\n分币种（全时代）:")
        for r in cur.fetchall():
            print(f"  {r[0]:<8} {r[1]:>5} 腿  净 {float(r[2] or 0):>+8.1f} bp   {float(r[3] or 0):>+8.3f} USD")
