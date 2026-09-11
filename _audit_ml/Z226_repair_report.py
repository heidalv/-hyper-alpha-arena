# -*- coding: utf-8 -*-
"""[§82 事故恢复 2026-09-11] 还原报告：mbcs(cp936) 反解 → 写回 UTF-8，并做**上下文频率插补**。

事故根因：用 `(Get-Content -Raw) -replace … | Set-Content -Encoding utf8` 编辑 UTF-8 报告，
PowerShell 以系统 ANSI(cp936) 误读、再以 UTF-8 写回 ⇒ 文本 = UTF-8(cp936(原始UTF-8))。
反解 = `text.encode('mbcs')`；其中被 ANSI 解码器替换成 `?`（0x3F）的**单个字节**不可逆，
表现为一个 U+FFFD（本文件约 10.6k 处，绝大多数是全角标点）。

本脚本：
  ① 备份损坏版本（`.corrupt_*`）；
  ② 反解并写出 UTF-8（保留 U+FFFD 占位）；
  ③ 用"上下文频率插补"修一批：对每个 U+FFFD，在**完好文本**里统计 `prev + X + next`
     出现次数最高的全角/中文候选字符 X（要求 3 字节、非 ASCII），唯一最优才替换；
  ④ 输出修复统计 + 结构自检（清单行数、§62 标题）。
"""
from __future__ import annotations

import shutil
import sys
import time
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
P = Path(r"D:\001Alpha\Hyper-Alpha-Arena\_中长线负期望根因报告_20260909.md")

raw = P.read_bytes()
text = raw.decode("utf-8")
if text.startswith("\ufeff"):
    text = text[1:]
repaired = text.encode("mbcs").decode("utf-8", errors="replace")

bak = P.with_name(P.name + f".corrupt_{time.strftime('%Y%m%d_%H%M%S')}")
shutil.copy2(P, bak)
print("损坏版本备份:", bak.name)

# 候选字符：完好文本里出现过的、非 ASCII 的字符（3 字节居多）
intact_pairs: Counter = Counter()
CH = "\ufffd"
for i, ch in enumerate(repaired):
    if ch == CH:
        continue
    prev = repaired[i - 1] if i else ""
    nxt = repaired[i + 1] if i + 1 < len(repaired) else ""
    if prev and nxt:
        intact_pairs[(prev, ch, nxt)] += 1

cand = Counter(ch for ch in repaired if ch != CH and ord(ch) > 0x2000)
print(f"候选字符 {len(cand)} 个｜完整三元组 {len(intact_pairs)} 种")

fixed = 0
out_chars = []
i = 0
n = len(repaired)
while i < n:
    if repaired[i] != CH:
        out_chars.append(repaired[i])
        i += 1
        continue
    prev = out_chars[-1] if out_chars else ""
    nxt = repaired[i + 1] if i + 1 < n else ""
    best, best_n = "", 0
    tie = False
    for x in cand:
        c = intact_pairs.get((prev, x, nxt), 0)
        if c > best_n:
            best, best_n, tie = x, c, False
        elif c == best_n and c > 0:
            tie = True
    if best and best_n >= 1 and not tie:
        out_chars.append(best)
        fixed += 1
    else:
        out_chars.append(CH)
    i += 1

result = "".join(out_chars)
left = result.count(CH)
print(f"上下文插补修复 {fixed} 处；剩余占位 {left} 处（{100.0*left/max(1,n):.2f}% 字符）")

P.write_text(result, encoding="utf-8")
print("已写回:", P.name, "行数 =", result.count("\n") + 1)
rows = [l for l in result.splitlines() if l.startswith("| ") and l.count("|") >= 6]
print("表格行数:", len(rows))
print("§62 标题存在:", "## 62. " in result, "| §82 标题存在:", "## 82. " in result)
