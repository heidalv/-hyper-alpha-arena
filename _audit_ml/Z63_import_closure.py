# -*- coding: utf-8 -*-
"""Z63: 证明 test_executors 两处失败与本轮改动无关（导入闭包论证）。"""
from __future__ import annotations
import ast, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

TARGETS = {"backend.services.trend_e1_engine", "backend.services.trend_e1_f4_gate",
           "backend.services.market_maker.core", "backend.services.market_maker"}

def imports_of(path: Path):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return []
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.module:
            out.append(n.module)
    return out

seen, stack, closure = set(), ["backend.services.exchange.executors",
                              "backend.services.exchange.live_executor",
                              "backend.services.exchange.paper_executor",
                              "backend.tests.test_executors"], set()
while stack:
    mod = stack.pop()
    if mod in seen:
        continue
    seen.add(mod)
    closure.add(mod)
    p = ROOT / (mod.replace(".", "/") + ".py")
    if not p.exists():
        p = ROOT / mod.replace(".", "/") / "__init__.py"
    if not p.exists():
        continue
    for imp in imports_of(p):
        base = imp.split(".")[0:3]
        cands = [imp] + [".".join(base[:i]) for i in (3, 2, 1)]
        for c in cands:
            if c.startswith("backend") and c not in seen:
                stack.append(c)
print("闭包模块数:", len(closure))
hit = sorted(t for t in TARGETS if t in closure)
print("闭包内是否含本轮被改模块:", hit or "否 —— 无任何交集")
print()
print("live_executor 的杠杆对齐硬闸位置:")
p = ROOT / "backend/services/exchange/live_executor.py"
for i, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
    if "leverage_align_failed" in line or "杠杆未确认对齐" in line:
        print(f"  {i}: {line.strip()[:110]}")
