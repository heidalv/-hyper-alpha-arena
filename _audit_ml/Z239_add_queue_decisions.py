# -*- coding: utf-8 -*-
"""[§83.5] 把 P25 / P26 的决策结果写进 §62.5 决策队列（表尾追加两行）。"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
P = Path(r"D:\001Alpha\Hyper-Alpha-Arena\_中长线负期望根因报告_20260909.md")
lines = P.read_text(encoding="utf-8").splitlines()

R25 = ("| **P25** | ~~是否清理审计文件里的历史测试夹具行~~ ✅ **已决策 2026-09-11（选 B：不清理）**"
       "（§82.4）：保留原始审计记录，已知 `opened` 只能当**上界**看；修复后不再新增（pytest 守卫） | "
       "取证优先；真实开仓数以 `paper_positions` 为准 | #68 |")
R26 = ("| **P26** | ~~P19-B 抑制面是否收窄~~ ✅ **已决策 2026-09-11（选 B：维持现状，等真实样本）**"
       "（§83.4）：代理证据（+$151.43 偏向\"继续持有\"）不足以推翻，且 after 侧样本 **n=0**；"
       "3–5 天后用 `Z235`（误伤/漏报）+ `Z236`（抑制安全）+ `Z221`（前后切片）复核再定 | "
       "不擅自回滚；`EXIT_CHANNEL_BREAKER_UNIFIED=false` 仍是一键回滚位 | #60/#66 |")

idx = next((i for i, l in enumerate(lines) if l.startswith("| **P5** |")), None)
assert idx is not None, "未找到 P5 行"
already = any("| **P26** |" in l for l in lines)
if already:
    print("已存在 P25/P26 行，跳过")
else:
    lines.insert(idx + 1, R26)
    lines.insert(idx + 1, R25)
    bak = P.with_name(P.name + f".pre_queue_{time.strftime('%Y%m%d_%H%M%S')}")
    shutil.copy2(P, bak)
    P.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("已追加 P25/P26 决策行（备份 %s）" % bak.name)
