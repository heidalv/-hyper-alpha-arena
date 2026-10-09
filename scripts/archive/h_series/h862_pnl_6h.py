# -*- coding: utf-8 -*-
from sqlalchemy import text
from backend.core.tenant import system_identity
from backend.database.connection import SessionLocal

SQL = """
SELECT symbol,
       count(*) n,
       round((sum(net_bp*notional)/nullif(sum(notional),0))::numeric, 2) net_bp,
       round((sum(fee_bp*notional)/nullif(sum(notional),0))::numeric, 2) fee_bp,
       round((sum(price_bp*notional)/nullif(sum(notional),0))::numeric, 2) price_bp,
       round((sum(spread_bp*notional)/nullif(sum(notional),0))::numeric, 2) spread_bp,
       round((sum(net_bp*notional)/10000.0)::numeric, 2) net_usd
FROM lane_ledger
WHERE lane_id='mm_asterdex' AND ts > now() - interval '6 hours' AND event='fill'
GROUP BY symbol
ORDER BY sum(net_bp*notional) ASC
LIMIT 12
"""
SQL2 = """
SELECT count(*) n,
       round((sum(net_bp*notional)/nullif(sum(notional),0))::numeric, 2) net_bp,
       round((sum(fee_bp*notional)/nullif(sum(notional),0))::numeric, 2) fee_bp,
       round((sum(spread_bp*notional)/nullif(sum(notional),0))::numeric, 2) spread_bp,
       round((sum(price_bp*notional)/nullif(sum(notional),0))::numeric, 2) price_bp,
       round((sum(net_bp*notional)/10000.0)::numeric, 2) net_usd,
       round(sum(notional)::numeric, 0) notional
FROM lane_ledger
WHERE lane_id='mm_asterdex' AND ts > now() - interval '6 hours' AND event='fill'
"""
with system_identity():
    with SessionLocal() as db:
        print("TOTAL", dict(db.execute(text(SQL2)).mappings().one()))
        for r in db.execute(text(SQL)).mappings():
            print(dict(r))
