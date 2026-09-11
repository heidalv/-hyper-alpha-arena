# -*- coding: utf-8 -*-
"""[§85] 写入第 51 轮：同类自锁横扫（含一处误报订正）+ 主平仓路径可追溯性修复。"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
P = Path(r"D:\001Alpha\Hyper-Alpha-Arena\_中长线负期望根因报告_20260909.md")
text = P.read_text(encoding="utf-8")
if "## 85. 第 51 轮" in text:
    print("已存在 §85，跳过")
    raise SystemExit(0)

BLOCK = """## 85. 第 51 轮：**同类自锁横扫** + 主平仓路径的可追溯性缺口（2026-09-11 17:0x）

### 85.1 横扫：还有哪些闸门会"冻住自己的证据"？

修完 #69（P27-A）之后必须问"同类还有几个"。`_audit_ml/Z249_gate_selflock_sweep.py`
按三问筛选候选（①证据是什么 ②拦的动作是否正是产生证据的动作 ③有无自愈路径），
先出**源码线索表**，再逐项读代码确认（脚本自称"只出候选，不出判决"）：

| 闸门 | 证据同源 | 新鲜度 | 自愈路径 | 重启重置 | 判定 |
|---|---|---|---|---|---|
| 出场通道熔断 | 是 | ✅（P27-A） | — | — | 曾自锁，**已修** |
| 来源信用 shadow | 是 | 有 | — | ✅ | 可自愈（**重启放行一笔**是既有契约） |
| 组合回撤熔断 | 是 | ✅（P17 `PB_DD_STALE_HOURS`） | ✅ | — | 可自愈 |
| midlong 车道熔断 | 是 | 有 | — | ✅ | 可自愈（状态可重建） |
| tier 熔断器 | 是 | — | ✅（恢复/衰减） | ✅ | 可自愈 |
| 冻结协调器 | 是 | — | ✅（unfreeze） | — | 可自愈 |
| 再入场冷却 | 是 | 有（时间窗） | — | ✅ | 可自愈（本就按时间自动过期） |
| 融合 pwin 地板 | 是 | — | ✅（连好 streak 回落） | — | 可自愈 |
| 活跃因子集 | 是 | — | — | — | ⚠️ **误报**（见 85.2） |

### 85.2 一处**误报**的订正（方法论示范）

关键词横扫把 `backend/services/factor_engine/active_set_policy.py` 标成"❗自锁"，
但读代码后发现：该文件只有 **108 行、纯只读**——它是 `role → 允许状态集合` 的
**SSOT 映射**（`STATES` / `load_factor_active_rows()`），**不做任何移除/拦截决策**，
自然也没有"自愈"概念；真正的因子进出是 `factor_evaluation_pipeline` /
`promotion_scan_service` / `factor_decay_monitor` 的职责。

⇒ 结论：**关键词筛查只能用来生成候选**；把它当判决就会产出假缺陷（本轮差一点又发布一条）。
这是本审计第三次踩"标记太宽"的坑（前两次：§81 的 P19-B 键、§84 的 `lane_pause_reason` 导入名），
纪律：**任何自动判定都必须有第二个独立证据（读代码/读数据）**。

### 85.3 修复缺陷 #70：主平仓路径的抑制**不进事件流**（目标③ 可追溯性）

`master_execution.py` 的主平仓路径在 `_gate.blocked` 时**只 `continue`**：

```python
_gate = unified_exit_executor.should_block(_exit_req)
if _gate.blocked:
    if _gate.convert_to_set_sl: ...
    continue          # ← 抑制事件没有 append_event
```

而同文件的**部分平仓**路径与 MLTO 路径都会把 `event_type` 写进会话事件流
（`exit_channel_broken`）。⇒ 主路径的抑制**在事件流里查不到**，只剩日志一行，
`Z236` 的两条追溯源（`position_exit_events` / `full_auto_sessions.event_log`）都读不到它。
抑制是**风险决策**，必须可追溯（§78 定的 `exit_channel_broken` 口径）。

**已修**：blocked 分支补 `host.append_event(session, _gate.event_type, _gate.detail)`
（失败只 WARNING，不影响抑制本身）；契约测试
`test_master_close_path_appends_suppression_event`（源码窗口断言：`blocked` 分支里必须
同时出现 `_gate.event_type` 与 `append_event`）。

### 85.4 P27-A 的**咨询式降级**也需要可见性

P27-A 的"证据过期 ⇒ 只记录不抑制"**不会**产生 `exit_channel_broken` 事件（它根本没抑制），
只有一行 WARNING ⇒ 若无统计就无从知道"过期规则到底拦下了多少次抑制"。
`Z236` 新增纯函数 `count_stale_advisories(lines)`（按 `tier|channel` 计数）并接入日报；
契约测试 2 例（能计数、无匹配则空）。

### 85.5 本轮结论

* 同类横扫结果：**除 #69 外未发现新的自锁闸门**（其余闸门都有过期/衰减/重启类恢复路径）；
  一处候选为误报，已订正并写进纪律；
* ③ 的可追溯性补上最后一块拼图（主路径事件流）+ 降级计数可见性；
* 仍然缺**真实样本**：after 侧 0/15 笔、抑制事件 0 次 ⇒ `Z236` 判定 NO_DATA（不假装通过）。

"""

idx = text.index("## 23. 验收标准")
shutil.copy2(P, P.with_name(P.name + f".pre_85_{time.strftime('%Y%m%d_%H%M%S')}"))
text = text[:idx] + BLOCK + text[idx:]

lines = text.splitlines()
row70 = ("| 70 | 🟡 低 | **主平仓路径的通道熔断抑制不进事件流**：`master_execution` 主路径在 "
         "`_gate.blocked` 时只 `continue`，不像部分平仓/MLTO 路径那样 `append_event` ⇒ "
         "抑制在 `position_exit_events` 与会话事件流里**都查不到**（只剩日志一行），"
         "而抑制是风险决策必须可追溯 | §85.3；`test_breaker_lookup_key_20260911.py`"
         "（`test_master_close_path_appends_suppression_event`） | ✅ **已修**：blocked 分支补 "
         "`append_event(_gate.event_type, _gate.detail)`（失败仅 WARNING）；契约测试锁住 |")
anchor = next((i for i, l in enumerate(lines) if l.startswith("| 69 |")), None)
assert anchor is not None, "未找到 #69 行"
lines.insert(anchor + 1, row70)
P.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("已写入 §85 与清单 #70 行")
