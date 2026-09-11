# -*- coding: utf-8 -*-
"""[§83] 在 §62.6 自我订正表里补一行：P19-B 抑制方向的口径订正（§81.1 → §83.4）。"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
P = Path(r"D:\001Alpha\Hyper-Alpha-Arena\_中长线负期望根因报告_20260909.md")
lines = P.read_text(encoding="utf-8").splitlines()

ROW = ("| 「被抑制通道 30 天净额为负 ⇒ 抑制方向正确」（§81.1 的措辞） | "
       "**只对\"已实现口径\"成立，对\"再持有\"口径不成立**：按净额 4 条可抑制通道 30 天合计 "
       "**−$126.96**；但用\"同标的下一笔平仓价\"做价格代理，当时继续持有的代理值 "
       "**−$48.81 vs 实际 −$200.24（差 +$151.43）** ⇒ 抑制这些离场**没有省钱迹象**"
       "（`mid\\|trend_broken` 三档持有上限 delta 皆为正，中位仅 +0.13~+0.32 美元/笔）。"
       "代理非回放（未含费用与后续通道建模），故不擅自回滚，交决策 **P26** | "
       "§81.1 → 订正于 §83.4 |")

idx = next((i for i, l in enumerate(lines) if "被测试夹具污染" in l), None)
assert idx is not None, "未找到锚点行"
if "再持有" in "\n".join(lines[idx:idx + 3]):
    print("已存在订正行，跳过")
else:
    lines.insert(idx + 1, ROW)
    bak = P.with_name(P.name + f".pre_p26_{time.strftime('%Y%m%d_%H%M%S')}")
    shutil.copy2(P, bak)
    P.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("已插入订正行（备份 %s），新行号 %d" % (bak.name, idx + 2))
