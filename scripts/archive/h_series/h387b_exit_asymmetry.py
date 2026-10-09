# -*- coding: utf-8 -*-
"""#2 时代出口类型分布（TP/止损/衰减/超时/普通配对 不对称统计）。"""
import sys
sys.path.insert(0, ".")
from scripts.h356_universe_trial import read_env_dsn  # noqa: E402
import psycopg  # noqa: E402

with psycopg.connect(read_env_dsn()) as c:
    with c.cursor() as cur:
        cur.execute("""
            SELECT COALESCE(meta_json->>'exit_path','') AS ep, count(*),
                   sum(net_bp)::float8, avg(net_bp)::float8
            FROM lane_ledger
            WHERE lane_id='mm_asterdex'
              AND ts > '2026-09-27 13:00:00+08'::timestamptz
            GROUP BY 1 ORDER BY 2 DESC
        """)
        print("== #2 时代出口类型分布（腿 / 净bp总和 / 均值） ==")
        for r in cur.fetchall():
            label = r[0] if r[0] else "(普通配对)"
            print(f"  {label:<20} 腿={r[1]:>4} 总和={float(r[2] or 0):>+9.1f} 均值={float(r[3] or 0):>+7.2f}")
