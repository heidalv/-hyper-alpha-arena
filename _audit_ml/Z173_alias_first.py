# -*- coding: utf-8 -*-
"""Z173：别名先行（先装 finder，再首次导入 services.*）是否干净、无 sys.modules 副本。

对照 Z172：先 import backend 触发装配，再导入 services.* 时留下了一个 stale 副本。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT / "backend"))
os.environ["MODULE_ALIAS_DEBUG"] = "1"

import backend._module_alias as ma  # noqa: E402  ← 只导入别名模块（会触发 backend/__init__ 安装）

ma.install()
print("\n--- 首次导入 services.factor_engine.factor_loader（此前没有任何 backend.* 因子模块）---",
      file=sys.stderr)
import services.factor_engine.factor_loader as tl  # noqa: E402
import backend.services.factor_engine.factor_loader as bl  # noqa: E402

print("同一模块?", tl is bl)
print("FactorLoader 同类?", tl.FactorLoader is bl.FactorLoader)
a = sys.modules.get("services.factor_engine.factor_loader")
b = sys.modules.get("backend.services.factor_engine.factor_loader")
print("sys.modules 值同一?", a is b, id(a), id(b))
print("split_report():", ma.split_report())
print("BaseFactor 同类?", sys.modules["services.factor_engine.factor_base"].BaseFactor is
      sys.modules["backend.services.factor_engine.factor_base"].BaseFactor)
fl = tl.FactorLoader()
n = fl.discover_and_load_all()
print(f"顶格身份 loader => {n} 个（失败 {len(fl.failed_files)}）")
