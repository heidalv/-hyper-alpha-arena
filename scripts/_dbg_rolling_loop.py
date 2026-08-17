# -*- coding: utf-8 -*-
"""逐个 registry 因子跑 rolling 路径，抓崩溃因子与异常类型。"""
import os, sys, warnings, traceback
warnings.filterwarnings("ignore")
os.chdir(os.path.dirname(os.path.abspath(__file__)) + "/..")
sys.path.insert(0, ".")
sys.path.append("backend")

from backend.services.factor_engine.midlong_registry_factors import list_registry_factor_ids, _rolling_recompute
from backend.services.factor_engine.factor_calculator import FactorCalculator
from backend.services.factor_engine.factor_backtest_scorer import factor_backtest_scorer
import pandas as pd
import numpy as np

calc = FactorCalculator()
fids = list_registry_factor_ids()
print("fids:", len(fids))
for fid in fids:
    for sym, tf in (("BTC", "4h"),):
        klines = factor_backtest_scorer._load_klines(sym, tf, 2400)
        if not klines or len(klines) < 120:
            continue
        df = pd.DataFrame(klines)
        try:
            series_map = calc.calculate([fid], df, symbol=sym, timeframe=tf)
            s = series_map.get(fid)
            nfinite = int(np.isfinite(np.asarray(s, dtype=float)).sum()) if s is not None and len(s) else 0
            thresh = max(60, int(len(df) * 0.05))
            print(f"{fid}: full-finite={nfinite} thresh={thresh}")
            if nfinite >= thresh:
                continue
            out = _rolling_recompute(calc, fid, df, sym, tf, 6)
            print(f"    rolling OK, finite={int(np.isfinite(out).sum())}")
        except Exception as e:
            print(f"{fid}: EXC {type(e).__name__}: {str(e)[:120]}")
            traceback.print_exc(limit=4)
