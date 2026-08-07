"""Hermes L3 流式调用测试 — 重启后验证。"""
from __future__ import annotations

import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

from backend.services.hermes_db import init_hermes_db
from backend.services.hermes_architecture_evolution_engine import architecture_evolution

init_hermes_db()
print("=== L3 流式测试开始 ===", flush=True)
t0 = time.time()
result = architecture_evolution.discover_architecture_gaps()
elapsed = round(time.time() - t0, 1)
summary = {
    "elapsed_s": elapsed,
    "proposals_count": len(result.get("proposals") or []),
    "priority": result.get("priority"),
    "error": result.get("error"),
    "sample_titles": [p.get("title") for p in (result.get("proposals") or [])[:3]],
}
print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
print("=== 测试结束 ===", flush=True)
