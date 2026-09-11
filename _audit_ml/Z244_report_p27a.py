# -*- coding: utf-8 -*-
"""[§84.6] 记录 P27-A 执行结果 + .cmd 编码教训；并给清单加 #69 行、同步统计。"""
from __future__ import annotations

import re
import shutil
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
P = Path(r"D:\001Alpha\Hyper-Alpha-Arena\_中长线负期望根因报告_20260909.md")
text = P.read_text(encoding="utf-8")
if "### 84.6 执行结果" in text:
    print("已存在 §84.6，跳过")
    raise SystemExit(0)

BLOCK = """### 84.6 执行结果（P27-A 已上线，2026-09-11 16:5x）

用户拍板 **A. 加证据新鲜度约束**，已实现并上线：

| 改动 | 文件 | 要点 |
|---|---|---|
| 通道样本时间戳 | `source_attribution.record_close` | 每次记 `bst["last_ts"] = time.time()` |
| 新鲜度查询 | `source_attribution.exit_channel_evidence_fresh()` | 返回 `(fresh, age_days, limit)`；无时间戳 ⇒ 不新鲜（fail-open，不抑制） |
| 闸门约束 | `exit/channel_breaker_gate.should_suppress` | shadow 但过期 ⇒ **只记录不抑制**，WARNING 带 `tier\\|channel` 与 age |
| 回填补时间戳 | `exit/breaker_backfill` + `backfill_exit_channel_breaker.py` | 支持 4 元组 `(reason,tier,win,ts)`；`merge_into_state` 取**较新**者 |
| 配置 | `settings.BREAKER_EVIDENCE_STALE_DAYS` + registry + `.env` | 默认 **7 天**，`0` = 关闭（一键回滚） |

**现场（回填 + 重启后实测，只读探针）**：

```
trend_broken            tier=mid    抑制=False  why=stale_evidence:mid|trend_broken(13.8 天>7d)
midlong                 tier=mid    抑制=False  why=stale_evidence:mid|midlong(7.7 天>7d)
scalp_review_time_decay tier=short  抑制=True   why=short|scalp_review_time_decay   (6.0 天，新鲜)
lifecycle_time_decay    tier=short  抑制=True   why=short|lifecycle_time_decay      (5.9 天，新鲜)
sl                      tier=short  抑制=False  why=protected_or_empty:sl
symbol_removed          tier=short  抑制=False  why=protected_or_empty:symbol_removed
```

WARNING 也按预期落日志：`[ExitChannelBreaker] 证据过期 ⇒ 本次不抑制（P27-A）：mid|trend_broken 最新样本 13.8 天 > 7 天`。
回填把 **42/58** 个通道补上了 `last_ts`（备份 `fusion_attribution.json.bak_20260911_164914`）。

**测试与变异**：新增 `test_breaker_evidence_freshness_20260911.py`（9 例：写入/新鲜/过期/
无时间戳/`0`=关闭/边界/回填取新）＋ `test_exit_channel_breaker_unified_…` 增 3 例（过期不抑制、
新鲜照常抑制、无时间戳 fail-open）；**变异总账 14/14 全部变红**（M13 = 拆掉新鲜度检查、
M14 = 无时间戳即视为新鲜）。

### 84.7 附带修好的一处"静默失效"：每日任务脚本的非 ASCII 陷阱

排查"每日任务没产出 Z221 段落"时发现：`scripts/run_midlong_audit_daily.cmd` 里我写的
**中文 echo 行**被 `cmd.exe` 按 OEM 码页解析后**断行**，变成
`'\midlong_audit_suite.py' 不是内部或外部命令` 之类的假命令 ⇒ 后续步骤被跳过、
日志里只剩 Z237 段（**这正是"调度看起来成功、实际什么都没跑"的典型**；
`Last Result` 仍为 0）。

修法：`.cmd` **一律 ASCII-only**（中文标签由 Python 脚本自己打印，它们有 `PYTHONIOENCODING=utf-8`），
并加 `[1/3]/[2/3]/[3/3]` 步骤标记便于事后核对。修复后实跑：日志 14440→24721 字节、
三段齐全、`P26-B 复核条件` 行如期出现、`Last Result=0`。

> 与 §82.6 同一族教训：**跨编码边界的文本一律别交给 shell**。`cmd`/PowerShell 只负责
> 启动与重定向，所有中文输出交给 Python。

### 84.8 决策 P26-B 的自动复核已接线

`Z221` 新增 **E′ 段**：每日自动判定 `after 侧 mid/long 平仓 ≥15 笔` 或 `抑制事件 >0`，
满足即打印"✅ 条件已满足：可以复核"。当前读数：**0/15 笔、0 次抑制 ⇒ ⏳ 未满足**。

"""

# 在 §23 之前插入
idx = text.index("## 23. 验收标准")
shutil.copy2(P, P.with_name(P.name + f".pre_846_{time.strftime('%Y%m%d_%H%M%S')}"))
text = text[:idx] + BLOCK + text[idx:]

# 给清单加 #69 行（放在 #68 行之后）
lines = text.splitlines()
row69 = ("| 69 | 🟠 中 | **熔断证据自锁（latent）**：抑制发生在 `record_close()` **之前** ⇒ 被抑制通道"
         "不再产生样本 ⇒ 胜率永久冻结；实测 `mid\\|trend_broken` 窗口最新样本 **14.1 天**、"
         "`mid\\|midlong` **8.0 天**却仍会抑制下一次离场（用两周前的证据拦今天的出场） | "
         "§84.1–84.2；`_audit_ml/Z241_breaker_evidence_lock.py`、"
         "`test_breaker_evidence_lock_20260911.py`、`test_breaker_evidence_freshness_20260911.py` | "
         "✅ **已修 P27-A**：`last_ts` + `BREAKER_EVIDENCE_STALE_DAYS=7`（`0`=关闭），"
         "过期 ⇒ 只记录不抑制 + WARNING；回填补时间戳、现场实测两条过期通道已不再抑制；变异 M13/M14 变红 |")
anchor = next((i for i, l in enumerate(lines) if l.startswith("| 68 |")), None)
assert anchor is not None, "未找到 #68 行"
lines.insert(anchor + 1, row69)
text = "\n".join(lines) + "\n"
P.write_text(text, encoding="utf-8")
print("已写入 §84.6–84.8 与清单 #69 行")
