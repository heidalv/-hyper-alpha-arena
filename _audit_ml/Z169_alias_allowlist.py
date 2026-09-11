# -*- coding: utf-8 -*-
"""Z169（P16 前置）：确定需要做「身份别名」的**顶格包名集合**。

口径：
  * 候选 = backend/ 下的顶层目录与 .py 模块；
  * 需要 = 生产代码中确实存在**非限定导入**（`from X...` / `import X`）的名字；
  * 排除 = 与 site-packages 第三方同名（alembic/schemas）或非包文件（如 models.py 与 backend/models/ 冲突需人工判断）。
输出即 module_alias 的 allowlist 依据。
"""
from __future__ import annotations

import os
import re
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
BE = ROOT / "backend"

dirs = sorted(d for d in os.listdir(BE) if os.path.isdir(BE / d) and not d.startswith("__") and d != "backend")
files = sorted(f[:-3] for f in os.listdir(BE) if f.endswith(".py") and f != "__init__.py")
candidates = sorted(set(dirs) | set(files))

# site-packages 冲突
third_party = set()
for name in candidates:
    for p in sys.path:
        if "site-packages" not in p:
            continue
        if (Path(p) / name).exists() or (Path(p) / f"{name}.py").exists():
            third_party.add(name)

pat_dirs = re.compile(r"^\s*(?:from|import)\s+([A-Za-z_][\w]*)\b")
hits: Counter[str] = Counter()
where: dict[str, set[str]] = {}
for py in sorted(BE.rglob("*.py")):
    rel = str(py.relative_to(BE))
    if "__pycache__" in rel or "_pytest_tmp" in rel:
        continue
    for line in py.read_text(encoding="utf-8", errors="replace").splitlines():
        m = pat_dirs.match(line)
        if not m:
            continue
        name = m.group(1)
        if name in candidates:
            hits[name] += 1
            where.setdefault(name, set()).add(rel.split(os.sep)[0])

print(f"backend/ 顶格候选 {len(candidates)} 个；被非限定导入的名字 {len(hits)} 个\n")
need, skip = [], []
for name, n in hits.most_common():
    if name in third_party:
        skip.append((name, n, "与第三方同名"))
    else:
        need.append((name, n, len(where.get(name, ()))))
print("=== 需要别名（按引用行数）===")
for name, n, nfiles in need:
    print(f"  {n:5d} 行 / {nfiles:3d} 个顶层目录  {name}")
print("\n=== 排除 ===")
for name, n, why in skip:
    print(f"  {n:5d} 行  {name}  ({why})")
print("\nALIAS_ALLOWLIST =", sorted(x[0] for x in need))
