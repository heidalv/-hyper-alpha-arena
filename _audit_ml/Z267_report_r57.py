# -*- coding: utf-8 -*-
"""[§91] 写入第 57 轮：熔断证据窗的账户污染核查（缺陷 #74）+ 清单/统计同步。"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
P = Path(r"D:\001Alpha\Hyper-Alpha-Arena\_中长线负期望根因报告_20260909.md")
text = P.read_text(encoding="utf-8")
if "## 91. 第 57 轮" in text:
    print("已存在 §91，跳过")
    raise SystemExit(0)

BLOCK = """## 91. 第 57 轮：熔断**证据窗**的账户污染核查（缺陷 #74）（2026-09-11 17:3x）

### 91.1 顺着 #73 往下查：在线风控闸的输入是否也被测试账户污染？

#73 修的是**审计脚本**的口径；但熔断器的证据窗来自 **DB 回填**（P21）与 `record_close`，
而回填脚本当时默认 **`--account 0`（全部账户）**。实测污染面：

```
30 天全层平仓 1864 笔，其中非 14 账户 400 笔（21.5%）
受影响最大的通道：short|max_hold_timeout 131/634、short|sl 65/305、
                short|symbol_removed 36/179、mid|trend_broken 10/65、mid|midlong 2/17 …
```

### 91.2 关键判据：**污染存在，但当前不改变任何判定**

`_audit_ml/Z266_breaker_account_pollution.py` 逐通道对比「全账户窗」与「仅账户 14 窗」的
有效窗口胜率（窗口 = min(MIN_N, 30) = **15 笔**），并检查 shadow 判定是否翻转：

| 通道 | 全账户窗 wr | 仅 14 窗 wr | 样本(全/14) | shadow | 翻转 |
|---|---|---|---|---|---|
| `mid\\|trend_broken` | 20.0% | 20.0% | 65/55 | True | 否 |
| `mid\\|midlong` | 33.3% | 33.3% | 17/15 | True | 否 |
| `short\\|sl` | 0.0% | 0.0% | 305/240 | True（保护） | 否 |
| `short\\|max_hold_timeout` | 26.7% | 26.7% | 634/503 | True（保护） | 否 |
| `short\\|symbol_removed` | 33.3% | 33.3% | 179/143 | True（保护） | 否 |
| 其余 8 个可评估通道 | — | 一致 | — | 一致 | 否 |

⇒ **判定：0 个通道翻转**。也就是说：**污染是真的，但当前没有改变熔断行为**
（最近 15 笔的胜负序列在两种口径下相同）。这与 §90 的 mid/long 情形一致：
口径要修，但不需要回滚任何已生效的决定。

### 91.3 修复（缺陷 #74）

| 改动 | 内容 |
|---|---|
| 回填默认账户 | `backfill_exit_channel_breaker.py` 的 `--account` 默认改为 `AUDIT_ACCOUNT_ID`（**14**），新增 `--all-accounts` 作为对照逃生门 |
| 重新回填 | 以账户 14 口径重写窗口（备份 `fusion_attribution.json.bak_20260911_173638`） |
| 回填结果 | **可评估 13 → 13、shadow 7 → 7、键集合完全一致**（dry-run 与实跑一致）⇒ 与 §91.2 的"不翻转"互相印证 |
| 状态核对 | 58 个通道、42 个带 `last_ts`、`shadow_mode=rolling`；`mid\\|trend_broken` 窗口 30 笔、最新样本 13.8 天（继续受 P27-A 的过期约束，不抑制） |
| 契约测试 | `test_breaker_backfill_scope_20260911.py`（**3 例**）：默认账户=14、`--all-accounts` 语义、账户条件必须真的进 SQL |

> 说明：`research|*` 这类**只有测试账户**数据的键在账户 14 口径下没有样本，
> 回填**不会删除**既有键（`merge_into_state` 只增不减），因此它们仍留在状态里但参数为空；
> 它们全部是保护性/非中长线通道，不影响在线行为。

### 91.4 规则补充（并入 §90.4）

4. **在线风控闸的输入**（熔断证据窗、回吐遥测、统计基线）一律按活跃账户取数；
   任何"从 DB 回填进风控状态"的脚本，默认账户必须是 `AUDIT_ACCOUNT_ID`，并保留显式对照开关。

"""

idx = text.index("## 23. 验收标准")
shutil.copy2(P, P.with_name(P.name + f".pre_91_{time.strftime('%Y%m%d_%H%M%S')}"))
text = text[:idx] + BLOCK + text[idx:]

lines = text.splitlines()
row = ("| 74 | 🟠 中 | **熔断证据窗被测试账户污染**：P21 回填默认 `--account 0`（全部账户），"
       "而 30 天全层平仓 **400/1864 笔（21.5%）**来自已归档测试账户（#147/#149）与短期实验账户"
       "（#156）——`short\\|max_hold_timeout` 131/634、`short\\|sl` 65/305、`mid\\|trend_broken` 10/65 等 | "
       "§91；`_audit_ml/Z266_breaker_account_pollution.py`、"
       "`test_breaker_backfill_scope_20260911.py` | ✅ **已修**：回填默认账户=`AUDIT_ACCOUNT_ID`(14) + "
       "`--all-accounts` 对照；按 14 口径重回填（13→13 可评估、7→7 shadow、键集合一致）。"
       "**实测 0 个通道判定翻转** ⇒ 未影响在线行为，但口径已纠正 |")
anchor = next((i for i, l in enumerate(lines) if l.startswith("| 73 |")), None)
assert anchor is not None, "未找到 #73 行"
lines.insert(anchor + 1, row)
P.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("已写入 §91 + 清单 #74")
