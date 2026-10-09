# -*- coding: utf-8 -*-
"""当前拦截构成（2min 增量）+ ar300_hist 是否真的在累积（原始状态文件键）。只读。"""
import json
import pathlib
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
st = ROOT / "logs" / "mm_lane_status.json"


def rd():
    return json.loads(st.read_text(encoding="utf-8"))


j1 = rd()
bn = (j1.get("states") or {}).get("BNB") or {}
print("BNB state 键：", sorted(bn.keys()))
print("ar300_hist 长度 =", len(bn.get("ar300_hist") or []))
k1 = {k: int(v) for k, v in (j1.get("skip_counts") or {}).items()}
q1 = int(j1.get("quoted_decisions") or 0)
f1 = int(j1.get("fills") or 0)
time.sleep(120)
j2 = rd()
k2 = {k: int(v) for k, v in (j2.get("skip_counts") or {}).items()}
q2 = int(j2.get("quoted_decisions") or 0)
f2 = int(j2.get("fills") or 0)
diff = {k: k2.get(k, 0) - k1.get(k, 0) for k in set(k1) | set(k2)}
print(f"\n2min：quote 决策 +{q2-q1}，fills +{f2-f1}")
print("skip 增量：")
for k, v in sorted(diff.items(), key=lambda x: -x[1]):
    if v:
        print(f"  {k:<26} {v:>+5}")
