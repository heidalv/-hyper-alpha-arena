# -*- coding: utf-8 -*-
"""轮126 补2：把 role 串里那对嵌套 ASCII 双引号换成「」。"""
import io

p = "backend/services/agent_wall.py"
s = io.open(p, encoding="utf-8", errors="surrogateescape", newline="").read()
old = '**这正是"中线与长线最后完全不同"的落点之一**'
new = "**这正是「中线与长线最后完全不同」的落点之一**"
if old in s:
    s = s.replace(old, new, 1)
    io.open(p, "w", encoding="utf-8", errors="surrogateescape", newline="").write(s)
    print("[OK] 嵌套引号已改为「」")
else:
    print("[--] 未找到目标串（可能已是全角）")
