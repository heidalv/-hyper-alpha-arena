# -*- coding: utf-8 -*-
"""Z42：逐个打印 6 处 fail-open 闸的实际代码上下文，判定「有意保护 vs 静默放行」。"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TARGETS = [
    ("backend/services/full_auto/midlong_chart_gate.py", "chart_gate_check"),
    ("backend/services/factor_engine/midlong_flow_gate.py", "mid_flow_consistency_gate"),
    ("backend/services/full_auto/paper_risk_helpers.py", "tiny_close_allowed_by_hardfact"),
    ("backend/services/auto_coin_selector.py", "rotation_remove_allowed"),
    ("backend/services/llm_config_service.py", "_forbid_shared_platform_llm"),
    ("backend/services/scalp/scalp_ranging_mr.py", "_trend_veto_enabled"),
]

for rel, fn in TARGETS:
    p = ROOT / rel
    if not p.is_file():
        print(f"--- {rel}: 文件不存在")
        continue
    text = p.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    print(f"\n===== {rel} :: {fn} =====")
    # 打印整个函数体（到下一个顶层 def）
    start = None
    for i, l in enumerate(lines):
        if re.match(rf"^def {re.escape(fn)}\s*\(", l):
            start = i
            break
    if start is None:
        print("  未找到 def")
        continue
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if re.match(r"^(def |class )", lines[j]):
            end = j
            break
    for k in range(start, min(end, start + 70)):
        mark = "  <<<" if re.search(r"except[^:]*:", lines[k]) or re.search(r"return\s+True", lines[k]) else ""
        print(f"{k+1:5} {lines[k][:118]}{mark}")
