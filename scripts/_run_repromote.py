import json
import sys

sys.path.insert(0, ".")

from backend.services.evolution.repromote_quarantine import repromote_quarantine_factors

result = repromote_quarantine_factors(period="4h")
print(json.dumps(result, ensure_ascii=False, indent=1))
