import sys
from pathlib import Path
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena"); sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv; load_dotenv(ROOT/".env")
from backend.config.env_registry import find_unknown_flags
sys.path.insert(0, str(ROOT/"backend"/"scripts"))
import audit_config_effective as ace
f = ace.build_report(ROOT)["findings"]
dead = set(f["env_truly_dead"])
u = set(find_unknown_flags())
inter = sorted(u & dead)
print(f"未登记 {len(u)} 个；其中 §54 判定的真死键 {len(inter)} 个（两套工具交叉印证）:")
for k in inter: print("   ", k)
