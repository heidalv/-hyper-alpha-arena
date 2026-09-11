import sys
from pathlib import Path
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena"); sys.path.insert(0, str(ROOT/"backend"/"scripts"))
import audit_config_effective as ace
f = ace.build_report(ROOT)["findings"]
ks = f["settings_not_in_registry"]
print(f"settings 定义但未登记 env_registry: {len(ks)}")
for k in ks: print("  ", k)
