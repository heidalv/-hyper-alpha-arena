# -*- coding: utf-8 -*-
"""Z166：双身份下**有状态模块**是否分裂（缓存类 —— 直接关系到价格/数据读取一致性）。"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT / "backend"))

TARGETS = [
    ("price_cache", None),
    ("market_flow_collector", "market_flow_collector"),
    ("onchain_data_collector", "onchain_collector"),
    ("kline_cache_service", None),
    ("unified_data_pool", None),
    ("signal_detection_service", "signal_detection_service"),
]

print(f"{'模块':32s} {'是否同一对象':14s} 说明")
for mod, attr in TARGETS:
    row = f"{mod:32s} "
    try:
        a = importlib.import_module(f"services.{mod}")
    except Exception as e:  # noqa: BLE001
        print(row + f"顶格身份导入失败: {type(e).__name__}")
        continue
    try:
        b = importlib.import_module(f"backend.services.{mod}")
    except Exception as e:  # noqa: BLE001
        print(row + f"包身份导入失败: {type(e).__name__}")
        continue
    if attr:
        va, vb = getattr(a, attr, None), getattr(b, attr, None)
        same = (va is vb)
        note = "" if va is not None and vb is not None else "（属性缺失，无法比较）"
        print(row + f"{'同一 ✅' if same else '分裂 ❌' if va is not None and vb is not None else '?':14s} "
              f"{attr}={type(va).__name__} {note}")
    else:
        # 无显式单例：比较模块级 dict/缓存对象身份
        shared = [k for k in vars(a) if not k.startswith("__") and k in vars(b) and vars(a)[k] is vars(b)[k]]
        split = [k for k in vars(a) if not k.startswith("__") and k in vars(b) and vars(a)[k] is not vars(b)[k]]
        print(row + f"{'模块对象分裂 ❌' if a is not b else '同一 ✅':14s} "
              f"共享可调用/常量 {len(shared)}，非共享 {len(split)}")
