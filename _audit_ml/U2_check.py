import os, sys, json
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv('.env', override=False)
from backend.services.full_auto.midlong_circuit_gate import _short_mode, _down_short_enabled, _chop_flat_enabled
print("=== 第十三轮配置 ===")
print("  short mode:", _short_mode())
print("  down_short_enabled:", _down_short_enabled(), "(默认 flat)")
print("  chop_flat:", _chop_flat_enabled(), "(默认 long_only)")
