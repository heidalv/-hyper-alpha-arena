# -*- coding: utf-8 -*-
"""诊断 2：用 mbcs(cp936) 反解后的**真实损坏点数量**（U+FFFD 计数与上下文样例）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
P = Path(r"D:\001Alpha\Hyper-Alpha-Arena\_中长线负期望根因报告_20260909.md")
text = P.read_bytes().decode("utf-8")
if text.startswith("\ufeff"):
    text = text[1:]
b = text.encode("mbcs")
print("反解字节数:", len(b))
try:
    ok = b.decode("utf-8")
    print("✅ 完全可逆")
except UnicodeDecodeError:
    pass
repaired = b.decode("utf-8", errors="replace")
n_bad = repaired.count("\ufffd")
print("U+FFFD 数量（= 不可逆字符数）:", n_bad)
# 定位损坏点上下文
idx = []
pos = 0
while True:
    pos = repaired.find("\ufffd", pos)
    if pos < 0:
        break
    idx.append(pos)
    pos += 1
print("前 12 个损坏点上下文：")
for i in idx[:12]:
    print("   ...", repaired[max(0, i - 45):i + 25].replace("\n", "⏎"), "...")
print("总行数:", repaired.count("\n") + 1)
rows = [l for l in repaired.splitlines() if l.startswith("| ") and l.count("|") >= 6]
print("疑似清单行数:", len(rows))
