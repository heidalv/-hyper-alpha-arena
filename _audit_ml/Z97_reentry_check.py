import re, sys
from pathlib import Path
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
print("=== REENTRY_COOLDOWN 相关读取 ===")
for p in (ROOT/"backend").rglob("*.py"):
    if ".venv" in str(p): continue
    try: t = p.read_text(encoding="utf-8", errors="ignore")
    except Exception: continue
    for i, line in enumerate(t.splitlines(), 1):
        if "REENTRY_COOLDOWN" in line:
            print(f"  {p.relative_to(ROOT)}:{i}  {line.strip()[:140]}")
print()
import os
sys.path.insert(0, str(ROOT/"backend"/"scripts"))
import audit_config_effective as ace
rep = ace.build_report(ROOT); f = rep["findings"]
mn = [k for k in f["settings_not_in_registry"] if "MIDLONG" in k or "TIER_" in k or "EXIT" in k or "LIVE" in k or "GATE" in k]
print(f"=== settings 未登记 env_registry: 共 {len(f['settings_not_in_registry'])}；其中 mid/long/闸门相关 {len(mn)} ===")
print("  ", ", ".join(mn))
print()
print(f"=== near_miss 现况 {len(f['near_miss'])} ===")
for nm in f["near_miss"]:
    print(f"   {nm['env_key']:<40} ≈ {nm['candidate']:<40} {nm['score']}")
