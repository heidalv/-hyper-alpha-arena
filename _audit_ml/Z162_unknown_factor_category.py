# -*- coding: utf-8 -*-
"""Z162: 未知因子类别（rev10/rev50 回退 PATTERN）定位 —— 哪些因子文件声明了它们。"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from backend.services.factor_engine.base_factors import FactorCategory  # noqa: E402

print("FactorCategory 成员:", [c.name for c in FactorCategory])

FACTORS = ROOT / "backend/services/factor_engine/factors"
pat = re.compile(r"""['"]category['"]\s*:\s*['"]([^'"]+)['"]|category\s*=\s*['"]([^'"]+)['"]""")
from collections import Counter  # noqa: E402

cats: Counter[str] = Counter()
where: dict[str, list[str]] = {}
for py in sorted(FACTORS.rglob("*.py")):
    txt = py.read_text(encoding="utf-8", errors="replace")
    for m in pat.finditer(txt):
        c = (m.group(1) or m.group(2) or "").strip()
        if c:
            cats[c] += 1
            where.setdefault(c, []).append(str(py.relative_to(ROOT)))
print("\n声明过的 category 值分布（前 25）:")
for k, v in cats.most_common(25):
    print(f"  {v:4d}  {k!r}")

print("\n未知类别（不在别名表/枚举里）的文件:")
known = set(FactorCategory.__members__) | {
    "TECHNICAL", "COMPOSITE", "DISCOVERED", "ALPHA101", "SEED_BOOTSTRAP",
}
for k, v in cats.items():
    if k.strip().upper() not in known:
        print(f"  {k!r} × {v}")
        for f in where[k][:6]:
            print(f"      {f}")
