# -*- coding: utf-8 -*-
"""重开中线因子候选并跑 registry 扫描（与每日调度同入口，修复后首扫）。"""
import os, sys, time, warnings
warnings.filterwarnings("ignore")
os.chdir(os.path.dirname(os.path.abspath(__file__)) + "/..")
sys.path.insert(0, ".")
sys.path.append("backend")

from backend.services.factor_engine.alpha101_factors import seed_alpha101
from backend.services.factor_engine.midlong_registry_factors import (
    seed_registry_candidates, scan_registry_midlong,
)

t0 = time.time()
print("== seed alpha101 (reopen) ==", flush=True)
print(seed_alpha101(["4h", "1d"], reopen=True), flush=True)
print("== seed registry (reopen rejected) ==", flush=True)
print(seed_registry_candidates(["4h", "1d"]), flush=True)
print("== scan registry midlong ==", flush=True)
res = scan_registry_midlong(limit=200)
print("scored=%s promoted=%s" % (res.get("scored"), res.get("promoted")), flush=True)
for r in res.get("results", []):
    print(f"  {r['factor_id']:<34} {r['timeframe']:<4} grade={r['grade']} "
          f"ic={r['ic']} oos_sharpe={r['oos_sharpe']}", flush=True)
print("elapsed %.1fs" % (time.time() - t0), flush=True)
