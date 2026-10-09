# -*- coding: utf-8 -*-
"""h354 部署前验证：asterdex_trades 的 VWAP 查询（带显式 CAST 的版本）。"""
import sys

sys.path.insert(0, ".")
from scripts.h354_p2_deploy import read_env_dsn  # noqa: E402

import psycopg  # noqa: E402

with psycopg.connect(read_env_dsn().replace("/alpha_arena", "/alpha_market")) as c:
    with c.cursor() as cur:
        cur.execute("SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_name='asterdex_trades' ORDER BY ordinal_position")
        print("cols:", cur.fetchall())
        cur.execute("SELECT max(event_ts_ms) FROM asterdex_trades")
        mx = cur.fetchone()[0]
        print("max event_ts_ms:", mx)
        cur.execute(
            "SELECT COALESCE(sum(price*qty)/NULLIF(sum(qty),0),0) AS vw"
            " FROM asterdex_trades"
            " WHERE symbol = CONCAT(CAST(%s AS TEXT), 'USDT')"
            "   AND event_ts_ms > CAST(%s AS BIGINT)"
            "   AND event_ts_ms <= CAST(%s AS BIGINT)",
            ("BTC", mx - 60000, mx))
        vw = cur.fetchone()[0]
        print("BTC 60s vwap:", vw)
        assert vw and float(vw) > 0, "VWAP 为空"
print("OK: 查询可用")
