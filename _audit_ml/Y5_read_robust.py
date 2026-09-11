# -*- coding: utf-8 -*-
"""读取 lock_robust.json 关键行（滑点敏感度）。"""
import json
from pathlib import Path

p = Path(__file__).resolve().parents[1] / "data" / "lock_robust.json"
d = json.loads(p.read_text(encoding="utf-8"))
for k, v in d["res"].items():
    if ("0.5/0.15" in k) or ("0.4/0.15" in k) or ("基线" in k):
        print(f"{k:<34} 均值={v['mean']:>+7.3f} 合计={v['sum']:>+7.1f} "
              f"胜率={v['win']:.3f} ≤-2%={v['bad_le_-2']:>3} 锁={v['lock_n']:>3}")
