# -*- coding: utf-8 -*-
"""验证 midlong_registry_factors 滚动重算路径：对快照型因子打分。"""
import os, sys, warnings, time
warnings.filterwarnings("ignore")
os.chdir(os.path.dirname(os.path.abspath(__file__)) + "/..")
sys.path.insert(0, ".")
sys.path.append("backend")

from backend.services.factor_engine.midlong_registry_factors import _score_one_registry_factor

for fid, tf in [("bb_width", "4h"), ("bb_width", "1d"), ("rsi", "4h"), ("zscore", "1d")]:
    t0 = time.time()
    r = _score_one_registry_factor(f"{fid}@{tf}", fid, tf)
    dt = time.time() - t0
    if r:
        print(f"[{fid}@{tf}] {dt:.1f}s grade={r.get('grade')} ic={r.get('ic_mean')} "
              f"icir={r.get('icir')} oos_sharpe={r.get('oos_sharpe')} "
              f"oos_net={r.get('oos_net_return')} trades={r.get('oos_trades')} "
              f"reason={r.get('reason')}")
    else:
        print(f"[{fid}@{tf}] None {dt:.1f}s")
