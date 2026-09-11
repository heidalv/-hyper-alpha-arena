import sys
from pathlib import Path
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT/"backend"/"scripts"))
from dotenv import load_dotenv; load_dotenv(ROOT/".env")
from backend.config.env_registry import find_unknown_flags
import audit_config_effective as ace
f = ace.build_report(ROOT)["findings"]
dead = set(f["env_truly_dead"]); unk = set(find_unknown_flags())
print("audit 死键:", len(dead), "| registry 未登记:", len(unk))
print("registry 未登记 ⊄ 死键 的差集:", sorted(unk - dead) or "空（完全一致）")
print("死键中未被 registry 报出的（非系统前缀，预期 3 个）:", sorted(dead - unk))
