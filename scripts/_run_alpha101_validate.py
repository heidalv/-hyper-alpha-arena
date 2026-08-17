# -*- coding: utf-8 -*-
"""[2026-08-15] 独立进程 alpha101 重开验证（与后端重启解耦，避免 job 随进程丢失）。"""
import os, sys, time, warnings
warnings.filterwarnings("ignore")
os.chdir(os.path.dirname(os.path.abspath(__file__)) + "/..")
sys.path.insert(0, ".")
sys.path.append("backend")

from backend.services.factor_engine.alpha101_factors import seed_alpha101, validate_alpha101

t0 = time.time()
print("== seed (reopen) ==", flush=True)
print(seed_alpha101(["4h", "1d"], reopen=True), flush=True)
print("== validate ==", flush=True)
res = validate_alpha101(limit=80)
print("validated=%s promoted=%s" % (res.get("validated"), res.get("promoted")), flush=True)
for r in res.get("results", []):
    print(f"  {r['factor_id']:<30} grade={r.get('grade')} admitted={r.get('admitted')} "
          f"ic={r.get('ic')} sharpe={r.get('oos_sharpe')}", flush=True)
print("elapsed %.1fs" % (time.time() - t0), flush=True)
