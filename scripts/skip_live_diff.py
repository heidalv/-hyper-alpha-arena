# -*- coding: utf-8 -*-
"""2 分钟 skip 增量快照：看现在到底什么在拦单。"""
import json
import pathlib
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]


def snap():
    j = json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
    return {k: int(v) for k, v in (j.get("skip_counts") or {}).items()}, \
           int(j.get("quoted_decisions") or 0), int(j.get("ticks") or 0)


s1 = snap()
time.sleep(120)
s2 = snap()
sk1, qd1, tk1 = s1
sk2, qd2, tk2 = s2
print(f"2min 内：ticks {tk2-tk1}，quote 决策 {qd2-qd1}，skip 增量：")
diff = {k: sk2.get(k, 0) - sk1.get(k, 0) for k in set(sk1) | set(sk2)}
tot = sum(v for v in diff.values() if v > 0)
for k, v in sorted(diff.items(), key=lambda x: -x[1]):
    if v != 0:
        print(f"  {k:<28} {v:>+5}")
print(f"  合计 +{tot} skip / {max(qd2-qd1,1)} quote 决策")
