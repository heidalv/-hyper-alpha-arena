# -*- coding: utf-8 -*-
"""[§84] 直接在 INVENTORY-STATS 标记之间写入实测统计（按标记定位，不靠受损文本匹配）。"""
from __future__ import annotations

import importlib.util
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
P = ROOT / "_中长线负期望根因报告_20260909.md"

spec = importlib.util.spec_from_file_location(
    "adi", ROOT / "backend" / "scripts" / "audit_defect_inventory.py")
adi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adi)
rows = adi.parse_report(P)["rows"]
sev = Counter(r.get("severity") for r in rows)
st = Counter(adi._status_bucket(r.get("status") or "") for r in rows)
print("实测:", len(rows), dict(sev), dict(st))

lines = P.read_text(encoding="utf-8").splitlines()
start = lines.index("<!-- INVENTORY-STATS -->")
end = lines.index("<!-- /INVENTORY-STATS -->")
new = [
    "",
    ("条目 **%d** 条（已合并 §13/§21、§22/§35 两组同源项）｜🔴 高 **%d**｜🟠 中 **%d**｜"
     "🟡 低 **%d**｜⚪ 记录 **%d**" % (len(rows), sev.get("高", 0), sev.get("中", 0),
                                      sev.get("低", 0), sev.get("记录", 0))),
    ("状态：✅ 已修 **%d**｜🟡 部分/可见性已修 **%d**｜📋 待决策 **%d**｜⏳ 待办 **%d**｜"
     "❌ 撤销 **%d**" % (st.get("已修", 0), st.get("部分", 0), st.get("待决策", 0),
                        st.get("待办", 0), st.get("撤销", 0))),
    "",
]
lines[start + 1:end] = new
P.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("已按标记重写统计块")
