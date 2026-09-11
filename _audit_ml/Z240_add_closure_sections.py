# -*- coding: utf-8 -*-
"""[§83.5/83.6] 在 §23 之前插入本轮闭环状态两节。"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
P = Path(r"D:\001Alpha\Hyper-Alpha-Arena\_中长线负期望根因报告_20260909.md")
text = P.read_text(encoding="utf-8")
anchor = "## 23. 验收标准"
idx = text.index(anchor)
if "### 83.6 目标①③ 的**闭环状态**" in text:
    print("已存在，跳过")
    raise SystemExit(0)

BLOCK = """### 83.5 决策结果（2026-09-11 16:3x，用户已拍板）

| 决策 | 选择 | 落地 |
|---|---|---|
| **P25** 历史夹具行是否清理 | **B 不清理，仅标注** | 保留原始审计记录；`opened` 一律当**上界**看（§82.4/§62.6 已标注）；修复后不再新增 |
| **P26** P19-B 抑制面是否收窄 | **B 维持现状，等真实样本** | 不擅自回滚；after 侧样本 3–5 天后用 `Z235`+`Z236`+`Z221` 复核再定；`EXIT_CHANNEL_BREAKER_UNIFIED=false` 仍是一键回滚位 |

### 83.6 目标①③ 的**闭环状态**（本轮结束时的实话）

| 目标 | 状态 | 证据 |
|---|---|---|
| ① 逐通道/逐层净期望与回吐度量 | ✅ **仪器齐备并已出判定**：误伤 0 / 漏报 0 / 一致 7 / 独立复核逐键一致 / 可抑制通道 30 天 −$126.96 / 逐通道回吐表 / 反事实代理（弱证据已标注方向） | `Z235`、`Z221`(A–F)、`Z237` |
| ① 修复**前后**对照 | ⏳ **after 侧 n=0**（10:19 后中长线零平仓）⇒ 无对照数据，**不能**声称"效果已验证" | `Z221` 输出 |
| ② 处理唯一待办 P5 | ✅ 已执行 P5-A 并线上验证（日亏闸读数 −$5.35 / 阈值 −$50） | §82.1 |
| ③ 抑制安全核验 | ✅ 监控器 + 14 例双向契约；当前判定 **NO_DATA**（0 次抑制）⇒ 安全属性**未被证伪也未被证实**，等首次抑制事件 | `Z236`、`test_suppression_safety_monitor_20260911.py` |
| ④ 脚本/判定/契约/报告/清单同步 | ✅ 本轮新增 4 个脚本（`Z235`–`Z238`）+ 14 例契约 + §83 章节 + §62.6 订正行 + 决策队列 P25/P26；清单自校验 ✅（66 条） | 本节与 §83.1–83.4 |

⇒ **本目标仍未完成**：①的前后对照与③的实战验证都需要**真实样本**（当前 after=0、抑制事件=0）。
下一轮起每日 09:05 自动产出 `Z221`/`Z235`/`Z236`/`Z237` 读数（已挂计划任务）；
一旦 `after` 侧累计 ≥15 笔或首次出现 `exit_channel_broken`，即可给出**结论性**判定。

---

"""

shutil.copy2(P, P.with_name(P.name + f".pre_835_{time.strftime('%Y%m%d_%H%M%S')}"))
P.write_text(text[:idx] + BLOCK + text[idx:], encoding="utf-8")
print("已插入 §83.5/§83.6（锚点行号约", text[:idx].count("\n") + 1, "）")
