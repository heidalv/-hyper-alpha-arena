"""弹药扩源 v2 冒烟：扩展 alpha101 库后重评，统计新增晋升。"""
import os, sys, time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
sys.path.append(os.path.dirname(os.path.abspath(__file__)) + "/../backend")
os.chdir(os.path.dirname(os.path.abspath(__file__)) + "/..")

t0 = time.time()
from backend.services.factor_engine.alpha101_factors import seed_alpha101, validate_alpha101

seed = seed_alpha101(["4h", "1d"], reopen=True)
print("① seed:", {k: seed[k] for k in ("registered", "skipped", "reopened")})
val = validate_alpha101(limit=200)
print("② validate: scored=%d promoted=%d" % (val["scored"], val["promoted"]))
from collections import Counter
grades = Counter(r["grade"] for r in val.get("results", []))
print("   评分分布:", dict(grades))
for r in val.get("results", []):
    if r.get("admitted") or (r.get("ic") is not None and abs(r["ic"]) >= 0.05):
        print("   ★", r["factor_id"], "| grade:", r.get("grade"), "| ic:", r.get("ic"),
              "| sharpe:", r.get("oos_sharpe"), "| reason:", str(r.get("reason"))[:90])
print(f"elapsed={time.time()-t0:.1f}s")
