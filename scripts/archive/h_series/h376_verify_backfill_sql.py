# -*- coding: utf-8 -*-
"""h376b 验证：asterdex 回填 SQL（15s 降采样）对 7 个新币可用。"""
import sys
sys.path.insert(0, ".")
from scripts.h356_universe_trial import read_env_dsn  # noqa: E402
import psycopg  # noqa: E402

NEW = ["DOGE", "SUI", "NEAR", "ARB", "ADA", "XRP", "ENA"]
with psycopg.connect(read_env_dsn().replace("/alpha_arena", "/alpha_market")) as c:
    with c.cursor() as cur:
        for s in NEW:
            cur.execute("""
                SELECT (event_ts_ms/15000)::bigint*15000 AS ts,
                       (array_agg(bid_px ORDER BY event_ts_ms DESC))[1] AS bb,
                       (array_agg(ask_px ORDER BY event_ts_ms DESC))[1] AS ba
                FROM asterdex_book_ticker
                WHERE symbol=CONCAT(CAST(%s AS TEXT),'USDT')
                  AND event_ts_ms > (extract(epoch from now())*1000 - 3*3600*1000)::bigint
                  AND bid_px>0 AND ask_px>bid_px
                GROUP BY ts ORDER BY ts DESC LIMIT 240
            """, (s,))
            rows = cur.fetchall()
            n = len(rows)
            if n:
                mids = [(float(r[1]) + float(r[2])) / 2.0 for r in reversed(rows)]
                span_s = (rows[0][0] - rows[-1][0]) / 1000.0
                print(f"{s}: {n} 条（{span_s/60:.0f} 分钟跨度，15s 桶）中价范围 "
                      f"{min(mids):.2f}-{max(mids):.2f}")
            else:
                print(f"{s}: 0 条 ✗")
