# -*- coding: utf-8 -*-
"""[§94] 把预注册判定规则写进报告，并把 Z276 挂到每日任务第 6 步。"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
P = ROOT / "_中长线负期望根因报告_20260909.md"
text = P.read_text(encoding="utf-8")
if "## 94. 第 60 轮" in text:
    print("已存在 §94，跳过")
    raise SystemExit(0)

BLOCK = """## 94. 第 60 轮：**预注册判定规则**（先写规则，再等数据）（2026-09-11 17:5x）

数据门迟迟不开（今日 10:19 后全账户零平仓、抑制事件 0），此时最容易犯的错是
"等数据来了再挑一个好看的口径"。所以本轮把判定规则**先写死**（`_audit_ml/Z276_preregistered_verdict.py`）：

**P19-B（统一熔断是否继续开着）** —— 条件：after 侧 mid/long ≥15 笔 **或** 出现抑制事件
* **PASS**：① 无保护性通道被抑制；② 无悬挂仓位（>48h）；③ after 侧被抑制通道单笔净额 ≥ before 侧；
* **FAIL**（建议回滚 `EXIT_CHANNEL_BREAKER_UNIFIED=false`）：①/② 任一成立，或 ③ 恶化 >20%；
* **INSUFFICIENT**：样本不足。

**P29-C（long SL 上限 + 风险预算）** —— 条件：边界后 long 平仓 ≥5 笔 **或** 出现夹子日志
* **PASS**：新 long 仓 SL 距离中位 ≤3% **且** 单笔亏损中位较边界前改善 ≥30%；
* **FAIL**：SL 距离仍 >3.5% 或单笔亏损中位恶化；
* **INSUFFICIENT**：样本不足。

**当前读数（实跑）**：

```
【P19-B】after n=0（需 ≥15）｜单笔 0.000 vs before n=171 单笔 -1.183｜抑制事件 0 ⇒ INSUFFICIENT
【P29-C】边界后新开 long 0｜long SL 距离均值 after 0.00% vs before 6.56%｜夹子日志 无 ⇒ INSUFFICIENT
```

已挂每日任务**第 6 步**：数据门一开，日报里直接出现 PASS/FAIL，而不是"再想想口径"。

"""

idx = text.index("## 23. 验收标准")
shutil.copy2(P, P.with_name(P.name + f".pre_94_{time.strftime('%Y%m%d_%H%M%S')}"))
P.write_text(text[:idx] + BLOCK + text[idx:], encoding="utf-8")

# 每日任务加第 6 步
cmd = ROOT / "scripts" / "run_midlong_audit_daily.cmd"
src = cmd.read_text(encoding="utf-8")
if "Z276_preregistered_verdict" not in src:
    src = src.replace('echo exit=%ERRORLEVEL%',
                      'echo [6/6] preregistered_verdict >> "logs\\audit_suite_daily.log" 2>&1\n'
                      '".venv\\Scripts\\python.exe" _audit_ml\\Z276_preregistered_verdict.py >> "logs\\audit_suite_daily.log" 2>&1\n'
                      'echo exit=%ERRORLEVEL%')
    for a, b in (("[4/5]", "[4/6]"), ("[5/5]", "[5/6]")):
        src = src.replace(a, b)
    cmd.write_text(src, encoding="utf-8")
    print("已挂第 6 步")
print("已写入 §94")
