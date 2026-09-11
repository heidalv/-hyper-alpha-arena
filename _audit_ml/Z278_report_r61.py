# -*- coding: utf-8 -*-
"""[§95] 写入第 61 轮：目标③「不产生悬挂仓位」的**预防**开关（P24-C，默认关）+ 清单 #76。"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
P = Path(r"D:\001Alpha\Hyper-Alpha-Arena\_中长线负期望根因报告_20260909.md")
text = P.read_text(encoding="utf-8")
if "## 95. 第 61 轮" in text:
    print("已存在 §95，跳过")
    raise SystemExit(0)

BLOCK = """## 95. 第 61 轮：目标③「不产生悬挂仓位」——从**检测**到**预防**（P24-C 可选开关，默认关）（2026-09-11 17:5x）

### 95.1 缺口是什么

`Z236` 只能**发现**悬挂（抑制后 >48h 未离场）。但 ③ 的原文是"不产生悬挂仓位"：
若某通道被熔断压住、而仓位一直不触发保护性通道，就会被**无限期压住**（§93.3 残留风险 2，
也是当年 P24 的 C 选项——当时用户选了 A，C 留作备选）。

### 95.2 实现（**默认关闭 ⇒ 与旧行为完全一致**）

| 组件 | 内容 |
|---|---|
| 计数 | `SourceAttribution._suppress_log`：`tier|通道 → [时间戳]`，进程内、窗口内自动剪枝（`EXIT_SUPPRESS_WINDOW_H`，默认 24h） |
| 记账 | `note_suppression()`：由闸门在**实际抑制生效**时调用（保护性通道/证据过期/开关关闭都不会记） |
| 上限 | `should_suppress()`：证据新鲜度通过后，若 `EXIT_SUPPRESS_MAX_COUNT>0` 且窗口内计数 ≥ 上限 ⇒ **放行本次离场** + WARNING（带键与计数），不再产生新的抑制 |
| 配置 | `EXIT_SUPPRESS_MAX_COUNT`（默认 **0=关闭**）、`EXIT_SUPPRESS_WINDOW_H`（默认 24）；已登记 `env_registry`（未写入 `.env`） |
| 语义 | 计数只按**通道**（不按仓位）——一个通道被反复抑制 N 次后，第 N+1 次离场放行，保证"不可能被永久压住" |

### 95.3 契约（`test_suppression_cap_20260911.py`，6 例）

默认关闭连续抑制不设限｜上限=2 时第 3 次放行 + WARNING｜窗口过期计数清零（monkeypatch 时间）｜
保护性通道不计数｜键归一化与 shadow 同口径｜接线护栏（闸门必须真的调用两个方法）。

回归：既有 5 个熔断相关测试文件 **47 passed**（行为零变化）。

### 95.4 决策 P31（供选择，默认不动）

* **A. 开启**（例如 `EXIT_SUPPRESS_MAX_COUNT=3` + `EXIT_SUPPRESS_WINDOW_H=24`）：
  "同通道 24h 内最多压 3 次离场"——当 P19-B 的抑制真的开始发生且你想确保仓位终会离场时用；
* **B. 维持 0（现状）**：悬挂交给 `Z236` 检测 + 保护性通道兜底；
* 回滚 = 删键或置 0，无需改代码。

"""

idx = text.index("## 23. 验收标准")
shutil.copy2(P, P.with_name(P.name + f".pre_95_{time.strftime('%Y%m%d_%H%M%S')}"))
text = text[:idx] + BLOCK + text[idx:]

lines = text.splitlines()
row = ("| 76 | 🟡 低 | **熔断抑制无上限 ⇒ 仓位可能被无限期压住**（③「不产生悬挂仓位」只做了检测没做预防）："
       "若通道被 shadow 而仓位始终不触发保护性通道，离场会被持续抑制；§93.3 残留风险 2（P24 的 C 选项） | "
       "§95；`test_suppression_cap_20260911.py` | ✅ **已加（默认关闭）**：`EXIT_SUPPRESS_MAX_COUNT`（0=关）+ "
       "`EXIT_SUPPRESS_WINDOW_H`（默认 24h）窗口计数，超限即放行 + WARNING；6 例契约、既有 47 passed、"
       "是否开启待 **P31** |")
anchor = next((i for i, l in enumerate(lines) if l.startswith("| 75 |")), None)
assert anchor is not None, "未找到 #75 行"
lines.insert(anchor + 1, row)
q = next((i for i, l in enumerate(lines) if l.startswith("| **P30** |")), None)
if q is not None:
    lines.insert(q + 1, "| **P31** | **是否开启抑制上限**（`EXIT_SUPPRESS_MAX_COUNT`，默认 0=关） | "
                        "开启后『同通道窗口内最多压 N 次离场』，保证不悬挂；关闭=现状（Z236 检测 + 保护通道兜底） | "
                        "#76（**待决策**） |")
P.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("已写入 §95 + 清单 #76 + 队列 P31")
