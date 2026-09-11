import os, sys
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv('.env', override=False)
from backend.services.mlto import decision_hub as dh
from backend.services.full_auto.midlong_circuit_gate import _short_mode
from backend.services.exit.exit_policy import ExitPolicy
print("=== 生产配置生效检查（需重启后由运行进程加载）===")
print("  LLM 方向需框架同意:", dh._llm_direction_requires_framework_agree())
print("  ai_governed 模式:", dh.ai_governed_enabled(), "weight:", dh.ai_governed_weight())
print("  WEIGHTS_MID.llm_qual:", dh.WEIGHTS_MID["llm_qual"], " WEIGHTS_LONG.llm_qual:", dh.WEIGHTS_LONG["llm_qual"])
print("  mid 空头模式:", _short_mode())
p = ExitPolicy.for_lane("mid").to_dict()
print("  mid 出场策略:", {k: p[k] for k in ("sl_pct","tp_pct","time_limit_sec","trailing_activation_pct","trailing_callback_pct","min_roi")})
import json
d=json.load(open('data/runtime_tuning.json',encoding='utf-8'))
print("  tier_max_hold_sec:", d.get('tier_max_hold_sec'))
