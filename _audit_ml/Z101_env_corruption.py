# -*- coding: utf-8 -*-
"""Z101: .env 注释乱码取证 —— 是文件里真的写入了 '?'，还是读取端解码问题？

判定要点：
  1. 原始字节里 '?'(0x3F) 的连续长度 → 真损坏；
  2. 是否存在 U+FFFD(EF BF BD) → 解码失败的替代符（另一种损坏形态）；
  3. **非注释行**（=实际生效的 KEY=VALUE）是否也有损坏 → 决定严重度；
  4. 是否存在可用的 .env 备份（用于恢复中文）。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
p = ROOT / ".env"
raw = p.read_bytes()
text = raw.decode("utf-8", errors="replace")
lines = text.splitlines()

q_runs = [len(m.group(0)) for m in re.finditer(r"\?{5,}", text)]
print(f"文件大小 {len(raw)} 字节 / {len(lines)} 行")
print(f"连续 '?'(0x3F) 片段数 {len(q_runs)}，最长 {max(q_runs) if q_runs else 0}")
print(f"含 U+FFFD 替代符的行数: {text.count(chr(0xFFFD))}")

comment_bad, active_bad = [], []
for i, line in enumerate(lines, 1):
    if not re.search(r"\?{5,}", line):
        continue
    s = line.strip()
    (comment_bad if s.startswith("#") or not s or "=" not in s else active_bad).append((i, line))

print(f"\n注释/空行损坏: {len(comment_bad)} 行")
print(f"**非注释（KEY=VALUE）损坏: {len(active_bad)} 行**")
for i, line in active_bad[:15]:
    print(f"   {i}: {line[:140]}")

print("\n备份候选:")
for pat in ("env.*backup*", ".env.*", "*env*.bak*", "env.framework*"):
    for f in ROOT.glob(pat):
        try:
            b = f.read_bytes()
            t = b.decode("utf-8", errors="replace")
            good = len(re.findall(r"[\u4e00-\u9fff]", t))
            print(f"   {f.name:<46} {len(b):>7}B  中文行数={good}")
        except Exception as e:
            print(f"   {f.name}: 读取失败 {str(e)[:60]}")
