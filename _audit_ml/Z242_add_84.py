# -*- coding: utf-8 -*-
"""[§84] 插入本轮发现：熔断证据自锁（缺陷 #69）+ 决策 P27 请求。"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
P = Path(r"D:\001Alpha\Hyper-Alpha-Arena\_中长线负期望根因报告_20260909.md")
text = P.read_text(encoding="utf-8")
anchor = "## 23. 验收标准"
if "## 84. 第 50 轮" in text:
    print("已存在 §84，跳过")
    raise SystemExit(0)

BLOCK = """## 84. 第 50 轮：**熔断的证据自锁** —— 用两周前的证据拦今天的出场（缺陷 #69）（2026-09-11 16:3x）

### 84.1 机制（代码路径证据，3/3 成立）

`_audit_ml/Z241_breaker_evidence_lock.py` 用**源码位置比较**证明：抑制发生在记账**之前**。

| 路径 | 证据 | 结论 |
|---|---|---|
| MLTO `_exec_close` | 熔断判定 `should_suppress`（offset 31442）**早于** `paper_engine.close_position`（32238） | 抑制 ⇒ **不落账** ⇒ `record_close` 不被调用 |
| Master `should_block` | 返回 `blocked`（offset 15120）早于 `_execute_raw`（21321） | 抑制 ⇒ 不执行离场 |
| Master 调用点 | `_gate.blocked`（82928）→ `continue`（83223） | 抑制 ⇒ 本次离场被跳过 |

⇒ **被抑制的通道不再产生新样本**。而熔断窗的更新**只**来自 `record_close()`
（`rebuild_breaker_shadow` 是从这个窗重算，`backfill` 是手动从 DB 灌）
⇒ 一旦某通道被 shadow 并真的抑制了一次，它的胜率就**永久冻结**在该值上（<40%），
**没有自愈路径**（这正是 P17 那个"回撤判据永不自愈"的同族问题，只是换了一条闸）。

### 84.2 数据面：**当前就有两条通道处于"冻结"状态**

阈值：最新样本 > **7 天**（`BREAKER_EVIDENCE_STALE_DAYS`，可配）。

| 通道 | 窗口 | 累计 n | 窗口最新样本 | 年龄 | 可抑制 | 冻结 |
|---|---|---|---|---|---|---|
| `mid\\|trend_broken` | 30 | 65 | **08-28 13:50** | **14.1 天** | 是 | ❗**是** |
| `mid\\|midlong` | 17 | 17 | **09-03 16:23** | **8.0 天** | 是 | ❗**是** |
| `short\\|lifecycle_time_decay` | 22 | 22 | 09-05 10:07 | 6.3 天 | 是 | 否（临界） |
| `short\\|scalp_review_time_decay` | 30 | 107 | 09-05 09:46 | 6.3 天 | 是 | 否（临界） |
| `short\\|max_hold_timeout` | 30 | 646 | 09-02 01:20 | 9.6 天 | 否（保护） | 否 |
| `short\\|sl` | 30 | 308 | 09-05 08:45 | 6.3 天 | 否（保护） | 否 |
| `short\\|symbol_removed` | 30 | 202 | 08-28 08:25 | 14.3 天 | 否（保护，P24-A） | 否 |

⇒ **可抑制的 4 条里已有 2 条证据过期**（`trend_broken` 两周、`midlong` 八天）。
今天**还没发生过抑制**（`exit_channel_broken = 0`），所以这是**潜伏**缺陷；
但只要下一次 `trend_broken`/`midlong` 离场触发，它就会**用两周前的胜率**去拦，
而且拦掉之后那条通道**再也不会刷新证据** ⇒ 形成自我维持的抑制。

### 84.3 与 P26（抑制是否省钱）的关系

§83.4 的反事实代理显示"抑制不省钱"，但那是**平均**结论；本节的发现更进一步：
即便某些通道确实该被抑制，**用过期证据抑制**在统计上也是不可辩护的（regime 已经变了）。
两条证据方向一致 ⇒ 建议给熔断加**证据新鲜度约束**。

### 84.4 契约与监控（已落地，待 P27 决定是否接线规则）

* 纯规则 `is_evidence_frozen(shadowed, suppressible, newest_age_days, stale_days)`；
* 契约测试 `test_breaker_evidence_lock_20260911.py`（**7 例**，双向：过期⇒冻结、
  新鲜⇒不冻结、保护通道/未 shadow⇒不冻结、无记录⇒冻结、边界 `==` 不算过期、
  代码路径 3/3 成立）；
* 已接入每日套件：**8 → 9 项**（新增 `breaker_evidence_age`），实跑全绿。

### 84.5 决策 P27（需你拍板；我未擅自改行为）

| 选项 | 做法 | 代价 |
|---|---|---|
| **A. 加证据新鲜度约束（推荐）** | 给 `breaker[key]` 增记 `last_ts`（`record_close` 与 DB 回填都写）；闸门在抑制前检查：`now − last_ts > BREAKER_EVIDENCE_STALE_DAYS×86400` ⇒ **只记录不抑制**，并打 WARNING（带 age/阈值）。与 P17 同款"自愈闸"，`0`=关闭可回滚 | 需改 3 处（gate/record_close/backfill）+ 契约测试 + 一次重启；会让"过期通道"暂时不被抑制 |
| **B. 定期探针放行** | 每 N 天允许该通道**放行一次**离场以刷新证据（类似来源信用 shadow"重启放行一笔"的设计） | 自愈但引入"已知会亏的一笔"；实现更复杂（需按通道记探针时间） |
| **C. 常态化 DB 回填** | 每日跑 `backfill_exit_channel_breaker.py` | **不解决**"证据本身老"（DB 里同样没有新样本），只保证窗口不截断 |
| **D. 维持现状** | 接受"用旧证据抑制 + 永久冻结" | 与 P17/§83.4 的证据方向相反，风险自留 |

---

"""

shutil.copy2(P, P.with_name(P.name + f".pre_84_{time.strftime('%Y%m%d_%H%M%S')}"))
idx = text.index(anchor)
P.write_text(text[:idx] + BLOCK + text[idx:], encoding="utf-8")
print("已插入 §84")
