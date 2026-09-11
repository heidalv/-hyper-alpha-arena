# -*- coding: utf-8 -*-
"""[§92] 写入第 58 轮：套件脚本的账户口径（缺陷 #75）+ 清单/统计同步。"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
P = Path(r"D:\001Alpha\Hyper-Alpha-Arena\_中长线负期望根因报告_20260909.md")
text = P.read_text(encoding="utf-8")
if "## 92. 第 58 轮" in text:
    print("已存在 §92，跳过")
    raise SystemExit(0)

BLOCK = """## 92. 第 58 轮：把账户口径推到**每日套件**（缺陷 #75）（2026-09-11 17:4x）

### 92.1 发现：套件里两个"面向业绩"的脚本没有账户条件

§90.4/§91.4 定的规则是"业绩一律按活跃 PAPER 账户（14）"。本轮扫每日套件：

| 套件脚本 | 源码 `account_id` 命中 | 判定 |
|---|---|---|
| `audit_profit_giveback.py`（浮盈回吐） | 0 次 | ❗ 未按账户 |
| `audit_gate_edge.py`（闸门边际效益） | 0 次 | ❗ 未按账户 |
| `audit_position_event_consistency.py`（持仓/事件一致性） | 0 次 | ⚪ 结构性校验，账户无关（保留） |
| `audit_paper_live_divergence.py`（paper/live 分叉） | 1 次 | ✅ 已按账户 |

### 92.2 影响量化：**当前窗口无差异，属潜在污染**

`_audit_ml/Z269_suite_account_scope.py` 用同口径聚合做对照（近 14 天 mid/long）：

| 口径 | 笔数 | 净额$ | giveback 模式 | 大亏笔数 |
|---|---|---|---|---|
| 全账户（脚本原口径） | 59 | −185.95 | 2（3.4%） | 31 |
| **仅账户 14** | 59 | −185.95 | 2（3.4%） | 31 |

⇒ **数字完全相同**：因为那两个实验/测试账户的活动都停在 **8/22 之前**，
14 天窗口（8/28 起）根本没覆盖到它们。所以这是**潜在**污染（任何回看到 8/23 之前的窗口、
或未来再开实验账户时就会中招），而不是"当前数字错了"。
这条结论与 §91.2（熔断窗口 0 个判定翻转）互相印证：**口径要修，结论不变**。

### 92.3 修复（缺陷 #75）

1. **口径模块提升**：`_audit_ml/_scope.py` → **`backend/config/audit_scope.py`**（套件脚本在
   `backend/scripts/` 下无法 import `_audit_ml`）；`_audit_ml/_scope.py` 改为**薄转发**，
   既有 5 个度量脚本无需改动（实测两边 `describe_scope()` 输出一致）；
2. `audit_profit_giveback.py` / `audit_gate_edge.py`：SQL 加 `{ACCT}` 占位、
   运行头部打印 `账户口径：account_id=14`（`AUDIT_ACCOUNT_ID=0` 可关，仅对照）；
   实跑核对：回吐脚本 n=59 与改前一致、`gate_edge` 输出正常（exit 0）；
3. 契约测试 `test_suite_account_scope_20260911.py`（**4 例**）：规范模块默认值/覆盖/`0=关`/
   归档告警、**薄转发一致性**、两个套件脚本的 `{ACCT}` 与口径打印护栏。

### 92.4 口径纪律（第 3 次收敛，并入 §90.4/§91.4）

5. 口径模块只有**一个真相源**（`backend/config/audit_scope.py`），任何新脚本一律 import 它；
6. 每个基于持仓的审计脚本都必须：**带账户条件 + 打印口径**（契约测试按名单强制）。

"""

idx = text.index("## 23. 验收标准")
shutil.copy2(P, P.with_name(P.name + f".pre_92_{time.strftime('%Y%m%d_%H%M%S')}"))
text = text[:idx] + BLOCK + text[idx:]

lines = text.splitlines()
row = ("| 75 | 🟡 低 | **每日套件里两个业绩脚本未按账户取数**（`audit_profit_giveback` / "
       "`audit_gate_edge` 源码 `account_id` 命中 0 次）⇒ 一旦窗口回看到 8/23 之前（#156 实验账户、"
       "#147/#149 测试残留）数字就会混杂；本轮实测 14 天窗口**无差异**（两账户活动止于 8/22）⇒ "
       "属**潜在**污染 | §92；`_audit_ml/Z269_suite_account_scope.py`、"
       "`backend/config/audit_scope.py`、`test_suite_account_scope_20260911.py` | ✅ **已修**：口径模块提升为 "
       "包内规范位置（`_scope` 薄转发）+ 两脚本注入 `{ACCT}` 与口径打印 + 4 例契约 |")
anchor = next((i for i, l in enumerate(lines) if l.startswith("| 74 |")), None)
assert anchor is not None, "未找到 #74 行"
lines.insert(anchor + 1, row)
P.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("已写入 §92 + 清单 #75")
