# -*- coding: utf-8 -*-
"""Z41：补丁后核验——每个被改模块必须能 import，且引用的 keep0 助手已定义。"""
from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MODULES = [
    "backend.services.mlto.open_gate",
    "backend.services.multi_venue_funding_collector",
    "backend.services.paper_fast_trial_controller",
    "backend.services.full_auto.midlong_helpers",
    "backend.services.agent_quant_feature_table",
    "backend.services.mlto.midlong_portfolio_risk",
    "backend.services.mlto.midlong_trade_design",
    "backend.services.full_auto.midlong_position_manager",
]

bad = 0
for m in MODULES:
    try:
        importlib.import_module(m)
        print(f"  ✓ import {m}")
    except Exception as e:
        bad += 1
        print(f"  ✗ import {m}: {type(e).__name__}: {e}")

print("\n=== 静态检查：使用了 _keep0_* 但未定义的文件 ===")
for p in (ROOT / "backend").rglob("*.py"):
    t = p.read_text(encoding="utf-8", errors="ignore")
    for fn in ("_keep0_int", "_keep0_float", "_cfg_int_keep0"):
        if re.search(rf"\b{fn}\(", t) and f"def {fn}(" not in t:
            print(f"  ✗ {p.relative_to(ROOT)} 使用 {fn} 但未定义")
            bad += 1

print(f"\n问题数 = {bad}")
raise SystemExit(1 if bad else 0)
