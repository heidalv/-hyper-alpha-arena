# -*- coding: utf-8 -*-
"""Z157: 146 个「无法导入」的 .py 到底在**活目录**还是**死目录**？

口径：与 import 机制一致地编译**字节**（解析 PEP 263 coding cookie）。
输出：按目录分组的坏文件统计 + 两个候选目录（backend/factor_engine vs
backend/services/factor_engine）的健康度对比 + 谁在 import 旧树。
"""
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


def verdict(py: Path) -> str | None:
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            compile(py.read_bytes(), str(py), "exec")
            for w in caught:
                if issubclass(w.category, SyntaxWarning):
                    return f"SyntaxWarning:{w.message}"
        return None
    except Exception as e:  # noqa: BLE001
        return f"{type(e).__name__}:{str(e)[:60]}"


def scan(d: Path) -> tuple[int, list[str]]:
    files = [p for p in sorted(d.rglob("*.py")) if not any(s in str(p) for s in SKIP)]
    bad = [str(p.relative_to(ROOT)) for p in files if verdict(p)]
    return len(files), bad


print("=== 1. 全 backend 坏文件按目录分组 ===")
buckets: Counter[str] = Counter()
allbad: list[str] = []
for py in sorted(BE.rglob("*.py")):
    if any(s in str(py) for s in SKIP):
        continue
    if verdict(py):
        allbad.append(str(py.relative_to(ROOT)))
        buckets["/".join(str(py.relative_to(ROOT)).split("/")[:4])] += 1
print(f"坏文件总数: {len(allbad)}")
for k, v in buckets.most_common(20):
    print(f"  {v:4d}  {k}")

print("\n=== 2. 两个候选因子目录健康度 ===")
for label, d in [
    ("旧树 backend/factor_engine/factors/ai_generated", BE / "factor_engine" / "factors" / "ai_generated"),
    ("活树 backend/services/factor_engine/factors/ai_generated",
     BE / "services" / "factor_engine" / "factors" / "ai_generated"),
]:
    if not d.exists():
        print(f"  {label}: 不存在")
        continue
    n, bad = scan(d)
    print(f"  {label}: 共 {n} 个 .py，坏 {len(bad)}")
    for b in bad[:3]:
        print(f"      e.g. {b}")

print("\n=== 3. 谁在 import 旧树 backend.factor_engine（非 services） ===")
pat = re.compile(r"(from|import)\s+backend\.factor_engine\b|from\s+\.\.factor_engine\b")
hits = []
for py in sorted(BE.rglob("*.py")):
    if any(s in str(py) for s in SKIP):
        continue
    txt = py.read_text(encoding="utf-8", errors="replace")
    for i, line in enumerate(txt.splitlines(), 1):
        if pat.search(line):
            hits.append(f"{py.relative_to(ROOT)}:{i}: {line.strip()[:100]}")
print(f"引用点 {len(hits)} 处:")
for h in hits[:25]:
    print("  " + h)

print("\n=== 4. 旧树是否被 sys.path 动态加载（pkgutil/importlib 扫描） ===")
dyn = []
for py in sorted(BE.rglob("*.py")):
    if any(s in str(py) for s in SKIP):
        continue
    txt = py.read_text(encoding="utf-8", errors="replace")
    if "pkgutil" in txt or "importlib.import_module" in txt or "spec_from_file_location" in txt:
        if "factor_engine" in txt or "factors" in txt:
            dyn.append(str(py.relative_to(ROOT)))
print(f"疑似动态加载点 {len(dyn)} 个:")
for d in dyn[:20]:
    print("  " + d)
