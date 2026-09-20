# -*- coding: utf-8 -*-
"""轮126 补：修 role 串里的嵌套双引号（SyntaxError）。"""
import io
import re

p = "backend/services/agent_wall.py"
s = io.open(p, encoding="utf-8", errors="surrogateescape", newline="").read()

# 找出所有以 "role": " 开头、且同一行内出现 >2 个 ASCII 双引号的行，把中间那对换成「」
lines = s.split("\n")
fixed = 0
for i, ln in enumerate(lines):
    st = ln.strip()
    if st.startswith('"role"') and ln.count('"') > 4:
        # 形如   "role": "...."xxx"....",
        head = ln.split('"role": "', 1)
        if len(head) == 2:
            body = head[1]
            # 末尾的 ",  之前的最后一个引号是结束引号
            end = body.rfind('"')
            inner = body[:end]
            tail = body[end:]
            if inner.count('"') >= 2:
                first = inner.find('"')
                last = inner.rfind('"')
                inner = inner[:first] + "「" + inner[first + 1:last] + "」" + inner[last + 1:]
                lines[i] = head[0] + '"role": "' + inner + tail
                fixed += 1
if fixed:
    io.open(p, "w", encoding="utf-8", errors="surrogateescape", newline="").write("\n".join(lines))
print(f"[OK] 修嵌套引号 {fixed} 处")
else:
    print("[--] 未发现需要修的行")
