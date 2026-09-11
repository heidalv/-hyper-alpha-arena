import sys
from pathlib import Path
sys.path.insert(0, str(Path(r"D:\001Alpha\Hyper-Alpha-Arena/backend/scripts")))
import audit_defect_inventory as inv
d = inv.parse_report()
from collections import Counter
print("severity:", dict(Counter(r["severity"] for r in d["rows"])))
print("status:", dict(Counter(inv._status_bucket(r["status"]) for r in d["rows"])))
print("\n按桶列出条目：")
by = {}
for r in d["rows"]:
    by.setdefault(inv._status_bucket(r["status"]), []).append(r["id"])
for k, v in by.items():
    print(f"  {k}: {sorted(v)}")
