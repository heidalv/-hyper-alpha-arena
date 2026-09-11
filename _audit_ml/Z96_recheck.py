import json, sys
from pathlib import Path
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena"); sys.path.insert(0, str(ROOT/"backend"/"scripts"))
import audit_config_effective as ace
rep = ace.build_report(ROOT)
f = rep["findings"]
print("truly_dead =", len(f["env_truly_dead"]))
print("dynamic    =", len(f["env_dynamic_prefix_read"]))
print("dead 列表:", ", ".join(f["env_truly_dead"]))
print()
print("dynamic 前缀数:", len(set(f["env_dynamic_prefix_read"].values())))
for k, v in sorted(f["env_dynamic_prefix_read"].items())[:40]:
    print(f"   {k:<44} ← {v}*")
