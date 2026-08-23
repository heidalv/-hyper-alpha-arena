# -*- coding: utf-8 -*-
"""分块测时：窗口计算各环节耗时。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd

from backend.services.strategy_evolver import StrategyEvolver
from backend.services.live_pipeline_backtest_engine import LivePipelineBacktestEngine
from services.factor_engine import factor_engine, FactorSignalGenerator

ev = StrategyEvolver()
bars = ev._load_bars("BTC", "5m", 8)
print(f"bars: {len(bars)}", flush=True)
eng = LivePipelineBacktestEngine(initial_capital=10000)
warmup = 30
N = 100
i0 = warmup + 50

# 1. DataFrame 构造
t0 = time.time()
for k in range(N):
    i = i0 + k
    ws = max(0, i - 29)
    wb = bars[ws:i + 1]
    df = pd.DataFrame([{'open': b.o, 'high': b.h, 'low': b.l, 'close': b.c, 'volume': b.v, 'timestamp': b.timestamp} for b in wb])
t_df = (time.time() - t0) / N * 1000
print(f"df construct: {t_df:.1f}ms", flush=True)

# 2. compute_all_factors
t0 = time.time()
for k in range(N):
    i = i0 + k
    ws = max(0, i - 29)
    wb = bars[ws:i + 1]
    df = pd.DataFrame([{'open': b.o, 'high': b.h, 'low': b.l, 'close': b.c, 'volume': b.v, 'timestamp': b.timestamp} for b in wb])
    fv = factor_engine.compute_all_factors(df)
t_caf = (time.time() - t0) / N * 1000
print(f"compute_all_factors: {t_caf:.1f}ms", flush=True)

# 3. generate_signals
t0 = time.time()
for k in range(N):
    i = i0 + k
    ws = max(0, i - 29)
    wb = bars[ws:i + 1]
    df = pd.DataFrame([{'open': b.o, 'high': b.h, 'low': b.l, 'close': b.c, 'volume': b.v, 'timestamp': b.timestamp} for b in wb])
    fv = factor_engine.compute_all_factors(df)
    gen = FactorSignalGenerator()
    c = gen.generate_signals(fv)
t_gs = (time.time() - t0) / N * 1000
print(f"full incl. signals: {t_gs:.1f}ms (signals+gen ≈ {t_gs - t_caf:.1f}ms)", flush=True)

# 4. 每个因子耗时分布（单窗口）
i = i0
ws = max(0, i - 29)
wb = bars[ws:i + 1]
df = pd.DataFrame([{'open': b.o, 'high': b.h, 'low': b.l, 'close': b.c, 'volume': b.v, 'timestamp': b.timestamp} for b in wb])
per = {}
for name, config in list(factor_engine.FACTORS.items()):
    t0 = time.time()
    try:
        v = config['compute'](df, None)
    except Exception:
        v = None
    per[name] = (time.time() - t0) * 1000
for name, ms in sorted(per.items(), key=lambda x: -x[1])[:10]:
    print(f"  {name}: {ms:.1f}ms", flush=True)
