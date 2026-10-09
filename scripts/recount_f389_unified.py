# -*- coding: utf-8 -*-
"""F389 统一口径重算：**同一窗口**内所有计数（避免混用窗口，纪律 23）。只读。"""
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
NB = 36_000_000
with open(p, "rb") as f:
    f.seek(0, 2)
    size = f.tell()
    f.seek(max(0, size - NB))
    lines = f.read().decode("utf-8", errors="replace").splitlines()

ts = [ln[:19] for ln in lines if re.match(r"^\d{4}-\d{2}-\d{2}", ln)]
print("=" * 96)
print(f"统一窗口：backend.log 末 {len(lines):,} 行（{min(NB, size)/1e6:.1f} MB / 共 {size/1e6:.1f} MB）")
print(f"时间跨度：{ts[0] if ts else '?'} → {ts[-1] if ts else '?'}")
print("=" * 96)

stage = Counter()
act = Counter()
reason = Counter()
lg_total = 0
lg_pct = Counter()
sb_total = 0
cd_total = 0
scan = Counter()
brain_skip = Counter()

re_stage = re.compile(r"\[MidLong\] stage=(\w+)")
re_act = re.compile(r"action=(\w+)")
re_reason = re.compile(r"reason=([^|\n]+)")
re_pct = re.compile(r"分位(\d+)%")

for ln in lines:
    m = re_stage.search(ln)
    if m:
        stage[m.group(1)] += 1
        a = re_act.search(ln)
        if a:
            act[a.group(1)] += 1
        r = re_reason.search(ln)
        if r:
            reason[r.group(1).strip()[:56]] += 1
    if "location_gate_veto" in ln:
        lg_total += 1
        pm = re_pct.search(ln)
        if pm:
            lg_pct[int(pm.group(1))] += 1
    if "midlong_short_regime_block" in ln:
        sb_total += 1
    if "[MidLongCooldown] BLOCK" in ln:
        cd_total += 1
    if "开仓扫描" in ln:
        sm = re.search(r"候选=(\d+) 成交=(\d+)", ln)
        if sm:
            scan[(sm.group(1), sm.group(2))] += 1
    if "[MidLongBrain] skip open" in ln:
        rm = re.search(r"reason=([^\s]+)", ln)
        if rm:
            brain_skip[rm.group(1)] += 1

print("\n【stage】", dict(stage))
print("【action（仅 stage 行内）】", dict(act))
print(f"\n【位置闸】location_gate_veto 总计 = {lg_total}")
over = {p_: n for p_, n in lg_pct.items() if p_ > 100}
n_over = sum(over.values())
print(f"   带分位的 = {sum(lg_pct.values())}   >100% = {n_over} "
      f"({n_over/max(1,sum(lg_pct.values())):.1%})   分布(>100%) = {dict(sorted(over.items()))}")
print(f"   分位区间 min={min(lg_pct) if lg_pct else '-'} max={max(lg_pct) if lg_pct else '-'}")
print(f"【空头闸】midlong_short_regime_block = {sb_total}")
print(f"【冷却】[MidLongCooldown] BLOCK = {cd_total}")
print("\n【开仓扫描 候选/成交】")
for (c, f_), n in scan.most_common(10):
    print(f"   候选={c:>3s} 成交={f_:>3s}   出现 {n} 次")
print("\n【主脑 skip open 理由】")
for k, v in brain_skip.most_common(12):
    print(f"   {v:5d}  {k}")
print("\n【reason 前 8】")
for k, v in reason.most_common(8):
    print(f"   {v:5d}  {k}")
