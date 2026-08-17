"""弹药管道冒烟：重开 alpha101 + 登记 registry 因子 + 真实 4h/1d 扫描打分。"""
import os, sys, time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
sys.path.append(os.path.dirname(os.path.abspath(__file__)) + "/../backend")
os.chdir(os.path.dirname(os.path.abspath(__file__)) + "/..")

t0 = time.time()
from backend.services.factor_engine.alpha101_factors import seed_alpha101, validate_alpha101
from backend.services.factor_engine.midlong_registry_factors import (
    seed_registry_candidates, scan_registry_midlong,
)

seed = seed_alpha101(["4h", "1d"], reopen=True)
print("① alpha101 seed(reopen):", {k: seed[k] for k in ("registered", "skipped", "reopened")})
val = validate_alpha101(limit=80)
print("② alpha101 validate: scored=%d promoted=%d" % (val["scored"], val["promoted"]))
for r in val.get("results", [])[:8]:
    print("   -", r["factor_id"], "| grade:", r.get("grade"), "| ic:", r.get("ic"),
          "| sharpe:", r.get("oos_sharpe"))

rseed = seed_registry_candidates(["4h", "1d"])
print("③ registry seed: %d 因子登记 %d 条" % (len(rseed["factor_ids"]), rseed["registered"]))
rscan = scan_registry_midlong(limit=200)
print("④ registry scan: scored=%d promoted=%d" % (rscan["scored"], rscan["promoted"]))
from collections import Counter
grades = Counter(r["grade"] for r in rscan["results"])
print("   评分分布:", dict(grades))
for r in sorted(rscan["results"], key=lambda x: -abs(x.get("ic") or 0))[:8]:
    print("   -", r["factor_id"], "| tf:", r.get("timeframe"), "| grade:", r.get("grade"),
          "| ic:", r.get("ic"), "| sharpe:", r.get("oos_sharpe"))
print(f"elapsed={time.time()-t0:.1f}s")
