"""Why does the roundtrip-log analysis silently drop ~27% of entries?

t60_counterfactual.py reported 798 roundtrips while the file has 1093 lines.
That is a silent 27% data loss in the very analysis that chose T60's threshold,
so it must be explained before the conclusion can be trusted.
"""
from __future__ import annotations

import io
import json
import sys
from collections import Counter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
LOG = r"D:\001Alpha\Hyper-Alpha-Arena\data\flow_roundtrip_log.jsonl"

rows = []
bad = 0
for ln in open(LOG, encoding="utf-8", errors="replace"):
    ln = ln.strip()
    if not ln:
        continue
    try:
        rows.append(json.loads(ln))
    except Exception:  # noqa: BLE001
        bad += 1

print("=" * 90)
print("往返日志的字段完整性")
print("=" * 90)
print(f"  可解析条数   = {len(rows)}")
print(f"  无法解析行数 = {bad}")
key_ct = Counter()
for d in rows:
    for k in d:
        key_ct[k] += 1
print()
print(f"  {'key':<18}{'present':>9}{'missing':>9}")
for k, v in sorted(key_ct.items(), key=lambda x: -x[1]):
    flag = "   <-- 部分缺失" if v < len(rows) else ""
    print(f"  {k:<18}{v:>9}{len(rows)-v:>9}{flag}")

no_h = [d for d in rows if d.get("hold_sec") is None]
no_y = [d for d in rows if d.get("y_bp") is None]
print()
print(f"  缺 hold_sec = {len(no_h)}")
print(f"  缺 y_bp     = {len(no_y)}")
if no_h:
    print()
    print("  缺 hold_sec 的样本（前 3 条）：")
    for d in no_h[:3]:
        print("   ", json.dumps(d, ensure_ascii=False)[:170])
    print()
    c = Counter(str(d.get("why")) for d in no_h)
    print("  按 exit_path 分布：")
    for k, v in c.most_common(8):
        print(f"    {k:<34}{v:>5}")
    use = [d for d in rows if d.get("hold_sec") is not None]
    c2 = Counter(str(d.get("why")) for d in use)
    print()
    print("  对照：有 hold_sec 的样本按 exit_path 分布：")
    for k, v in c2.most_common(8):
        print(f"    {k:<34}{v:>5}")
