# -*- coding: utf-8 -*-
"""中线：stage 分布 + exec 前的最后一道闸 + 实盘/纸面开仓证据。只读。"""
from __future__ import annotations

import io
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "logs" / "backend.log"
nbytes = int(sys.argv[1]) if len(sys.argv) > 1 else 20_000_000
with open(path, "rb") as f:
    f.seek(0, 2)
    size = f.tell()
    f.seek(max(0, size - nbytes))
    lines = f.read().decode("utf-8", errors="replace").splitlines()

stage = Counter()
exec_lines = []
buy_lines = []
mid_open = []
other_gate = Counter()

st_re = re.compile(r"\[MidLong\] stage=(\w+)")
for ln in lines:
    m = st_re.search(ln)
    if m:
        stage[m.group(1)] += 1
        if m.group(1) == "exec":
            exec_lines.append(ln)
    if "[MidLong]" in ln and "action=buy" in ln:
        buy_lines.append(ln)
    if re.search(r"\[Paper\].*(mid|中线)|tier=mid.*open|开仓.*tier=mid", ln):
        mid_open.append(ln)
    for g in ("swing_consensus", "chart_gate", "midlong_circuit", "midlong_open_halted",
              "authority_block", "margin_zero", "cycle_conflict", "regime_block",
              "v2_gate", "learned_gate", "micro"):
        if g in ln:
            other_gate[g] += 1

print("=" * 92)
print(f"扫描 {len(lines):,} 行")
print("=" * 92)
print("\n【1】[MidLong] stage 分布")
for k, v in stage.most_common():
    print(f"   {k:12s} {v}")

print(f"\n【2】stage=exec 行数 = {len(exec_lines)}")
for ln in exec_lines[-15:]:
    print("   " + ln[:230])

print(f"\n【3】action=buy 行数 = {len(buy_lines)}（最近 10）")
for ln in buy_lines[-10:]:
    print("   " + ln[:230])

print(f"\n【4】其它闸门关键词命中")
for k, v in other_gate.most_common():
    print(f"   {k:24s} {v}")

print(f"\n【5】疑似中线开仓行（{len(mid_open)}）")
for ln in mid_open[-10:]:
    print("   " + ln[:230])
