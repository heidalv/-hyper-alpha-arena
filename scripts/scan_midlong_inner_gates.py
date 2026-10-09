# -*- coding: utf-8 -*-
"""查"exec 决定 buy 但没有成交"死在哪个内部闸。只读。"""
from __future__ import annotations

import io
import re
import sys
from collections import Counter
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
p = ROOT / "logs" / "backend.log"
with open(p, "rb") as f:
    f.seek(0, 2)
    size = f.tell()
    f.seek(max(0, size - 25_000_000))
    lines = f.read().decode("utf-8", errors="replace").splitlines()

TAGS = ["[MidLongMTF]", "[FixedSymbolGate]", "[MidLongFunding]", "[MidLongPortfolio]",
        "[TierCircuit]", "[MidLongCooldown]", "[PullbackEntry]", "[AIStrat]",
        "[MidLongStructureSL]", "[MidLongATR]", "[MidLongSL]", "[MidLongTradeDesign]",
        "[MidLongFactorIC]", "[MidLongAudit]", "[MidLongCfg]", "[MidLong][TTL]",
        "[MidLongBrain]", "[MidLong][Open]", "[MidLong] 开仓", "notional", "名义",
        "预算", "margin", "保证金不足"]

cnt = Counter()
samples = {}
for ln in lines:
    for t in TAGS:
        if t in ln:
            cnt[t] += 1
            samples.setdefault(t, ln)
            break

print("=" * 96)
print(f"扫描 {len(lines):,} 行")
print("=" * 96)
print(f"\n{'标签':28s} {'命中':>7s}   样例")
for t, n in cnt.most_common():
    print(f"{t:28s} {n:7d}   {samples[t][:150]}")

print("\n--- 最近 30 条含 BLOCK / 拒绝 / 不足 的中线相关行 ---")
rx = re.compile(r"(BLOCK|拒绝|不足|超限|不允许|skip|跳过)")
sel = [ln for ln in lines if ("MidLong" in ln or "开仓" in ln) and rx.search(ln)]
for ln in sel[-30:]:
    print("   " + ln[:210])
