# -*- coding: utf-8 -*-
"""Z161: 因子加载器端到端冒烟 —— §64 改动后仍能正常加载，并给出真实计数。

输出：加载成功数 / 失败文件数 / 类别分布。这是"改诊断不影响功能"的正面证据。
"""
from __future__ import annotations

import logging
import os
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

# 把 INFO 日志打到 stdout，方便观察新日志是否生效
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)

from backend.services.factor_engine.factor_loader import (  # noqa: E402
    FactorLoader,
    get_factor_loader,
)

loader = FactorLoader()
n = loader.discover_and_load_all()
print(f"\n[Z161] 加载成功 {n} 个；失败文件 {len(loader.failed_files)} 个")
if loader.failed_files:
    for f in loader.failed_files[:10]:
        print("   失败:", f)
cats = Counter()
for fid, cls in loader.loaded_factors.items():
    try:
        cats[cls({}).get_metadata().category] += 1
    except Exception:  # noqa: BLE001
        cats["<meta失败>"] += 1
print("[Z161] 类别分布:", dict(cats))

# 顺带验证单例入口
gl = get_factor_loader()
print("[Z161] 单例 loaded_factors =", len(gl.loaded_factors))
