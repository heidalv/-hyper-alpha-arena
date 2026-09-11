# -*- coding: utf-8 -*-
"""[§82 恢复 2026-09-11] 把 §62.1 统计行改成与校验器**实测分布**一致，并列出两个"部分"行。"""
from __future__ import annotations

import importlib.util
import re
import shutil
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
P = ROOT / "_中长线负期望根因报告_20260909.md"

spec = importlib.util.spec_from_file_location(
    "adi", ROOT / "backend" / "scripts" / "audit_defect_inventory.py")
adi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adi)

import argparse  # noqa: E402
ns = argparse.Namespace(report=str(P), json_out=str(ROOT / "data" / "audit_defect_inventory.json"))
data = adi.parse_report(str(P))
rows = data["rows"]
from collections import Counter  # noqa: E402
sev = Counter(r.get("severity") for r in rows)
st = Counter(adi._status_bucket(r.get("status") or "") for r in rows)
print("实测:", len(rows), dict(sev), dict(st))
print("部分行:", [(r["id"], (r.get("status") or "")[:60]) for r in rows
                if adi._status_bucket(r.get("status") or "") == "部分"])

text = P.read_text(encoding="utf-8")
old = re.search(r"条目 \*\*\d+\*\* 条（已合并 §13/§21、§22/§35 两组同源项）.*?\n状态：.*?\n", text, re.S)
if not old:
    print("未找到统计行，跳过改写")
    raise SystemExit(1)
new = ("条目 **%d** 条（已合并 §13/§21、§22/§35 两组同源项）｜🔴 高 **%d**｜🟠 中 **%d**｜"
       "🟡 低 **%d**｜⚪ 记录 **%d**\n"
       "状态：✅ 已修 **%d**｜🟡 部分/可见性已修 **%d**｜📋 待决策 **%d**｜⏳ 待办 **%d**｜"
       "❌ 撤销 **%d**\n") % (
    len(rows), sev.get("高", 0), sev.get("中", 0), sev.get("低", 0), sev.get("记录", 0),
    st.get("已修", 0), st.get("部分", 0), st.get("待决策", 0), st.get("待办", 0), st.get("撤销", 0),
)
bak = P.with_name(P.name + f".pre_stats_{time.strftime('%Y%m%d_%H%M%S')}")
shutil.copy2(P, bak)
P.write_text(text[:old.start()] + new + text[old.end():], encoding="utf-8")
print("统计行已改写（备份 %s）:" % bak.name)
print(new)
