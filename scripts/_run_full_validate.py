# -*- coding: utf-8 -*-
"""lookback=2400 全量验证：alpha101 重开打分 + registry 扫描（修复后 + 深样本）。"""
import os, sys, time, warnings
warnings.filterwarnings("ignore")
os.chdir(os.path.dirname(os.path.abspath(__file__)) + "/..")
sys.path.insert(0, ".")
sys.path.append("backend")

from backend.services.factor_engine.alpha101_factors import seed_alpha101
from backend.services.factor_engine.midlong_registry_factors import (
    seed_registry_candidates, scan_registry_midlong,
)
from backend.services.factor_engine.factor_jobs import (
    factor_job_manager, run_validate_alpha101,
)

t0 = time.time()
print("== seed ==", flush=True)
print(seed_alpha101(["4h", "1d"], reopen=True), flush=True)
print(seed_registry_candidates(["4h", "1d"]), flush=True)

print("== alpha101 validate (job) ==", flush=True)
job = run_validate_alpha101(limit=80)
while True:
    j = factor_job_manager.get(job.id)
    if j and j.get("status") in ("done", "error"):
        break
    time.sleep(5)
print("alpha101 job:", j.get("status"), "scored=%s promoted=%s" % (
    (j.get("result") or {}).get("scored"), (j.get("result") or {}).get("promoted")), flush=True)
for r in ((j.get("result") or {}).get("results") or [])[:40]:
    print(f"  {r['factor_id']:<30} grade={r['grade']} ic={r['ic']} oos_sharpe={r['oos_sharpe']}", flush=True)

print("== registry scan ==", flush=True)
res = scan_registry_midlong(limit=200)
print("scored=%s promoted=%s" % (res.get("scored"), res.get("promoted")), flush=True)
for r in res.get("results", []):
    print(f"  {r['factor_id']:<34} {r['timeframe']:<4} grade={r['grade']} "
          f"ic={r['ic']} oos_sharpe={r['oos_sharpe']}", flush=True)
print("elapsed %.1fs" % (time.time() - t0), flush=True)
