import sys
from pathlib import Path
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena"); sys.path.insert(0, str(ROOT/"backend"/"scripts"))
import audit_config_effective as ace
rep = ace.build_report(ROOT)
hs = rep["cfg_helpers"]
dang = [h for h in hs if h["dangerous"]]
benign = [h for h in hs if h.get("kind") == "getenv_or_benign"]
print(f"助手总数 {len(hs)}；真危险(getattr 左值) {len(dang)}；形状无害(getenv 左值) {len(benign)}")
print("\n=== 真危险明细 ===")
for h in dang:
    print(f"  {h['file']:<58} {h['fn']:<12} {h['reason']}")
    print(f"      {h['body'][:120]}")
print("\n=== 形状无害（前 12）===")
for h in benign[:12]:
    print(f"  {h['file']:<58} {h['fn']}")
