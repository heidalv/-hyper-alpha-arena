# -*- coding: utf-8 -*-
"""离线验证 factor_route_decide：当前活跃因子对 BTC/ETH/SOL 的决策输出。"""
import os, sys, warnings, time
warnings.filterwarnings("ignore")
os.chdir(os.path.dirname(os.path.abspath(__file__)) + "/..")
sys.path.insert(0, ".")
sys.path.append("backend")

from backend.services.factor_engine.midlong_active_factor_set import midlong_active_factor_set
from backend.services.factor_engine.midlong_factor_route import factor_route_decide

active = midlong_active_factor_set.get_active_factors()
print("active factors:", [(r.get("factor_id"), r.get("grade")) for r in active])

ms = {sym: {"current_price": 100.0, "data_reliable": True} for sym in ("BTC", "ETH", "SOL")}
for sym in ("BTC", "ETH", "SOL"):
    t0 = time.time()
    d = factor_route_decide(sym, market_summary=ms)
    print(f"{sym} [{time.time()-t0:.1f}s] action={d['action']} score={d['score']} "
          f"conf={d['confidence']} reason={d['reason']}")
    print("   votes:", d.get("votes"))
