# -*- coding: utf-8 -*-
"""近 12 小时账本：手续费、价差、方向各亏多少，以及出口。"""
from sqlalchemy import text
from backend.core.tenant import system_identity
from backend.database.connection import SessionLocal

SQL_TOTAL = """
SELECT count(*) n,
       round((sum(net_bp*notional)/nullif(sum(notional),0))::numeric, 2) net_bp,
       round((sum(fee_bp*notional)/nullif(sum(notional),0))::numeric, 2) fee_bp,
       round((sum(spread_bp*notional)/nullif(sum(notional),0))::numeric, 2) spread_bp,
       round((sum(price_bp*notional)/nullif(sum(notional),0))::numeric, 2) price_bp,
       round((sum(net_bp*notional)/10000.0)::numeric, 2) net_usd,
       round(sum(notional)::numeric, 0) notional
FROM lane_ledger
WHERE lane_id='mm_asterdex' AND ts > now() - interval '12 hours' AND event='fill'
"""
SQL_PATH = """
SELECT coalesce(meta_json::json->>'exit_path', meta_json::json->>'exit_reason', '(entry)') AS path,
       count(*) n,
       round((sum(fee_bp*notional)/nullif(sum(notional),0))::numeric, 2) fee_bp,
       round((sum(spread_bp*notional)/nullif(sum(notional),0))::numeric, 2) spread_bp,
       round((sum(price_bp*notional)/nullif(sum(notional),0))::numeric, 2) price_bp,
       round((sum(net_bp*notional)/10000.0)::numeric, 2) net_usd
FROM lane_ledger
WHERE lane_id='mm_asterdex' AND ts > now() - interval '12 hours' AND event='fill'
GROUP BY 1
ORDER BY sum(net_bp*notional) ASC
"""
SQL_SIDE = """
SELECT coalesce(meta_json::json->>'side', '?') AS side,
       coalesce(meta_json::json->>'flatten', '?') AS flatten,
       count(*) n,
       round((sum(spread_bp*notional)/nullif(sum(notional),0))::numeric, 2) spread_bp,
       round((sum(price_bp*notional)/nullif(sum(notional),0))::numeric, 2) price_bp,
       round((sum(fee_bp*notional)/nullif(sum(notional),0))::numeric, 2) fee_bp,
       round((sum(net_bp*notional)/10000.0)::numeric, 2) net_usd
FROM lane_ledger
WHERE lane_id='mm_asterdex' AND ts > now() - interval '12 hours' AND event='fill'
GROUP BY 1, 2
ORDER BY 1, 2
"""
with system_identity():
    with SessionLocal() as db:
        print("TOTAL", dict(db.execute(text(SQL_TOTAL)).mappings().one()))
        print("--- path ---")
        for r in db.execute(text(SQL_PATH)).mappings():
            print(dict(r))
        print("--- side ---")
        for r in db.execute(text(SQL_SIDE)).mappings():
            print(dict(r))
