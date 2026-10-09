# -*- coding: utf-8 -*-
"""F389 完整取证抽取：把三条链路的原始日志逐条抽出，写成可直接引用的证据文件。只读。"""
from __future__ import annotations

import io
import re
import sys
from collections import Counter
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "logs" / "backend.log"
OUT = ROOT / "logs" / "f389_evidence_20260918.txt"

with open(LOG, "rb") as f:
    f.seek(0, 2)
    size = f.tell()
    f.seek(max(0, size - 36_000_000))
    lines = f.read().decode("utf-8", errors="replace").splitlines()

def pick(pred, limit=200):
    out = []
    for ln in lines:
        if pred(ln):
            out.append(ln)
    return out[-limit:]

sec = []

sec.append("=" * 100)
sec.append("F389 取证：中线开仓被冻结（三闸互锁）——原始日志摘录")
sec.append(f"来源: logs/backend.log  扫描 {len(lines):,} 行  文件总 {size/1e6:.1f} MB")
sec.append("=" * 100)

# A. 冷却拦截
a = pick(lambda l: "[MidLongCooldown] BLOCK" in l, 400)
sec.append(f"\n\n### A. 冷却拦截全部 {len(a)} 条（看 30→120 分钟的演进）\n")
for ln in a:
    sec.append(ln)

# B. SL 上限
b = pick(lambda l: "[MidLongSL]" in l, 60)
sec.append(f"\n\n### B. 止损距离上限（mid cap=1.50%）共 {len(b)} 条，末 20\n")
for ln in b[-20:]:
    sec.append(ln)

# C. 开仓扫描 成交统计
c = pick(lambda l: "开仓扫描" in l, 60)
sec.append(f"\n\n### C. 开仓扫描（候选 vs 成交）共 {len(c)} 条，末 25\n")
for ln in c[-25:]:
    sec.append(ln)

# D. location_gate_veto（按 symbol 取末条）
d = pick(lambda l: "location_gate_veto" in l, 500)
per_sym = {}
for ln in d:
    m = re.search(r"symbol=(\S+)", ln)
    if m:
        per_sym[m.group(1)] = ln
sec.append(f"\n\n### D. 位置闸否决共 {len(d)} 条；每 symbol 末条\n")
for k, ln in sorted(per_sym.items()):
    sec.append(f"[{k}]\n{ln}")
sec.append("\n--- 分位分布（全部 location_gate_veto）---")
pcts = Counter()
for ln in d:
    m = re.search(r"分位(\d+)%", ln)
    if m:
        pcts[int(m.group(1))] += 1
for p in sorted(pcts):
    sec.append(f"   分位 {p:3d}%  {pcts[p]:4d} 次")
over100 = {p: n for p, n in pcts.items() if p > 100}
sec.append(f"   >100% 的不可能值: {over100}")

# E. 空头 regime 闸
e = pick(lambda l: "midlong_short_regime_block" in l, 300)
sec.append(f"\n\n### E. 空头 regime 闸共 {len(e)} 条，末 12\n")
for ln in e[-12:]:
    sec.append(ln)

# F. brain skip open
f = pick(lambda l: "[MidLongBrain] skip open" in l, 400)
sec.append(f"\n\n### F. 主脑 skip open 共 {len(f)} 条；按理由归类\n")
rc = Counter()
for ln in f:
    m = re.search(r"reason=([^\s]+)", ln)
    if m:
        rc[m.group(1)] += 1
for k, v in rc.most_common(20):
    sec.append(f"   {v:5d}  {k}")
sec.append("\n--- 末 15 条 ---")
for ln in f[-15:]:
    sec.append(ln)

# G. XRP 全生命周期（开/平/冷却）
g = [ln for ln in lines if "XRP" in ln and re.search(
    r"stage=exec|Cooldown|close|平仓|SL |TP ", ln)]
sec.append(f"\n\n### G. XRP 相关（exec/冷却/平仓）末 40 条\n")
for ln in g[-40:]:
    sec.append(ln)

# H. 各 stage 计数
h = Counter()
for ln in lines:
    m = re.search(r"\[MidLong\] stage=(\w+)", ln)
    if m:
        h[m.group(1)] += 1
sec.append(f"\n\n### H. [MidLong] stage 计数: {dict(h)}")

OUT.write_text("\n".join(sec), encoding="utf-8")
print(f"已写出 {OUT}  ({OUT.stat().st_size/1024:.1f} KB, {len(sec)} 行)")
print(f"  A 冷却={len(a)}  B SL={len(b)}  C 扫描={len(c)}  D 位置闸={len(d)}  "
      f"E 空头闸={len(e)}  F 主脑={len(f)}  G XRP={len(g)}")
