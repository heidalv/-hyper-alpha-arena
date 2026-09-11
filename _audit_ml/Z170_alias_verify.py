# -*- coding: utf-8 -*-
"""Z170（P16 验证）：装上身份重定向器后，双身份是否真的消失。

与 Z163 的对照：同一段代码，修复前 `services.*` 与 `backend.*` 是两套对象、
loader 加载 0 个；修复后应完全一致、loader 加载 174 个。
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
sys.path.append(str(ROOT / "backend"))  # 与 run_uvicorn_dev.py 保持一致

import backend  # noqa: F401,E402  ← 触发 backend/__init__ 里的身份统一安装
import backend._module_alias as ma  # noqa: E402

print("install() 返回:", {k: (v if k != 'newly_aliased' else f"{len(v)} 个") for k, v in ma.install().items()})

print("\n=== 1. 关键模块身份（期望：全部同一）===")
targets = [
    "services.factor_engine.factor_base",
    "services.factor_engine.factor_loader",
    "services.factor_engine.factor_registry",
    "services.factor_engine.base_factors",
    "services.factor_engine.factor_decay_monitor",
    "services.price_cache",
    "services.scheduler",
    "services.onchain_data_collector",
    "services.market_flow_collector",
    "services.signal_detection_service",
    "services.kline_cache_service",
    "services.unified_data_pool",
    "config.settings",
    "utils",
]
bad = 0
for name in targets:
    try:
        a = importlib.import_module(name)
        b = importlib.import_module("backend." + name)
    except Exception as e:  # noqa: BLE001
        print(f"  {name:52s} 导入失败 {type(e).__name__}: {str(e)[:50]}")
        continue
    same = a is b
    bad += 0 if same else 1
    print(f"  {name:52s} {'同一 ✅' if same else '分裂 ❌'}")

print(f"\n  分裂数 = {bad}")

print("\n=== 2. BaseFactor 类身份与跨身份 issubclass ===")
fb_top = importlib.import_module("services.factor_engine.factor_base")
fb_pkg = importlib.import_module("backend.services.factor_engine.factor_base")
print("  BaseFactor 同一类?", fb_top.BaseFactor is fb_pkg.BaseFactor)
fl = importlib.import_module("services.factor_engine.factor_loader")
print("  FactorLoader 同一类?", fl.FactorLoader is importlib.import_module(
    "backend.services.factor_engine.factor_loader").FactorLoader)

print("\n=== 3. 顶格身份 loader 现在加载多少因子（修复前 = 0）===")
loader = fl.FactorLoader()
n = loader.discover_and_load_all()
print(f"  services.* FactorLoader => {n} 个（失败 {len(loader.failed_files)}）")

print("\n=== 4. 全量分裂自检 ===")
splits = ma.split_report()
print(f"  仍分裂的模块: {len(splits)}")
for k, v in list(splits.items())[:10]:
    print(f"    {k}: {v}")
