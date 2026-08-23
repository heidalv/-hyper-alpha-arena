# -*- coding: utf-8 -*-
"""核查：账户14 权益/已实现盈亏/持仓状态 + 短线结算趋势。"""
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
    r = c.execute(text(
        "SELECT id, name, initial_capital, current_cash, is_active, auto_trading_enabled "
        "FROM accounts WHERE id=14"
    )).fetchone()
    print("account14:", r)
    # paper_positions 账户14 持仓与未实现
    pos = c.execute(text(
        "SELECT symbol, side, size, entry_price, mark_price, unrealized_pnl "
        "FROM paper_positions WHERE account_id=14 AND status='OPEN' ORDER BY symbol"
    )).fetchall()
    print("open positions (status filter test):", pos)
    # 若 status 列不存在，退化为无过滤
    if not pos:
        pos2 = c.execute(text(
            "SELECT symbol, side, size, entry_price, mark_price, unrealized_pnl "
            "FROM paper_positions WHERE account_id=14 LIMIT 8"
        )).fetchall()
        print("any positions:", pos2)
