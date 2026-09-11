# -*- coding: utf-8 -*-
"""Z62: §51 修复的端到端核验（真实 env，两种 live 标志）。"""
from __future__ import annotations
import os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
import backend.services.trend_e1_engine as E

print("=== A. 语法/导入 ===")
print("  E1_LIVE_ROUTING_IMPLEMENTED =", E.E1_LIVE_ROUTING_IMPLEMENTED)
print("  _pre_exec_live_gate 存在    =", callable(E._pre_exec_live_gate))
gate_path = ROOT / "backend" / "data" / "trend_e1" / "f4_gate_latest.json"

print()
print("=== B. 生产配置（TREND_E1_LIVE_ASTER=false）：不评估门禁 ===")
before = gate_path.stat().st_mtime if gate_path.exists() else None
os.environ["TREND_E1_LIVE_ASTER"] = "false"
r = E._pre_exec_live_gate()
after = gate_path.stat().st_mtime if gate_path.exists() else None
print("  返回 =", r)
print("  f4_gate_latest.json 是否被改写 =", before != after, "(应 False：零副作用)")

print()
print("=== C. live 意图打开（TREND_E1_LIVE_ASTER=true）：门禁前移 + 拒绝 ===")
os.environ["TREND_E1_LIVE_ASTER"] = "true"
r2 = E._pre_exec_live_gate()
print("  live_requested =", r2["live_requested"], " allowed =", r2["allowed"],
      " blocked =", r2["blocked"], " reason =", r2["reason"])
g = r2.get("gate") or {}
print("  gate.passed =", g.get("passed"), " 失败项 =",
      [c.get("name") for c in (g.get("checks") or []) if not c.get("ok")])
print("  真实落盘 =", gate_path.exists(),
      (gate_path.stat().st_size if gate_path.exists() else 0), "bytes")
os.environ["TREND_E1_LIVE_ASTER"] = "false"

print()
print("=== D. 三准则①：assert_live_allowed 调用点 ===")
import inspect
src = inspect.getsource(E)
print("  trend_e1_engine 内引用处数 =", src.count("assert_live_allowed("))
print("  scheduled_job 先门禁后执行 =", src.index("_pre_exec_live_gate()") < src.index("out = run_daily()"))
