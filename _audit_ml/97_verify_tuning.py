import os, sys, json
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv('.env', override=False)
from backend.services import runtime_tuning_store as rts
rts.invalidate_cache()
print(json.dumps(rts.get_tuning("tier_max_hold_sec"), ensure_ascii=False))
