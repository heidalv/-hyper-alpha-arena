import os, sys
from pathlib import Path
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena"); sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
try:
    from backend.config.framework_rollout import apply_aggressive_rollout
    apply_aggressive_rollout()
except Exception as e:
    print("  rollout 失败:", str(e)[:80])
print("  早期阶段 MIDLONG_PORTFOLIO_GATE_ENABLED 在 os.environ 里?", "MIDLONG_PORTFOLIO_GATE_ENABLED" in os.environ)
from backend.config.env_registry import find_unknown_flags
print("  此时 find_unknown_flags() 命中:", len(find_unknown_flags()))
