# -*- coding: utf-8 -*-
"""Z102: §24 #12 —— 71 份配置读取助手里，哪些**真的**会吞掉显式 0/False？

判定纪律（沿用 §50.3）：`or default` 只有在**左值已被解析为 int/float/bool** 时才有害；
若左值是 `os.getenv(...)` 返回的**字符串**，`"0"`/`"false"` 均为真值 → 无害。
本脚本逐条打印危险助手的实现与调用点计数，供人工核验。
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "scripts"))
import audit_config_effective as ace  # noqa: E402

rep = ace.build_report(ROOT)
helpers = rep["cfg_helpers"]
danger = [h for h in helpers if h["dangerous"]]
print(f"助手总数 {len(helpers)}；标记危险 {len(danger)}")
print()
for h in danger:
    print("=" * 100)
    print(f"{h['file']}   fn={h['fn']}")
    print("   body:")
    for line in (h["body"] or "").splitlines():
        print("      " + line.strip()[:150])

# 每个助手的调用点（按函数名在 backend/ 内搜索）
py_files = [p for p in (ROOT / "backend").rglob("*.py") if ".venv" not in str(p)]
texts = {}
for p in py_files:
    try:
        texts[p] = p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        pass

print("\n\n=== 危险助手的调用点（生产代码，排除 tests）===")
for h in danger:
    fn = h["fn"]
    defs = f"{h['file'].split(':')[0]}"
    hits = []
    for p, t in texts.items():
        rel = str(p.relative_to(ROOT))
        if "tests" in p.parts:
            continue
        for i, line in enumerate(t.splitlines(), 1):
            if re.search(rf"(?<![\w.]){fn}\s*\(", line) and "def " not in line:
                hits.append(f"{rel}:{i}")
    print(f"  {fn:<20} 定义于 {defs:<52} 生产调用 {len(hits)} 处")
    for x in hits[:6]:
        print(f"        {x}")
