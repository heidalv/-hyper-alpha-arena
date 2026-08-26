import sys, os
sys.path.insert(0, os.path.abspath("."))
import logging, json, traceback
logging.getLogger("backend").setLevel(logging.ERROR)
from backend.services.evolution.repromote_quarantine import repromote_quarantine_factors
from backend.services.evolution.factor_evolution_loop import _min_net_ic_threshold
print("threshold:", _min_net_ic_threshold(), flush=True)
r = repromote_quarantine_factors(period="4h", limit=40)
json.dump(r, open("data/_repromote_result.json", "w", encoding="utf-8"), ensure_ascii=False, default=str)
print("DONE scanned=", r.get("scanned"), "promoted=", len(r.get("promoted") or []), flush=True)
