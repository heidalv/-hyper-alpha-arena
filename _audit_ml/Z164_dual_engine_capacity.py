# -*- coding: utf-8 -*-
"""Z164：两套模块身份下的因子引擎**实例**各自有多少因子（决定性证据）。

判定口径：分别取 `services.factor_engine.factor_engine` 与
`backend.services.factor_engine.factor_engine` 两个单例，比较 FACTORS 规模与差集。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT / "backend"))  # 与 run_uvicorn_dev.py 一致

import services.factor_engine as top_pkg  # noqa: E402
import backend.services.factor_engine as pkg_pkg  # noqa: E402

top_eng = getattr(top_pkg, "factor_engine", None)
pkg_eng = getattr(pkg_pkg, "factor_engine", None)
print("引擎对象同一? ", top_eng is pkg_eng)

for label, eng in [("services.*", top_eng), ("backend.services.*", pkg_eng)]:
    if eng is None:
        print(f"  {label}: 无 factor_engine 属性")
        continue
    factors = getattr(eng, "FACTORS", None)
    print(f"  {label:20s} 类型={type(eng).__name__}  FACTORS={len(factors) if factors is not None else 'N/A'}")

if top_eng is not None and pkg_eng is not None:
    a = set((getattr(top_eng, "FACTORS", None) or {}).keys())
    b = set((getattr(pkg_eng, "FACTORS", None) or {}).keys())
    print(f"\n  仅 services.* 有: {len(a - b)}  仅 backend.* 有: {len(b - a)}  交集: {len(a & b)}")
    if b - a:
        sample = sorted(b - a)[:12]
        print("  backend 独有因子样例:", sample)

# 注册表单例对比
from services.factor_engine.factor_registry import FactorRegistry as TopReg  # noqa: E402
from backend.services.factor_engine.factor_registry import FactorRegistry as PkgReg  # noqa: E402

print("\n  FactorRegistry 是同一类?", TopReg is PkgReg)
