# -*- coding: utf-8 -*-
"""Z69: 直接读「审计通用拒仓」行的前文，定位真实原因（按文件就地取证）。"""
from __future__ import annotations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
targets = ["backend.pid20848.log", "backend.pid33412.log", "backend.pid10964.log", "backend.pid8112.log"]
for name in targets:
    p = ROOT / "logs" / name
    if not p.exists():
        continue
    try:
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception as e:
        print(name, "读取失败", e)
        continue
    idx = [i for i, l in enumerate(lines) if "evaluate_and_execute_returned_false" in l]
    print(f"\n{'='*100}\n{name}: 总行 {len(lines)}，通用拒仓行 {len(idx)}")
    for i in idx[:3]:
        print(f"--- 第 {i+1} 行上下文 ---")
        for j in range(max(0, i - 8), min(len(lines), i + 2)):
            mark = ">>" if j == i else "  "
            print(f" {mark} {lines[j][:210]}")
