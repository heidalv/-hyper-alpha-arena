import os, sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
sys.path.append(os.path.dirname(os.path.abspath(__file__)) + "/../backend")
os.chdir(os.path.dirname(os.path.abspath(__file__)) + "/..")

import logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler("logs/_coldpool_scan.log", encoding="utf-8"),
    ],
)

from backend.services.factor_engine.midlong_cold_pool import scan_cold_pool_midlong

_max_files = int(os.environ.get("COLDPOOL_MAX_FILES", "400"))
t0 = time.time()
r = scan_cold_pool_midlong(promote=False, max_files=_max_files)
print("RESULT scanned=%d loaded=%d passers=%d timeouts=%d elapsed=%ss" % (
    r.get("scanned", 0), r.get("loaded", 0), r.get("passers", 0),
    r.get("timeouts", 0), r.get("elapsed_sec", 0)))
print("TOP-IC:")
for row in r.get("top_by_ic", [])[:10]:
    print("  -", row["factor_id"], "| tf:", row["timeframe"], "| ic:", row["ic_mean"],
          "| sharpe:", row["oos_sharpe"], "| grade:", row["grade"])
if r.get("passers"):
    print("PASSERS:", [(p["factor_id"], p["timeframe"], p["grade"], p["oos_sharpe"]) for p in r["passer_details"]])
