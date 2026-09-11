# -*- coding: utf-8 -*-
"""[§93.4] 把"验证矩阵已接入每日任务第 5 步"记进 §93。"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
P = Path(r"D:\001Alpha\Hyper-Alpha-Arena\_中长线负期望根因报告_20260909.md")
text = P.read_text(encoding="utf-8")
if "### 93.4 每日任务第 5 步" in text:
    print("已存在，跳过")
    raise SystemExit(0)
BLOCK = """### 93.4 每日任务第 5 步：验证矩阵进日报（2026-09-11 17:4x）

`scripts/run_midlong_audit_daily.cmd` 由 4 步扩到 **5 步**，最后一步跑
`_audit_ml/Z272_objective_verification.py` ⇒ **每天的日报末尾都会打印 ①–④ 的状态矩阵**
（交付物数量、清单统计、三个数据门的 PENDING 与触发条件、两台机器判定）。
实跑验证：`exit=0`，日志尾部即为上文 §93 的那段输出。

> 这样做的目的：数据门一开（after ≥15 笔 / 首次抑制 / P29 夹子日志），日报里会**立刻**从
> `[PENDING]` 变 `[OK]`，不需要有人记得去跑复核。

"""

idx = text.index("## 23. 验收标准")
shutil.copy2(P, P.with_name(P.name + f".pre_934_{time.strftime('%Y%m%d_%H%M%S')}"))
text = text[:idx] + BLOCK + text[idx:]
P.write_text(text, encoding="utf-8")
print("已写入 §93.4")
