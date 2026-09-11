import json
from pathlib import Path
p = Path("data/config_effective_audit.json")
d = json.loads(p.read_text(encoding="utf-8"))
print("顶层键:", list(d.keys()))
for k, v in d.items():
    if isinstance(v, list):
        print(f"  {k}: {len(v)} 项 样例={v[:3]}")
    elif isinstance(v, dict):
        print(f"  {k}: dict({len(v)}) 样例键={list(v)[:6]}")
    else:
        print(f"  {k} = {v}")
