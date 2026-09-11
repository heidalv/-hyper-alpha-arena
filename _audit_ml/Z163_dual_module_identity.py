# -*- coding: utf-8 -*-
"""Z163（重大发现复核）：同一份代码在进程内存在**两套模块身份**（backend.* vs services.*），
导致模块级单例分裂 —— 因子加载器的第二个实例加载 **0 个因子**。

复现方式与真实启动一致：sys.path = [仓库根] + [backend/]（见 scripts/run_uvicorn_dev.py:48-54）。

输出：
  1. 同名模块是否 `is` 相同；
  2. BaseFactor 类对象是否同一（issubclass 跨路径为何静默为假）；
  3. `services.` 身份的 FactorLoader 实际加载数（预期 0）；
  4. 静态统计：两种导入写法的文件数。
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
BACKEND = ROOT / "backend"
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.path.append(str(BACKEND))  # 与 run_uvicorn_dev.py 一致

print("=== 1. 同名模块的两套身份 ===")
import services.factor_engine.factor_base as fb_top  # noqa: E402
import backend.services.factor_engine.factor_base as fb_pkg  # noqa: E402

print(f"  services...factor_base        id={id(fb_top)}")
print(f"  backend.services...factor_base id={id(fb_pkg)}")
print(f"  is 同一对象? {fb_top is fb_pkg}")
print(f"  BaseFactor 同一类? {fb_top.BaseFactor is fb_pkg.BaseFactor}")
print(f"  sys.modules 中两份都在? "
      f"{'services.factor_engine.factor_base' in sys.modules} / "
      f"{'backend.services.factor_engine.factor_base' in sys.modules}")

print("\n=== 2. 第二个身份的加载器实际加载多少因子 ===")
from services.factor_engine.factor_loader import FactorLoader as TopLoader  # noqa: E402

tl = TopLoader()
n = tl.discover_and_load_all()
print(f"  services.* 身份 FactorLoader.discover_and_load_all() => {n} 个（失败文件 {len(tl.failed_files)}）")
if n == 0:
    print("  ⇒ 该实例的注册表为空：任何走 services.* 身份取因子的路径**拿不到任何因子**")

print("\n=== 3. 静默机制：跨身份 issubclass 判定 ===")
import importlib  # noqa: E402

mods = {}
for name in ["factor_base", "factor_registry", "factor_loader"]:
    a = importlib.import_module(f"services.factor_engine.{name}")
    b = importlib.import_module(f"backend.services.factor_engine.{name}")
    mods[name] = (a is b)
print("  同名子模块是否同一对象:", mods)

print("\n=== 4. 静态统计（生产代码，非 tests） ===")
pat_top = re.compile(r"^\s*(from|import)\s+services\.")
pat_pkg = re.compile(r"^\s*(from|import)\s+backend\.services\.")
cnt_top = cnt_pkg = 0
for py in sorted(BACKEND.rglob("*.py")):
    rel = str(py)
    if "tests" in rel or "__pycache__" in rel or "_pytest_tmp" in rel:
        continue
    for line in py.read_text(encoding="utf-8", errors="replace").splitlines():
        if pat_top.search(line):
            cnt_top += 1
        elif pat_pkg.search(line):
            cnt_pkg += 1
print(f"  `from/import services.*`        : {cnt_top} 行")
print(f"  `from/import backend.services.*`: {cnt_pkg} 行")
print("  ⇒ 两种写法在同进程共存 = 模块身份分裂（单例/类型判定都不共享）")
