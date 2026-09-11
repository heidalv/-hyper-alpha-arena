# -*- coding: utf-8 -*-
"""Z64: §24#13 审计粒度缺口的真实数据核验（midlong_direction_audit.jsonl）。"""
from __future__ import annotations
import json, os, time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
p = Path(os.getenv("MIDLONG_DIRECTION_AUDIT_PATH", ROOT / "data" / "midlong_direction_audit.jsonl"))
print("审计文件:", p, "存在:", p.exists(), "大小(MB):", round(p.stat().st_size/1e6, 2) if p.exists() else 0)

rows = []
with p.open(encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
print("总行数:", len(rows))
if not rows:
    raise SystemExit(0)

now = time.time()
def window(days):
    lo = now - days * 86400
    return [r for r in rows if float(r.get("epoch") or 0) >= lo]

for days in (1, 3, 7, 30):
    w = window(days)
    if not w:
        continue
    oc = Counter(str(r.get("outcome") or "?") for r in w)
    sk = [r for r in w if str(r.get("outcome")) == "skip"]
    rc = Counter(str(r.get("reason") or "?").split("(")[0].split(":")[0][:60] for r in sk)
    gen = sum(v for k, v in rc.items() if k == "evaluate_and_execute_returned_false")
    print(f"\n--- 近 {days} 天: 行数={len(w)} outcome={dict(oc)} skip={len(sk)}")
    if sk:
        print(f"    通用原因占比 evaluate_and_execute_returned_false = {gen}/{len(sk)} = {gen/len(sk):.1%}")
        for k, v in rc.most_common(12):
            print(f"      {v:>6}  {v/len(sk):>6.1%}  {k}")

print("\n--- stage 分布（全部历史）---")
print(dict(Counter(str(r.get("stage") or "?") for r in rows)))
print("\n--- 近 7 天 exec 阶段 reason 明细（前 20）---")
w7 = window(7)
ex = [r for r in w7 if str(r.get("stage")) == "exec" and str(r.get("outcome")) == "skip"]
for k, v in Counter(str(r.get("reason") or "?")[:80] for r in ex).most_common(20):
    print(f"  {v:>6}  {k}")

print("\n--- 是否有 extra 字段（可承载细粒度原因）---")
ne = sum(1 for r in rows if isinstance(r.get("extra"), dict) and r.get("extra"))
print("  含 extra 的行:", ne, "/", len(rows))
