import sys
from collections import Counter
from pathlib import Path
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena"); sys.path.insert(0, str(ROOT/"backend"/"scripts"))
import audit_config_effective as ace
rep = ace.build_report(ROOT)
hs = rep["cfg_helpers"]
print("助手总数:", len(hs), " 危险:", rep["cfg_helpers_dangerous"])
print("分类:", dict(Counter(h.get("kind") for h in hs)))
print("env_first_benign 明细:")
for h in hs:
    if h.get("kind") == "env_first_benign":
        print("   ", h["file"], h["fn"])
print("falsy HIGH 站点:", rep["falsy_high_n"], " 其中安全键:", rep["falsy_high_safety_n"])
for s in rep["falsy_sites"]:
    if s["severity"] == "HIGH" and s["safety"]:
        print("   ⚠", s["file"], s["snippet"][:100])
