# -*- coding: utf-8 -*-
"""诊断：报告文件"对乱码再编码"事故的可逆性与可用编解码器。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
P = Path(r"D:\001Alpha\Hyper-Alpha-Arena\_中长线负期望根因报告_20260909.md")
raw = P.read_bytes()
print("文件字节数:", len(raw), "| 开头:", raw[:6])

text = raw.decode("utf-8")
if text.startswith("\ufeff"):
    text = text[1:]
    print("已剥离 BOM")

for codec in ("cp936", "gbk", "gb18030", "mbcs", "big5"):
    try:
        b = text.encode(codec)
        try:
            b.decode("utf-8")
            print(f"  [{codec}] ✅ 可逆且还原后是合法 UTF-8（字节数 {len(b)}）")
        except UnicodeDecodeError as exc:
            print(f"  [{codec}] 可编码，但还原后不是合法 UTF-8: {exc}")
    except UnicodeEncodeError as exc:
        print(f"  [{codec}] ❌ 有 {1} 处无法编码，首个位置 {exc.start}，字符 {text[exc.start]!r} (U+{ord(text[exc.start]):04X})")
    except LookupError:
        print(f"  [{codec}] 编解码器不可用")

# 统计可疑字符（PUA / 替换符）
from collections import Counter
odd = Counter(ch for ch in text if 0xE000 <= ord(ch) <= 0xF8FF or ch in "\ufffd")
print("PUA/替换符统计:", {f"U+{ord(k):04X}": v for k, v in odd.most_common(8)})
# 统计 `?`（可能表示不可逆损失）
print("'?' 数量:", text.count("?"))
print("样例（前 200 字）:", repr(text[:200]))
