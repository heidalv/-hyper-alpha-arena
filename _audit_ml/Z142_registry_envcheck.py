import sys
from pathlib import Path
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena"); sys.path.insert(0, str(ROOT/"backend"/"scripts"))
import audit_config_effective as ace
env = ace.parse_env((ROOT/".env").read_text(encoding="utf-8", errors="ignore"))
f = ace.build_report(ROOT)["findings"]
ks = f["settings_not_in_registry"]
in_env = [k for k in ks if k in env]
print(f"未登记 {len(ks)} 个；其中 **在 .env 里有值** 的 {len(in_env)} 个（这些会在启动时被 validate_strict 报为未知 flag）：")
for k in in_env:
    print(f"   {k:<40} = {str(env[k])[:40]}")
print("\n其余（settings 有定义但 .env 未设）:")
for k in ks:
    if k not in in_env:
        print("   ", k)
