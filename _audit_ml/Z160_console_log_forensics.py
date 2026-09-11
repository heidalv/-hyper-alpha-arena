# -*- coding: utf-8 -*-
"""Z160: `logs/backend-console.log`（540MB，无轮转）里到底是谁在刷屏？

只读文件尾部 4MB（避免把 540MB 读进内存），统计行的前缀/来源分布。
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
TAIL_BYTES = 4 * 1024 * 1024


def tail_lines(p: Path, nbytes: int) -> list[str]:
    size = p.stat().st_size
    with p.open("rb") as f:
        f.seek(max(0, size - nbytes))
        data = f.read()
    return data.decode("utf-8", errors="replace").splitlines()


for name in ["backend-console.log", "backend-console.err.log"]:
    p = ROOT / "logs" / name
    if not p.exists():
        print(f"{name}: 不存在")
        continue
    lines = tail_lines(p, TAIL_BYTES)
    print(f"\n=== {name}  大小 {p.stat().st_size/1024/1024:.1f}MB  尾部 {len(lines)} 行 ===")
    print("--- 最后 12 行 ---")
    for ln in lines[-12:]:
        print("  " + ln[:160])

    # 归一化前缀：去掉时间/数字/符号，取前 40 字符
    norm = Counter()
    src = Counter()
    for ln in lines:
        s = re.sub(r"\d", "#", ln.strip())[:40]
        norm[s] += 1
        m = re.search(r"\b([\w./\\-]+\.py)\b", ln)
        if m:
            src[m.group(1).split("\\")[-1].split("/")[-1]] += 1
    print("--- 高频行前缀 TOP8 ---")
    for k, v in norm.most_common(8):
        print(f"  {v:5d}  {k}")
    print("--- 出现最多的 .py 来源 TOP8 ---")
    for k, v in src.most_common(8):
        print(f"  {v:5d}  {k}")
