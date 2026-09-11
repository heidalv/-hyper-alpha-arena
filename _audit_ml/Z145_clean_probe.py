import os, sys, subprocess
from pathlib import Path
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
# 干净子进程：只导入 env_registry，然后打印环境里是否已有 .env 变量
code = """
import os, sys
sys.path.insert(0, r'{root}')
from backend.config.env_registry import find_unknown_flags
print('MIDLONG_PORTFOLIO_GATE_ENABLED in env:', 'MIDLONG_PORTFOLIO_GATE_ENABLED' in os.environ)
print('unknown:', len(find_unknown_flags()))
""".format(root=str(ROOT))
r = subprocess.run([str(ROOT/'.venv/Scripts/python.exe'), '-c', code], capture_output=True, text=True, cwd=str(ROOT))
print("stdout:", r.stdout.strip())
print("stderr:", (r.stderr or '').strip()[:300])
