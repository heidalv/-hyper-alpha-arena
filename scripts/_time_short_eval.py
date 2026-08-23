# -*- coding: utf-8 -*-
"""测时：短线 tier 单次 fitness 评估（interval=3 与 6 各一次）。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

_env = {}
for _line in Path(__file__).resolve().parents[1].joinpath(".env").read_text(encoding="utf-8").splitlines():
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        _env[_k] = _v

eng = create_engine(_env["DATABASE_URL"], pool_pre_ping=True)
db = Session(bind=eng)

from backend.services.strategy_evolver import StrategyEvolver
from backend.services.live_pipeline_backtest_engine import DEFAULT_PIPELINE_PARAMS

ev = StrategyEvolver()

class _Tpl:
    template_id = "tpl_short_pullback"
    name = "短线回调入场"
    tier = "short"

for interval in (6, 3):
    genome = dict(DEFAULT_PIPELINE_PARAMS)
    genome["factor_signal_interval"] = interval
    t0 = time.time()
    res = ev._run_single_backtest_for_genome(_Tpl(), genome, db)
    dt = time.time() - t0
    print(f"interval={interval}: {dt:.1f}s trades={res.get('total_trades') if res else None}")
db.close()
