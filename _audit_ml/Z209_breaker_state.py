# -*- coding: utf-8 -*-
"""Z209：通道熔断（`_breaker_shadow`）的**当前状态**与**查询点**。

问题：`master_running_close` 近 30 笔胜率 7%，若熔断生效它不该继续执行。
本脚本回答：
  1. 运行期 `_breaker_shadow` 里到底有哪些被标记的通道（键 = tier|reason）？
  2. 哪些"应 shadow"的通道**没有**出现在表里（说明从未被评估 / 键不匹配）？
  3. 生产代码里谁在查询它（调用点清单）。
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT / "backend"))

# ── 1. 运行期状态 ──
from backend.services.source_attribution import attribution  # noqa: E402

try:
    attribution._ensure_loaded()
except Exception as exc:  # noqa: BLE001
    print("_ensure_loaded 失败:", exc)

snap = attribution.snapshot() if hasattr(attribution, "snapshot") else {}
shadow = snap.get("breaker_shadow") or dict(getattr(attribution, "_breaker_shadow", {}) or {})
print(f"=== 1. 熔断表条目数: {len(shadow)} ===")
on = {k: v for k, v in shadow.items() if v}
off = {k: v for k, v in shadow.items() if not v}
print(f"  已 shadow（True）: {len(on)} 条")
for k in sorted(on)[:40]:
    print(f"    {k}")
print(f"  已评估但放行（False）: {len(off)} 条（前 10）")
for k in sorted(off)[:10]:
    print(f"    {k}")
persist = snap.get("persist_path") or getattr(attribution, "_path", None)
print(f"  持久化文件: {persist}")

# ── 2. 哪些"应 shadow"的通道不在表里 ──
print("\n=== 2. 生产代码里 exit_channel_shadow / _channel_shadowed 的查询点 ===")
pat = re.compile(r"exit_channel_shadow|_channel_shadowed")
for py in sorted((ROOT / "backend").rglob("*.py")):
    s = str(py).replace("\\", "/")
    if any(x in s for x in ("__pycache__", "_ai_gen_archive", "_ai_gen_quarantine")):
        continue
    txt = py.read_text(encoding="utf-8", errors="replace")
    for i, line in enumerate(txt.splitlines(), 1):
        if pat.search(line):
            tag = "测试" if "/tests/" in s else "生产"
            print(f"  [{tag}] {py.relative_to(ROOT)}:{i}: {line.strip()[:110]}")
