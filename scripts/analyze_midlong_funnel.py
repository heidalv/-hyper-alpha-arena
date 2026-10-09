# -*- coding: utf-8 -*-
"""中线（MidLong）决策漏斗统计：否决理由分布、开仓次数、时间线。只读。

数据源：logs/backend.log（可指定扫描字节数）。
关注：`[MidLong] stage=... action=... reason=...` 与 `[MidLongBrain] skip open ...`
"""
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
nbytes = int(sys.argv[1]) if len(sys.argv) > 1 else 12_000_000

with open(path, "rb") as f:
    f.seek(0, 2)
    size = f.tell()
    f.seek(max(0, size - nbytes))
    lines = f.read().decode("utf-8", errors="replace").splitlines()

print("=" * 92)
print(f"扫描 backend.log 尾部 {len(lines):,} 行（{nbytes/1e6:.1f} MB / 共 {size/1e6:.1f} MB）")
print("=" * 92)

ts_re = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
act_re = re.compile(r"\[MidLong\] stage=(\w+) symbol=(\S+)(?:.*?action=(\w+))?")
rea_re = re.compile(r"reason=([^|]+)")

acts = Counter()
reasons = Counter()
reasons_by_act = defaultdict(Counter)
by_hour = defaultdict(Counter)
symbols = Counter()
brain_skip = Counter()
first_ts = last_ts = None

for ln in lines:
    m = ts_re.match(ln)
    if m:
        if first_ts is None:
            first_ts = m.group(1)
        last_ts = m.group(1)
        hour = m.group(1)[:13]
    else:
        hour = hour if "hour" in dir() else "?"

    if "[MidLong] stage=" in ln:
        a = act_re.search(ln)
        if a:
            stage, sym, act = a.group(1), a.group(2), a.group(3) or "?"
            acts[act] += 1
            symbols[sym] += 1
            by_hour[hour][act] += 1
            r = rea_re.search(ln)
            if r:
                key = r.group(1).strip()[:70]
                reasons[key] += 1
                reasons_by_act[act][key] += 1

    if "[MidLongBrain] skip open" in ln:
        r = rea_re.search(ln)
        brain_skip[(r.group(1).strip()[:70] if r else "?")] += 1

    if "opened=True" in ln and "FactorRoute" in ln:
        acts["__factorroute_opened"] += 1

print(f"时间跨度: {first_ts} → {last_ts}\n")

print("【1】action 分布")
for k, v in acts.most_common():
    print(f"   {k:32s} {v}")

print("\n【2】reason 分布（全部 action 合计，前 25）")
for k, v in reasons.most_common(25):
    print(f"   {v:6d}  {k}")

print("\n【3】按 action 拆 reason（只看 open/buy/enter 类，若有）")
for act in ("open", "buy", "enter", "long", "short"):
    if reasons_by_act.get(act):
        print(f"   --- action={act} ---")
        for k, v in reasons_by_act[act].most_common(10):
            print(f"      {v:6d}  {k}")

print("\n【4】[MidLongBrain] skip open 理由")
for k, v in brain_skip.most_common(15):
    print(f"   {v:6d}  {k}")

print("\n【5】按小时 × action（最近 14 小时）")
for h in sorted(by_hour)[-14:]:
    tot = sum(by_hour[h].values())
    detail = " ".join(f"{a}={n}" for a, n in by_hour[h].most_common())
    print(f"   {h}  合计{tot:5d}   {detail}")

print("\n【6】出现过的 symbol（前 15）")
print("   " + "  ".join(f"{s}:{n}" for s, n in symbols.most_common(15)))
