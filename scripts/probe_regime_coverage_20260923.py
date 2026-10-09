# -*- coding: utf-8 -*-
"""When did regime_type become 'unknown'? What's usable in the 30d window?"""
import sys
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
import psycopg

ANALYTICS = "postgresql://laobao:alpha_pass@localhost:5432/alpha_analytics"
with psycopg.connect(ANALYTICS, autocommit=True) as c:
    cur = c.cursor()
    cur.execute("""SELECT date_trunc('week', created_at) w, regime_type, count(*)
                   FROM market_analysis_snapshots
                   WHERE created_at > now() - interval '60 days'
                   GROUP BY 1,2 ORDER BY 1,3 DESC""")
    print("== regime_type by week (60d) ==")
    for r in cur.fetchall():
        print("  ", r[0], r[1], r[2])
    cur.execute("""SELECT regime_direction, count(*) FROM market_analysis_snapshots
                   WHERE created_at > now() - interval '30 days'
                   GROUP BY 1 ORDER BY 2 DESC""")
    print("== regime_direction (30d) ==")
    for r in cur.fetchall():
        print("  ", r[0], r[1])
    cur.execute("""SELECT period, count(*) FROM market_analysis_snapshots
                   WHERE created_at > now() - interval '30 days' GROUP BY 1""")
    print("== period (30d) ==")
    for r in cur.fetchall():
        print("  ", r[0], r[1])
    # indicator_snapshot.direction_label availability in window
    cur.execute("""SELECT (indicator_snapshot->>'direction_label') dl, count(*)
                   FROM market_analysis_snapshots
                   WHERE created_at > now() - interval '30 days'
                   GROUP BY 1 ORDER BY 2 DESC""")
    print("== indicator_snapshot.direction_label (30d) ==")
    for r in cur.fetchall():
        print("  ", r[0], r[1])
    cur.execute("""SELECT (indicator_snapshot->>'regime') rg, count(*)
                   FROM market_analysis_snapshots
                   WHERE created_at > now() - interval '30 days'
                   GROUP BY 1 ORDER BY 2 DESC""")
    print("== indicator_snapshot.regime (30d) ==")
    for r in cur.fetchall():
        print("  ", r[0], r[1])
