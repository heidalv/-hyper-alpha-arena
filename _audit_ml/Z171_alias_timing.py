# -*- coding: utf-8 -*-
"""Z171：定位 Z170 里"顶格身份已统一、但 backend 身份 loader 反而 0 个"的时序。

逐步打印：每一步之后的两套 BaseFactor 身份 + 已分裂模块。
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

STEP = {"n": 0}


def probe(label: str) -> None:
    STEP["n"] += 1
    import importlib

    fb_top = sys.modules.get("services.factor_engine.factor_base")
    fb_pkg = sys.modules.get("backend.services.factor_engine.factor_base")
    fl = sys.modules.get("backend.services.factor_engine.factor_loader")
    bt = getattr(fb_top, "BaseFactor", None)
    bp = getattr(fb_pkg, "BaseFactor", None)
    print(f"[{STEP['n']}] {label}")
    print(f"    services...factor_base   = {'载入' if fb_top else '-'} BaseFactor id={id(bt) if bt else '-'}")
    print(f"    backend...factor_base    = {'载入' if fb_pkg else '-'} BaseFactor id={id(bp) if bp else '-'}")
    print(f"    同一模块? {fb_top is fb_pkg if (fb_top and fb_pkg) else 'N/A'}；同一 BaseFactor? "
          f"{bt is bp if (bt and bp) else 'N/A'}")
    if fl:
        print(f"    loader.BaseFactor id={id(getattr(fl, 'BaseFactor', None))}")


import backend  # noqa: E402,F401
probe("import backend（含身份统一安装）")

import backend.services.factor_engine.factor_loader as bl  # noqa: E402
probe("import backend.services.factor_engine.factor_loader")

loader = bl.FactorLoader()
n = loader.discover_and_load_all()
print(f"    backend 身份 loader => {n} 个（失败 {len(loader.failed_files)}）")
probe("backend 身份 loader 跑完后")

import services.factor_engine.factor_loader as tl  # noqa: E402
probe("import services.factor_engine.factor_loader（顶格局）")
print(f"    顶格 loader 与 backend loader 同一类? {tl.FactorLoader is bl.FactorLoader}")

# 直接看一个因子文件绑定的 BaseFactor
import importlib  # noqa: E402

fm = importlib.import_module("backend.services.factor_engine.factors.ai_generated.ai_gen_momvol")
bases = [c for c in vars(fm).values() if isinstance(c, type) and c.__name__ == "BaseFactor"]
print("    因子文件里 BaseFactor 的 id:", [id(c) for c in bases])
print("    backend loader.BaseFactor id:", id(bl.BaseFactor))
