# -*- coding: utf-8 -*-
"""临时核查：冠军落库 + evolution_events + 晋升模板状态（M0-E1 验证）。"""
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
        "SELECT run_id, template_id, is_champion, generation, sharpe_ratio, "
        "win_rate, max_drawdown, total_trades, completed_at "
        "FROM backtest_runs WHERE run_id='champ_3846025d'"
    )).fetchone()
    print("champion row:", r)
    ev = c.execute(text(
        "SELECT evolution_type, success, template_count, promoted_count, "
        "best_fitness, created_at FROM evolution_events "
        "WHERE created_at > now() - interval '3 hours' ORDER BY created_at DESC"
    )).fetchall()
    for x in ev:
        print("event:", x)
    tp = c.execute(text(
        "SELECT template_id, rating, backtest_sharpe, backtest_win_rate, "
        "backtest_max_drawdown, backtest_total_trades "
        "FROM strategy_templates WHERE template_id='tpl_mid_bull_momentum'"
    )).fetchone()
    print("template:", tp)
