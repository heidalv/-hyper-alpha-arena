import os, sys
from pathlib import Path
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena"); sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv; load_dotenv(ROOT/".env")
from backend.config.env_registry import find_unknown_flags, validate_strict
unk = find_unknown_flags()
print(f"find_unknown_flags() 命中 {len(unk)} 个（匹配系统前缀但未登记）：")
for k in sorted(unk):
    print("   ", k)
print("\nvalidate_strict() 输出（默认 warn）：")
try:
    validate_strict()
except SystemExit as e:
    print("  硬失败:", str(e)[:200])
