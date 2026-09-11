# -*- coding: utf-8 -*-
"""Z189（P9 只读分析）：`.env` 里的乱码注释能否**可逆还原**。

判定：文件是合法 UTF-8（无替换字符），但部分中文疑似"UTF-8 字节被按 GBK 解读后再存成 UTF-8"
（经典双重编码）。若如此，`line.encode('gbk').decode('utf-8')` 应还原出原中文。

本脚本**只读**：统计可疑行、试还原、报告成功率与样例；不改文件。
"""
from __future__ import annotations

import io
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
ENV = ROOT / ".env"
raw = ENV.read_bytes()
text = raw.decode("utf-8-sig")  # 保留原始 \r\n
lines = text.split("\n")

# 可疑判定：含 CJK 但出现"乱码高频字"（GBK 误读的典型产物）
SUSPECT = set("锛鏄涓绾鍜屽彲鑳芥槸鎴戜滑鐨勪簡鍦ㄤ笉鏈変负浠ヤ娇浣犱粬濂逛滑杞彉鍖栧弬鏁伴檺鍒舵帶鍒堕槻姝㈤渶瑕佸凡缁忓洜涓烘墍浠ュ杩樻湁鎵€浠ュ洜涓")
suspicious = []
for i, ln in enumerate(lines, 1):
    if not ln.strip() or not ln.strip().startswith("#"):
        continue
    if any(ch in SUSPECT for ch in ln):
        suspicious.append(i)

print(f".env 行数 = {len(lines)}；注释行里的可疑乱码行 = {len(suspicious)}")
print("前 5 个可疑行号:", suspicious[:5])

ok = bad = 0
samples = []
for i in suspicious[:40]:
    ln = lines[i - 1]
    try:
        fixed = ln.encode("gbk").decode("utf-8")
        ok += 1
        if len(samples) < 6:
            samples.append((i, ln[:70], fixed[:70]))
    except Exception:
        bad += 1
print(f"\n试还原（前 40 行）：成功 {ok} / 失败 {bad}")
for i, before, after in samples:
    print(f"  [{i}] 原: {before}")
    print(f"       还原: {after}")

# 全量可逆性统计
ok_all = fail_all = 0
for i in suspicious:
    try:
        lines[i - 1].encode("gbk").decode("utf-8")
        ok_all += 1
    except Exception:
        fail_all += 1
print(f"\n全量：可还原 {ok_all} / 不可还原 {fail_all}")

# key=value 解析（确认值不含乱码、恢复只动注释）
kv = {}
for ln in lines:
    s = ln.strip()
    if not s or s.startswith("#") or "=" not in s:
        continue
    k, _, v = s.partition("=")
    kv[k.strip()] = v.split("#")[0].strip()
bad_values = {k: v for k, v in kv.items() if any(ch in SUSPECT for ch in v)}
print(f"\n键值对总数 = {len(kv)}；其中**值**含乱码字的 = {len(bad_values)} -> {list(bad_values)[:5]}")
print("⇒ 若为 0，说明乱码只影响注释，恢复注释不改任何生效值。")
