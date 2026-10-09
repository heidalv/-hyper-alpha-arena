# -*- coding: utf-8 -*-
"""扫描 `isinstance(x, dict)` 用在 **SQLAlchemy 映射行** 上的同类反模式（只读）。

F387 的形态：`r` 来自 `.mappings().all()`（`RowMapping`，是 `Mapping` 但**不是** `dict`），
却写成 `... if isinstance(r, dict) else ""` ⇒ 该分支永远不成立 ⇒ 字段恒空且静默。

扫法（不依赖运行）：找出同一函数体内既出现 `.mappings()` 或 `mappings()`
又出现 `isinstance(<var>, dict)` 的片段，人工确认。
"""
from __future__ import annotations

import io
import re
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
SKIP = {".venv", "node_modules", "__pycache__", ".git", "logs"}

pat_map = re.compile(r"\.mappings\(\)|mappings\(\)\s*\.\s*all\(\)")
pat_isd = re.compile(r"isinstance\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*,\s*dict\s*\)")

hits = []
cands = []
for base in ("backend", "scripts"):
    cands.extend((ROOT / base).rglob("*.py"))
print(f"[扫描] 候选文件 {len(cands)} 个（限定 backend/ 与 scripts/；全仓 rglob 会超时）")
for py in cands:
    if any(s in py.parts for s in SKIP):
        continue
    try:
        src = py.read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        continue
    if not pat_map.search(src) or not pat_isd.search(src):
        continue
    lines = src.splitlines()
    # 粗略：把"函数块"当作 `def` 到下一个顶层 def 之间
    bounds = [i for i, ln in enumerate(lines) if re.match(r"^(def|async def|class)\s", ln)]
    bounds.append(len(lines))
    for a, b in zip(bounds, bounds[1:]):
        block = "\n".join(lines[a:b])
        if pat_map.search(block) and pat_isd.search(block):
            for m in pat_isd.finditer(block):
                ln_no = a + block[:m.start()].count("\n") + 1
                hits.append((str(py.relative_to(ROOT)), ln_no, m.group(0),
                             lines[ln_no - 1].strip()[:110]))

print("=" * 78)
print("同类反模式扫描：`isinstance(x, dict)` 与 `.mappings()` 同函数体")
print("=" * 78)
if not hits:
    print("  未发现同类片段")
for f, ln, frag, text in hits:
    print(f"  {f}:{ln}  {frag}")
    print(f"      {text}")
print(f"\n合计 {len(hits)} 处候选（需人工确认变量是否来自 mappings）")
