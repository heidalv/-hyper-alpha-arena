# -*- coding: utf-8 -*-
"""M0-P2v2 验证：同窗口两次回测结果确定一致 + 第二次复用序列更快。"""
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
from backend.services.live_pipeline_backtest_engine import (
    LivePipelineBacktestEngine, DEFAULT_PIPELINE_PARAMS,
    _FACTOR_DIR_CACHE,
)

ev = StrategyEvolver()
bars = ev._load_bars("BTC", "5m", 3)  # 3天 ≈ 864 根，控制首算时间

genome = dict(DEFAULT_PIPELINE_PARAMS)
genome["factor_signal_interval"] = 3

t0 = time.time()
r1 = LivePipelineBacktestEngine(initial_capital=10000).run(
    bars, genome, tier="short",
)
t1 = time.time() - t0
print(f"run1: {t1:.1f}s trades={r1.total_trades if r1 else None} sharpe={r1.sharpe_ratio if r1 else None}")

t0 = time.time()
r2 = LivePipelineBacktestEngine(initial_capital=10000).run(
    bars, genome, tier="short",
)
t2 = time.time() - t0
print(f"run2: {t2:.1f}s trades={r2.total_trades if r2 else None} sharpe={r2.sharpe_ratio if r2 else None}")

same = (
    r1 is not None and r2 is not None
    and r1.total_trades == r2.total_trades
    and abs((r1.sharpe_ratio or 0) - (r2.sharpe_ratio or 0)) < 1e-9
    and r1.total_return == r2.total_return
)
print("cache entries:", len(_FACTOR_DIR_CACHE))
print("DETERMINISTIC:", same)
print("SPEEDUP: %.1fx" % (t1 / max(t2, 1e-6)))
db.close()
