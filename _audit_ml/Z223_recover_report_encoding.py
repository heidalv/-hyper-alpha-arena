# -*- coding: utf-8 -*-
"""[§82 事故恢复 2026-09-11] 撤销 PowerShell 重编码事故：把报告文件从"UTF-8(乱码)"还原。

事故：用 `(Get-Content -Raw) -replace ... | Set-Content -Encoding utf8` 编辑报告，
PowerShell 以 GBK 误读 UTF-8 字节、再以 UTF-8 写回 ⇒ 变成"对乱码再编码"。
还原：把当前文本按 `gbk` 重新编码，即得原始 UTF-8 字节。
先备份损坏版本，再校验还原结果（含 §62 清单行数）。
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
P = Path(r"D:\001Alpha\Hyper-Alpha-Arena\_中长线负期望根因报告_20260909.md")

raw = P.read_bytes()
try:
    text = raw.decode("utf-8")
except UnicodeDecodeError as exc:
    print("当前文件不是合法 UTF-8，需另法处理:", exc)
    raise SystemExit(1)

try:
    restored = text.encode("gbk")
except UnicodeEncodeError as exc:
    bad = text[exc.start:exc.start + 20]
    print("存在 GBK 无法表示的字符（可能已被替换为 U+FFFD）:", repr(bad))
    restored = text.encode("gbk", errors="replace")

try:
    decoded = restored.decode("utf-8")
except UnicodeDecodeError as exc:
    print("还原后不是合法 UTF-8（有不可逆字节）:", exc)
    raise SystemExit(2)

bak = P.with_name(P.name + f".corrupt_{time.strftime('%Y%m%d_%H%M%S')}")
shutil.copy2(P, bak)
print("损坏版本备份:", bak.name)

P.write_bytes(restored)
print("已还原。行数 =", decoded.count("\n") + 1)
print("前 2 行:")
for line in decoded.splitlines()[:2]:
    print("   ", line[:90])
rows = [l for l in decoded.splitlines() if l.startswith("| ") and l.count("|") >= 6]
print("疑似清单行数 =", len(rows))
key = [l for l in decoded.splitlines() if l.startswith("| 66 |")]
print("第 66 行存在:", bool(key), (key[0][:80] if key else ""))
