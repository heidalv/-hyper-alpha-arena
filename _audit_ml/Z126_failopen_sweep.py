# -*- coding: utf-8 -*-
"""Z126: 中长线链路「静默 fail-open」残留普查（§41.2→§51.7→本轮 的收口）。

判据：`logger.debug(...)` 且同行含 fail-open / 放行 / 跳过 / skip / 不否决 / 不拦 等语义，
且所在模块属于中长线**入口或出场**链路。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULES = [
    "backend/services/full_auto/midlong_helpers.py",
    "backend/services/full_auto/midlong_executor.py",
    "backend/services/full_auto/midlong_circuit_gate.py",
    "backend/services/full_auto/midlong_chart_gate.py",
    "backend/services/full_auto/midlong_location_gate.py",
    "backend/services/full_auto/midlong_position_manager.py",
    "backend/services/full_auto/proposal_execution.py",
    "backend/services/full_auto/paper_execution.py",
    "backend/services/full_auto/mlto_cycle.py",
    "backend/services/full_auto/master_execution.py",
    "backend/services/decision_core/pipeline.py",
    "backend/services/decision_core/midlong_ev_gate.py",
    "backend/services/decision_core/midlong_mtf_constraint.py",
    "backend/services/factor_engine/midlong_flow_gate.py",
    "backend/services/factor_engine/midlong_factor_route.py",
    "backend/services/risk_constitution.py",
    "backend/services/mlto/brain.py",
    "backend/services/mlto/decision_hub.py",
    "backend/services/mlto/midlong_portfolio_risk.py",
    "backend/services/unified_exit_executor.py",
    "backend/services/risk_band_resolver.py",
    "backend/services/midlong_scan_gate.py",
]
SEM = re.compile(r"(fail-open|fail_open|放行|不否决|不拦|跳过|skip)", re.I)
DEBUG = re.compile(r"logger\.debug\(")

hits = []
for rel in MODULES:
    p = ROOT / rel
    if not p.exists():
        continue
    for i, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
        if DEBUG.search(line) and SEM.search(line):
            hits.append((rel, i, line.strip()[:150]))

print(f"候选（debug 级 + fail-open 语义）共 {len(hits)} 处：")
by_mod = {}
for rel, i, s in hits:
    by_mod.setdefault(rel, []).append((i, s))
for rel, items in by_mod.items():
    print(f"\n=== {rel}  ({len(items)}) ===")
    for i, s in items:
        print(f"   {i}: {s}")
