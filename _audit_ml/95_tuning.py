import os, sys, json
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv('.env', override=False)
from backend.services import runtime_tuning_store as rts
print("before:", json.dumps(rts.get_tuning("tier_max_hold_sec"), ensure_ascii=False))
res = rts.apply_patches({"tier_max_hold_sec": {"mid": 604800}}, proposal_id=None)
print("apply result:", json.dumps(res, ensure_ascii=False)[:400] if isinstance(res,(dict,list)) else res)
rts.invalidate_cache()
print("after:", json.dumps(rts.get_tuning("tier_max_hold_sec"), ensure_ascii=False))
