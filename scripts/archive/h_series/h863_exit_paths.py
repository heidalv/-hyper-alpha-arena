# -*- coding: utf-8 -*-
"""近 6 小时：按出口原因看手续费和净盈亏。"""
from sqlalchemy import text
from backend.core.tenant import system_identity
from backend.database.connection import SessionLocal

SQL = """
SELECT coalesce(meta_json::json->>'exit_path', '(none)') AS path,
       count(*) n,
       round((sum(fee_bp*notional)/nullif(sum(notional),0))::numeric, 2) fee_bp,
       round((sum(spread_bp*notional)/nullif(sum(notional),0))::numeric, 2) spread_bp,
       round((sum(price_bp*notional)/nullif(sum(notional),0))::numeric, 2) price_bp,
       round((sum(net_bp*notional)/nullif(sum(notional),0))::numeric, 2) net_bp,
       round((sum(net_bp*notional)/10000.0)::numeric, 2) net_usd
FROM lane_ledger
WHERE lane_id='mm_asterdex' AND ts > now() - interval '6 hours' AND event='fill'
GROUP BY 1
ORDER BY sum(net_bp*notional) ASC
"""
with system_identity():
    with SessionLocal() as db:
        for r in db.execute(text(SQL)).mappings():
            print(dict(r))
