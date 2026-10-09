# -*- coding: utf-8 -*-
"""扫日志尾部，找"中线开仓被否决/冻结"的实际原因。只读。"""
from __future__ import annotations

import io
import re
import sys
from collections import Counter
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
TAIL_BYTES = 3_000_000

KW = ["冻结", "frozen", "freeze", "disabled_natures", "否决", "拒绝", "reject", "veto",
      "gate", "闸", "blocked", "不允许", "开仓被", "skip_open", "no_open", "halt",
      "mid", "中线", "swing", "nature"]

def tail_lines(p: Path, nbytes: int):
    with open(p, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - nbytes))
        data = f.read().decode("utf-8", errors="replace")
    return data.splitlines()

for name in ("backend.log", "brain_subprocess.log", "backend.error.log"):
    p = ROOT / "logs" / name
    if not p.exists():
        continue
    lines = tail_lines(p, TAIL_BYTES)
    print("=" * 90)
    print(f"{name}  尾部 {len(lines)} 行")
    print("=" * 90)
    hits = [ln for ln in lines if any(k in ln for k in KW)]
    print(f"命中关键词行数: {len(hits)}")
    # 归类：把日志级别与消息主干提出来计数
    pats = {
        "Parity冻结": r"Parity|parity",
        "disabled_natures": r"disabled_natures",
        "FreezeCoordinator": r"Freeze|freeze_coordinator|冻结台账",
        "风控/gate否决": r"\[(Gate|Risk|Guard|Budget|Veto|闸)\]",
        "中线/mid": r"mid|中线|MIDLONG",
        "拒绝/否决": r"拒绝|否决|reject|veto|blocked|不允许",
    }
    for label, pat in pats.items():
        rx = re.compile(pat, re.I)
        sub = [ln for ln in hits if rx.search(ln)]
        print(f"  {label:20s} {len(sub)}")
    print("\n--- 最近 40 条命中（去重前缀）---")
    for ln in hits[-40:]:
        print("   " + ln[:220])
