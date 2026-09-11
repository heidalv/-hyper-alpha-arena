# -*- coding: utf-8 -*-
"""Z158: 坏文件逐目录归类 + 动态加载器扫描根 + 旧树是否可达（字符串路径口径）。"""
from __future__ import annotations

import re
import sys
import warnings
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
BE = ROOT / "backend"
SKIP = ("_pytest_tmp", "__pycache__", ".venv", "node_modules")


def bad_reason(py: Path) -> str | None:
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            compile(py.read_bytes(), str(py), "exec")
            for w in caught:
                if issubclass(w.category, SyntaxWarning):
                    return f"SyntaxWarning:{w.message}"
        return None
    except Exception as e:  # noqa: BLE001
        return type(e).__name__


print("=== 1. 坏文件按父目录归类 ===")
cnt: Counter[str] = Counter()
reasons: Counter[str] = Counter()
detail: dict[str, list[str]] = {}
for py in sorted(BE.rglob("*.py")):
    if any(s in str(py) for s in SKIP):
        continue
    r = bad_reason(py)
    if r:
        rel = py.relative_to(ROOT)
        parent = str(rel.parent)
        cnt[parent] += 1
        reasons[r] += 1
        detail.setdefault(parent, []).append(py.name)
for k, v in cnt.most_common():
    print(f"  {v:4d}  {k}")
print("  原因分布:", dict(reasons))

print("\n=== 2. 非 ai_generated 旧树的坏文件逐个列出 ===")
for parent, names in detail.items():
    if "backend\\factor_engine\\factors\\ai_generated" in parent:
        continue
    print(f"  [{parent}] {len(names)} 个")
    for n in names[:12]:
        print(f"     - {n}")

print("\n=== 3. 旧树包结构 / 是否被字符串路径引用 ===")
old = BE / "factor_engine"
print(f"  backend/factor_engine/__init__.py 存在: {(old / '__init__.py').exists()}")
print(f"  backend/factors/ 存在: {(BE / 'factors').is_dir()}"
      f"  文件数: {len(list((BE / 'factors').rglob('*.py'))) if (BE / 'factors').is_dir() else 0}")
pat = re.compile(r"""["'][^"']*(?<!services[\\/])factor_engine[\\/]factors[^"']*["']""")
hits: list[str] = []
for py in sorted(BE.rglob("*.py")):
    if any(s in str(py) for s in SKIP):
        continue
    txt = py.read_text(encoding="utf-8", errors="replace")
    for i, line in enumerate(txt.splitlines(), 1):
        if pat.search(line):
            hits.append(f"{py.relative_to(ROOT)}:{i}: {line.strip()[:120]}")
print(f"  字符串路径引用 {len(hits)} 处:")
for h in hits[:20]:
    print("   " + h)

print("\n=== 4. factor_loader / discovery 的扫描根 ===")
for f in [
    BE / "services" / "factor_engine" / "factor_loader.py",
    BE / "services" / "ai_factor_discovery_service.py",
]:
    txt = f.read_text(encoding="utf-8", errors="replace").splitlines()
    print(f"  --- {f.relative_to(ROOT)} ---")
    for i, line in enumerate(txt, 1):
        if re.search(r"(FACTOR_(DIR|ROOT|PATH)|factors_dir|ai_generated|root\s*=|Path\()", line):
            print(f"    {i}: {line.strip()[:120]}")
