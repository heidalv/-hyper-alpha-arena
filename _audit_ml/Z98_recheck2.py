import sys
from pathlib import Path
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena"); sys.path.insert(0, str(ROOT/"backend"/"scripts"))
import audit_config_effective as ace
f = ace.build_report(ROOT)["findings"]
print("truly_dead =", len(f["env_truly_dead"]))
print("  ", ", ".join(f["env_truly_dead"]))
print("\nonly_in_tests =", len(f["env_used_only_in_tests"]), "->", ", ".join(f["env_used_only_in_tests"]))
print("\nnear_miss =", len(f["near_miss"]))
for nm in f["near_miss"]:
    print(f"   {nm['env_key']:<42} ≈ {nm['candidate']:<40} {nm['score']}")
print("\ndynamic =", len(f["env_dynamic_prefix_read"]))
