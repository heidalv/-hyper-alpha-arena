import sys
from pathlib import Path
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena"); sys.path.insert(0, str(ROOT/"backend"/"scripts"))
import audit_config_effective as ace
f = ace.build_report(ROOT)["findings"]
suf = f["env_suffix_composed_read"]
print(f"命中 {len(suf)} 个键；后缀集合 {sorted(set(suf.values()))}")
for k, v in sorted(suf.items())[:20]:
    print(f"   {k:<44} ← *{v}")
