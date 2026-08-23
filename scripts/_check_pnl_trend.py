# -*- coding: utf-8 -*-
"""核查：账户14 已实现盈亏趋势（position_exit_events / strategy_trades）。"""
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
    # strategy_trades 每日 pnl
    rows = c.execute(text(
        "SELECT date_trunc('day', closed_at)::date AS d, count(*), "
        "round(sum(pnl_pct)::numeric,4) FROM strategy_trades "
        "WHERE closed_at IS NOT NULL AND closed_at >= now() - interval '5 days' "
        "GROUP BY 1 ORDER BY 1"
    )).fetchall()
    print("strategy_trades 每日 pnl_pct 合计:")
    for r in rows:
        print(f"  {r[0]} n={r[1]} sum_pnl_pct={r[2]}")
    # position_exit_events 账户14
    try:
        cols = [x[0] for x in c.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='position_exit_events' ORDER BY ordinal_position"
        )).fetchall()]
        print("position_exit_events cols:", cols[:16])
    except Exception as e:
        print("exit events err:", e)
