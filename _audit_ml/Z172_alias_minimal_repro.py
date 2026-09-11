# -*- coding: utf-8 -*-
"""Z172：最小复现 —— 为什么顶格导入没被别名（开 MODULE_ALIAS_DEBUG=1 看 finder 决策）。"""
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

import backend  # noqa: E402,F401

print("\n--- step: import services.factor_engine.factor_loader ---", file=sys.stderr)
import services.factor_engine.factor_loader as tl  # noqa: E402
import backend.services.factor_engine.factor_loader as bl  # noqa: E402

print("top-level 模块对象 id:", id(tl))
print("backend   模块对象 id:", id(bl))
print("同一模块?", tl is bl)
print("FactorLoader 同一类?", tl.FactorLoader is bl.FactorLoader)
print("sys.modules 键:", "services.factor_engine.factor_loader" in sys.modules,
      "backend.services.factor_engine.factor_loader" in sys.modules)
print("sys.modules 值同一?",
      sys.modules.get("services.factor_engine.factor_loader") is sys.modules.get(
          "backend.services.factor_engine.factor_loader"))
print("tl.__name__ =", getattr(tl, "__name__", None), " tl.__file__ =", getattr(tl, "__file__", None))
print("bl.__name__ =", getattr(bl, "__name__", None), " bl.__file__ =", getattr(bl, "__file__", None))
print("父包 services.factor_engine is backend.services.factor_engine ?",
      sys.modules.get("services.factor_engine") is sys.modules.get("backend.services.factor_engine"))
print("父包属性 factor_loader id:",
      id(getattr(sys.modules.get("services.factor_engine"), "factor_loader", None)),
      id(getattr(sys.modules.get("backend.services.factor_engine"), "factor_loader", None)))
for k in ("services.factor_engine.factor_loader", "backend.services.factor_engine.factor_loader"):
    v = sys.modules.get(k)
    print(f"  sys.modules[{k}] = {type(v).__name__} id={id(v)} name={getattr(v, '__name__', v)}")
import backend._module_alias as ma  # noqa: E402

print("split_report():", ma.split_report())
