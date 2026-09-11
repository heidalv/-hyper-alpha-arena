# -*- coding: utf-8 -*-
"""Z165：双模块身份的**影响面**量化。

1. 模块级单例是否分裂（decay_monitor 等）；
2. 生产代码里 `services.*`（顶格身份）导入的分布与具体文件（中长线相关目录优先）。
"""
from __future__ import annotations

import os
import re
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
BACKEND = ROOT / "backend"
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.path.append(str(BACKEND))

print("=== 1. 模块级单例是否分裂 ===")
import importlib  # noqa: E402

for modname, attr in [
    ("factor_decay_monitor", "decay_monitor"),
    ("factor_engine", "factor_engine"),
    ("factor_cache_manager", "factor_cache_manager"),
]:
    try:
        a = importlib.import_module(f"services.factor_engine.{modname}")
        b = importlib.import_module(f"backend.services.factor_engine.{modname}")
        va, vb = getattr(a, attr, None), getattr(b, attr, None)
        tag = "同一对象 ✅" if (va is not None and va is vb) else "**分裂 ❌**" if va is not None and vb is not None else "无法比较"
        print(f"  {modname}.{attr:22s} -> {tag}")
    except Exception as e:  # noqa: BLE001
        print(f"  {modname}: 导入失败 {type(e).__name__}: {str(e)[:60]}")

print("\n=== 2. 静态分布（生产代码，排除 tests） ===")
pat_top = re.compile(r"^\s*(?:from|import)\s+services\.[\w.]+")
pat_pkg = re.compile(r"^\s*(?:from|import)\s+backend\.services\.[\w.]+")
per_dir_top: Counter[str] = Counter()
files_top: dict[str, list[str]] = {}
per_dir_pkg: Counter[str] = Counter()
for py in sorted(BACKEND.rglob("*.py")):
    rel = str(py.relative_to(BACKEND))
    if "tests" in rel.split(os.sep) or "__pycache__" in rel or "_pytest_tmp" in rel:
        continue
    top_dir = "/".join(rel.split(os.sep)[:2])
    for line in py.read_text(encoding="utf-8", errors="replace").splitlines():
        if pat_top.search(line):
            per_dir_top[top_dir] += 1
            files_top.setdefault(top_dir, []).append(rel)
        elif pat_pkg.search(line):
            per_dir_pkg[top_dir] += 1
print("  使用 `services.*` 顶格身份的目录 TOP10:")
for d, n in per_dir_top.most_common(10):
    print(f"    {n:4d} 行  {d}   （backend 前缀写法 {per_dir_pkg.get(d, 0)} 行）")

print("\n  中长线链路相关文件中使用顶格身份的文件清单:")
keys = ("full_auto", "mlto", "decision_core", "factor_engine", "trend", "risk")
for d, files in sorted(files_top.items()):
    if any(k in d for k in keys):
        uniq = sorted(set(files))
        print(f"    [{d}] {len(uniq)} 个文件")
        for f in uniq[:12]:
            print(f"        {f}")
