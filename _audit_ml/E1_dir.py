import os, sys
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv('.env', override=False)
# 语法与导入检查
import backend.services.mlto.hub_decision_log as hdl
import backend.services.mlto.orchestrator as orch
import backend.services.mlto.decision_hub as dh
print("imports OK")
print("require_fw_agree:", dh._llm_direction_requires_framework_agree())
# 构造信号验证方向闸
from backend.services.mlto.types import Signal
sig_llm_short = [Signal("llm_qual", 0.30, 0.85, "llm"), Signal("orch_mid_bias", 1.0, 0.5, "framework")]
print("LLM 想 short + 框架看多 ->", dh._derive_direction(sig_llm_short, 0.5, ai_governed=True))
sig_llm_short_agree = [Signal("llm_qual", 0.30, 0.85, "llm"), Signal("orch_mid_bias", 0.0, 0.5, "framework")]
print("LLM 想 short + 框架同向 ->", dh._derive_direction(sig_llm_short_agree, 0.5, ai_governed=True))
sig_llm_long_against = [Signal("llm_qual", 0.80, 0.85, "llm"), Signal("orch_mid_bias", 0.0, 0.5, "framework")]
print("LLM 想 long + 框架看空 ->", dh._derive_direction(sig_llm_long_against, 0.5, ai_governed=True))
