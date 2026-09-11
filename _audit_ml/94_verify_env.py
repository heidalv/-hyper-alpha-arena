import os, sys
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv('.env', override=False)
# 验证 .env 新值能被读取
from backend.config import settings as S
print("MIDLONG_SHORT_MODE(env) =", os.getenv("MIDLONG_SHORT_MODE"))
print("MIDLONG_LOCATION_GATE_ENABLED =", os.getenv("MIDLONG_LOCATION_GATE_ENABLED"))
print("MLTO_LLM_WEIGHT_MID =", os.getenv("MLTO_LLM_WEIGHT_MID"))
print("AUTO_COIN_MAX_HOLD_HOURS_MID =", os.getenv("AUTO_COIN_MAX_HOLD_HOURS_MID"))
print("TIER_MID_MAX_HOLD_SEC(settings) =", getattr(S, "TIER_MID_MAX_HOLD_SEC", None))
from backend.services.exit.exit_policy import ExitPolicy
p = ExitPolicy.for_lane("mid")
print("ExitPolicy mid:", p.to_dict())
from backend.services.full_auto.midlong_circuit_gate import _short_mode
print("short mode =", _short_mode())
from backend.services.mlto.decision_hub import WEIGHTS_MID, WEIGHTS_LONG
print("WEIGHTS_MID llm_qual =", WEIGHTS_MID.get("llm_qual"), " WEIGHTS_LONG llm_qual =", WEIGHTS_LONG.get("llm_qual"))
