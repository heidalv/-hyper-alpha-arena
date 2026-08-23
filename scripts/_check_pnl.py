# -*- coding: utf-8 -*-
"""临时核查：账户14 PnL 与短线结算统计。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine, text

_env = {}
for _line in Path(__file__).resolve().parents[1].joinpath(".env").read_text(encoding="utf-8").splitlines():
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        _env[_k] = _v

eng = create_engine(_env["DATABASE_URL"], pool_pre_ping=True)
with eng.connect() as c:
    c.execute(text("SET app.is_admin='on'"))
    c.execute(text("SET app.tenant_id=326"))
    # 账户14 的 equity/realized（trading_accounts 或 account_metrics 表，探测）
    try:
        r = c.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='trading_accounts' ORDER BY ordinal_position"
        )).fetchall()
        print("trading_accounts cols:", [x[0] for x in r][:20])
    except Exception as e:
        print("trading_accounts:", e)
    # 最近24h 短线结算统计（settle_ts 是 bigint epoch）
    try:
        n = c.execute(text("SELECT count(*) FROM scalp_signal_log")).scalar()
        print("scalp_signal_log rows (main db):", n)
        st = c.execute(text(
            "SELECT count(*), round(avg(net_ret)::numeric,6), "
            "round(sum(CASE WHEN win THEN 1 ELSE 0 END)::numeric/count(*),4), "
            "max(settle_ts) "
            "FROM scalp_signal_log WHERE settled AND settle_ts > (extract(epoch from now())-86400)::bigint"
        )).fetchone()
        print("24h settled:", st)
        t = c.execute(text(
            "SELECT count(*), round(coalesce(sum(pnl_pct),0)::numeric,4) "
            "FROM strategy_trades WHERE account_id=14"
        )).fetchone()
        print("strategy_trades account14:", t)
    except Exception as e:
        print("stats failed:", type(e).__name__, str(e)[:160])
