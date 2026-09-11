import json
from pathlib import Path
d = json.loads(Path("data/config_effective_audit.json").read_text(encoding="utf-8"))
f = d["findings"]
for k, v in f.items():
    n = len(v) if hasattr(v, "__len__") else v
    print(f"\n=== {k}（{n}）===")
    if isinstance(v, list):
        for item in v[:25]:
            print("   ", str(item)[:200])
    elif isinstance(v, dict):
        for kk, vv in list(v.items())[:25]:
            print(f"    {kk}: {str(vv)[:180]}")
