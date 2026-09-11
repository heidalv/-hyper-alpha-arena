import sys
from pathlib import Path
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena"); sys.path.insert(0, str(ROOT/"backend"/"scripts"))
import audit_config_effective as ace
rep = ace.build_report(ROOT)
for k in ("cfg_helpers_dangerous", "cfg_helpers_kinds", "falsy_high_n", "falsy_high_prod_n",
          "falsy_high_in_tests_n", "falsy_high_safety_n", "falsy_safety_n"):
    print(f"  {k} = {rep.get(k)}")
