# -*- coding: utf-8 -*-
"""Z175（P16 回归）：按**真实启动路径**（import backend.main）检查
  * 因子引擎容量（修复前 183；改写后应仍为 183）
  * sys.modules 中是否还有"同名不同对象"的重复模块
  * 仍分裂的顶格/backend 模块对
"""
from __future__ import annotations

import logging
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT / "backend"))

captured: list[str] = []


class _Cap(logging.Handler):
    def emit(self, record):  # noqa: D102
        msg = record.getMessage()
        if "FactorLoader" in msg or "Registry 合并" in msg or "Registered" in msg:
            captured.append(f"{record.levelname} {record.name}: {msg[:150]}")


logging.getLogger().addHandler(_Cap())
logging.getLogger().setLevel(logging.INFO)

import backend.main  # noqa: E402,F401

import backend.services.factor_engine as fe  # noqa: E402
import backend._module_alias as ma  # noqa: E402

eng = getattr(fe, "factor_engine", None)
print("\n因子引擎 FACTORS =", len(getattr(eng, "FACTORS", {}) or {}))
print("持久化注册表因子数 =", len(getattr(eng, "_registry", {}) or {}) if hasattr(eng, "_registry") else "N/A")

print("\nsplit_report():", ma.split_report())

byname: dict[str, list[str]] = defaultdict(list)
for k, v in list(sys.modules.items()):
    n = getattr(v, "__name__", None)
    if n:
        byname[n].append(k)
dupes = {n: ks for n, ks in byname.items() if len(ks) > 1}
print(f"\n同名多键（同一 __name__ 被登记多次）: {len(dupes)}")
for n, ks in list(dupes.items())[:12]:
    same = all(sys.modules[ks[0]] is sys.modules[k] for k in ks)
    print(f"   {n}: {ks}  对象相同={same}")

print("\n关键日志（因子相关，最多 12 条）:")
for line in captured[:12]:
    print("   ", line)
