# -*- coding: utf-8 -*-
"""测时：100 次窗口因子方向计算的平均耗时。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.strategy_evolver import StrategyEvolver
from backend.services.live_pipeline_backtest_engine import LivePipelineBacktestEngine

ev = StrategyEvolver()
bars = ev._load_bars("BTC", "5m", 8)
print(f"bars: {len(bars)}", flush=True)

eng = LivePipelineBacktestEngine(initial_capital=10000)
warmup = 30
t0 = time.time()
for i in range(warmup, warmup + 100):
    eng._compute_factor_direction_windowed(i, bars)
dt = time.time() - t0
print(f"100 windows: {dt:.1f}s = {dt*10:.1f}ms/window", flush=True)

# 全量预计算耗时外推
n = len(bars) - warmup
print(f"estimated full precompute: {dt/100*n:.0f}s", flush=True)
