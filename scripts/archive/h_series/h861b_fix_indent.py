# -*- coding: utf-8 -*-
"""[h861b] 精确修缩进:把 elif True: 下面的块整体缩进 4 空格。"""
import io
import sys
from pathlib import Path

p = Path(r"D:\001Alpha\Hyper-Alpha-Arena\backend\services\market_maker\runner.py")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)
lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
# 找到 "elif True:" 行,把它之后直到 "if not _gate.get(\"allow\"):" 之前的所有行缩进 4
start = None
for i, l in enumerate(lines):
    if l.strip() == "elif True:":
        start = i
        break
if start is None:
    print("未找到 elif True:")
    raise SystemExit(1)
end = None
for j in range(start + 1, len(lines)):
    if lines[j].startswith("                    if not _gate.get(\"allow\"):"):
        end = j
        break
if end is None:
    print("未找到块结束")
    raise SystemExit(1)
n = 0
for k in range(start + 1, end):
    if lines[k].strip():
        lines[k] = "    " + lines[k]
        n += 1
p.write_text("".join(lines), encoding="utf-8")
print(f"已缩进 {n} 行(第 {start+2}~{end} 行)")
