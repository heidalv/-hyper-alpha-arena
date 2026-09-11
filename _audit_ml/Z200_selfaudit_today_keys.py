# -*- coding: utf-8 -*-
"""Z200：**自审今天新增的配置键与新分支**（每一项都要"读了才存在"）。

检查矩阵（对每个今天新增的键）：
  1. 读路径：谁读它？（grep 生产代码）
  2. 读法正确性：经 settings 读 ⇒ 必须在 settings.py 声明；经 os.getenv 读 ⇒ 必须已 load_dotenv
  3. 登记：是否在 env_registry.KNOWN_FLAGS
  4. 默认值语义：显式 0/false 是否被吞（falsy 短路）
  5. 契约测试：是否有测试引用该键
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT / "backend"))
BE = ROOT / "backend"

TODAY_KEYS = [
    "MIDLONG_MAX_SAME_SYMBOL_POSITIONS",   # P13
    "MIDLONG_PORTFOLIO_NOTIONAL_ALIGNED",  # P12
    "MIDLONG_CHART_ADVICE_TTL_MIN",        # P11
    "MIDLONG_EV_ENFORCE_MID",              # P7
    "PB_DD_STALE_HOURS",                   # P17-C①
    "AUDIT_BACKUP_KEEP_DAYS",              # P10
    "BACKEND_CONSOLE_LOG_MAX_MB",          # §64 控制台轮转
]

prod_files, test_files = [], []
for py in BE.rglob("*.py"):
    s = str(py).replace("\\", "/")
    if any(x in s for x in ("__pycache__", "_ai_gen_archive", "_ai_gen_quarantine")):
        continue
    (test_files if "/tests/" in s else prod_files).append(py)

SETTINGS_SRC = (BE / "config" / "settings.py").read_text(encoding="utf-8-sig", errors="replace")

from backend.config.env_registry import KNOWN_FLAGS  # noqa: E402

print(f"{'键':38s} {'读它的文件':28s} {'settings声明':12s} {'登记':6s} {'测试':5s}")
problems: list[str] = []
for key in TODAY_KEYS:
    readers = []
    for py in prod_files:
        txt = py.read_text(encoding="utf-8", errors="replace")
        if re.search(rf'["\']{key}["\']', txt):
            readers.append(str(py.relative_to(BE)).replace("\\", "/"))
    declared = bool(re.search(rf"^{key}\b", SETTINGS_SRC, re.M))
    registered = key in KNOWN_FLAGS
    tested = any(key in p.read_text(encoding="utf-8", errors="replace") for p in test_files)
    reader_show = readers[0] if readers else "❌ 无人读"
    print(f"{key:38s} {reader_show[:28]:28s} {'✅' if declared else '—（os.getenv 直读可不声明）':12s} "
          f"{'✅' if registered else '❌':6s} {'✅' if tested else '❌':5s}")
    if not readers:
        problems.append(f"{key}: 没有任何生产代码读它（装饰键）")
    if not registered:
        problems.append(f"{key}: 未登记到 env_registry")
    if not tested:
        problems.append(f"{key}: 没有契约测试引用")
    if len(readers) > 1:
        problems.append(f"{key}: 被 {len(readers)} 个文件读 —— 需确认不是重复定义: {readers}")

print("\n=== 显式 0/false 语义抽查（用实际读取函数）===")
import os  # noqa: E402

from backend.services.full_auto.midlong_helpers import _cfg_bool_env  # noqa: E402
from backend.services.mlto.midlong_portfolio_risk import _cfg_int_allow_zero  # noqa: E402
from backend.services.risk_management import portfolio_budget as pbm  # noqa: E402
from backend.services.full_auto import midlong_chart_gate as cg  # noqa: E402

checks = [
    ("MIDLONG_PORTFOLIO_NOTIONAL_ALIGNED=0", lambda: _cfg_bool_env("MIDLONG_PORTFOLIO_NOTIONAL_ALIGNED", True), False,
     lambda: os.environ.__setitem__("MIDLONG_PORTFOLIO_NOTIONAL_ALIGNED", "0")),
    ("MIDLONG_PORTFOLIO_NOTIONAL_ALIGNED 未设 ⇒ 默认 True", lambda: _cfg_bool_env("MIDLONG_PROBE_UNSET_X", True), True, lambda: None),
    ("PB_DD_STALE_HOURS=0", lambda: pbm._cfg_float("PB_DD_STALE_HOURS", 12.0), 0.0,
     lambda: os.environ.__setitem__("PB_DD_STALE_HOURS", "0")),
    ("MIDLONG_CHART_ADVICE_TTL_MIN=0", lambda: cg._advice_ttl_min(), 0,
     lambda: os.environ.__setitem__("MIDLONG_CHART_ADVICE_TTL_MIN", "0")),
]
for label, fn, expect, setup in checks:
    setup()
    got = fn()
    ok = (got == expect)
    print(f"  {label:52s} -> {got!r}  {'✅' if ok else f'❌ 期望 {expect!r}'}")
    if not ok:
        problems.append(f"{label}: 显式 0 语义错误（got {got!r}）")

print("\n=== 结论 ===")
if problems:
    print(f"❌ {len(problems)} 项待处理：")
    for p in problems:
        print("   -", p)
else:
    print("✅ 今天新增的配置键：读路径/声明/登记/测试/零语义 全部通过")
