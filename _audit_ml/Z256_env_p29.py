# -*- coding: utf-8 -*-
"""[§88 执行 2026-09-11 / 决策 P29-A+B] 写入 long 层两个参数：
   * `MIDLONG_SL_MAX_PCT_LONG=0.03`（SL 距离上限，新增；0=关闭）
   * `PC_RISK_PER_TRADE_PCT_LONG` 0.0125 → 0.0075（对齐 mid）
保留 BOM/CRLF，改前备份，改后打印生效值。
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
env = Path(r"D:\001Alpha\Hyper-Alpha-Arena\.env")
text = env.read_bytes().decode("utf-8-sig")
bak = env.with_name(env.name + f".bak_p29_{time.strftime('%Y%m%d_%H%M%S')}")
shutil.copy2(env, bak)
print("备份:", bak.name)

lines = text.splitlines()
changed = []
for i, l in enumerate(lines):
    if l.startswith("PC_RISK_PER_TRADE_PCT_LONG="):
        lines[i] = "PC_RISK_PER_TRADE_PCT_LONG=0.0075"
        changed.append(("PC_RISK_PER_TRADE_PCT_LONG", l, lines[i]))
if not any(l.startswith("MIDLONG_SL_MAX_PCT_LONG=") for l in lines):
    lines.append("# [§88 执行 2026-09-11 / 决策 P29-A] long 层 SL 距离上限（§87 审计：SL 6.52% vs MAE 1.41%）；0=关闭")
    lines.append("MIDLONG_SL_MAX_PCT_LONG=0.03")
    changed.append(("MIDLONG_SL_MAX_PCT_LONG", "(none)", "MIDLONG_SL_MAX_PCT_LONG=0.03"))

env.write_bytes(b"\xef\xbb\xbf" + ("\r\n".join(lines) + "\r\n").encode("utf-8"))
for k, before, after in changed:
    print(f"  {k}: {before[:60]} → {after}")
keys = [l for l in env.read_bytes().decode("utf-8-sig").splitlines()
        if l.strip() and not l.strip().startswith("#") and "=" in l]
print("键数:", len(keys), "| BOM:", env.read_bytes()[:3] == b"\xef\xbb\xbf")
