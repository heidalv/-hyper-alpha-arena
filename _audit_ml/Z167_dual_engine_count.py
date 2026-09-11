# -*- coding: utf-8 -*-
"""Z167：`base_factors` 模块级引擎在两套身份下各自装载了多少因子（决定性数字）。

注意与 Z164 的区别：`services.factor_engine.factor_engine`（包属性）是**再导出**，
指向 backend 身份的引擎；但 `from services.factor_engine.**base_factors** import factor_engine`
拿到的是**顶格身份自己的**引擎 —— 实测两者因子数不同。
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT / "backend"))

bf_top = importlib.import_module("services.factor_engine.base_factors")
bf_pkg = importlib.import_module("backend.services.factor_engine.base_factors")
eng_top = getattr(bf_top, "factor_engine", None)
eng_pkg = getattr(bf_pkg, "factor_engine", None)

print("base_factors 模块同一?", bf_top is bf_pkg)
print("模块级引擎同一?  ", eng_top is eng_pkg)
for label, eng in [("services.factor_engine.base_factors.factor_engine", eng_top),
                   ("backend.services.factor_engine.base_factors.factor_engine", eng_pkg)]:
    f = getattr(eng, "FACTORS", None)
    print(f"  {label}\n      FACTORS = {len(f) if f is not None else 'N/A'}")

# 包属性再导出的引擎
pkg = importlib.import_module("services.factor_engine")
print("\n包属性 services.factor_engine.factor_engine is backend 模块引擎?",
      getattr(pkg, "factor_engine", None) is eng_pkg,
      f" FACTORS={len(getattr(getattr(pkg, 'factor_engine', None), 'FACTORS', {}) or {})}")

if eng_top is not None and eng_pkg is not None:
    a = set((getattr(eng_top, "FACTORS", None) or {}).keys())
    b = set((getattr(eng_pkg, "FACTORS", None) or {}).keys())
    print(f"\n仅顶格身份有 {len(a - b)} 个；仅包身份有 {len(b - a)} 个；交集 {len(a & b)} 个")
    if b - a:
        print("包身份独有（顶格身份缺失）样例:", sorted(b - a)[:10])
