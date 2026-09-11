import os, sys
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv('.env', override=False)
from backend.services.mlto import decision_hub as dh
print("ai_governed_enabled():", dh.ai_governed_enabled())
print("ai_governed_weight():", dh.ai_governed_weight())
print("_LLM_WEIGHT_MID:", dh._LLM_WEIGHT_MID, "_LLM_WEIGHT_LONG:", dh._LLM_WEIGHT_LONG)
print("env MLTO_AI_GOVERNED =", os.getenv("MLTO_AI_GOVERNED"))
print("env AI_GOVERNED_WEIGHT_CONFIRMED =", os.getenv("AI_GOVERNED_WEIGHT_CONFIRMED"))
print("env MLTO_AI_GOVERNED_WEIGHT =", os.getenv("MLTO_AI_GOVERNED_WEIGHT"))
