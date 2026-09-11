import os, sys, traceback
from pathlib import Path
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena"); sys.path.insert(0, str(ROOT)); os.chdir(ROOT)

# 追踪：谁在什么时候调用了 validate_strict / find_unknown_flags
import backend.config.env_registry as reg
_orig_v = reg.validate_strict
def _traced(*a, **k):
    print("[TRACE] validate_strict 被调用；调用栈:", file=sys.stderr)
    for fr in traceback.extract_stack()[:-1][-4:]:
        print(f"        {fr.filename.replace(str(ROOT),'.')}:{fr.lineno} {fr.name}", file=sys.stderr)
    print("[TRACE] 此刻 .env 是否已加载:", "MIDLONG_PORTFOLIO_GATE_ENABLED" in os.environ, file=sys.stderr)
    return _orig_v(*a, **k)
reg.validate_strict = _traced
import backend.main  # noqa  —— 触发真实启动序（不启动服务，仅 import 阶段）
print("import backend.main 完成")
print("最终 unknown:", len(reg.find_unknown_flags()))
