# -*- coding: utf-8 -*-
"""Z181：列出清单里仍未结案的条目（供收尾汇报）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
d = json.loads((ROOT / "data" / "audit_defect_inventory.json").read_text(encoding="utf-8"))
rows = d.get("rows") or d.get("items") or []
print("清单条目:", len(rows))
for r in rows:
    st = r.get("status", "")
    if "待决策" in st or "待办" in st:
        print(f"  #{r.get('id')} [{r.get('severity')}] {r.get('title', '')[:64]}")
        print(f"      状态: {st[:120]}")
print("\n汇总:", {k: v for k, v in d.items() if not isinstance(v, (list, dict))})
