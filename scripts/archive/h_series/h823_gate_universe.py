# -*- coding: utf-8 -*-
"""[h823] 看门 + 把宇宙对齐到"成交活跃 + 门开着"的币。"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")

g = json.loads((ROOT / "data" / "flow_gate_last.json").read_text(encoding="utf-8"))
print(f"门 ts={g.get('as_of')} 协议={g.get('protocol')}")
opened, closed = [], []
for k, v in (g.get("gates") or {}).items():
    oos = v.get("oos") or {}
    row = (f"{k:<9} side={str(v.get('side')):<5} H={v.get('horizon_sec')}s "
           f"mu={v.get('mu')} OOS_mean_y={oos.get('mean_y')} n_eff={oos.get('n_eff')} "
           f"win={oos.get('win_rate')}")
    (opened if v.get("allow") else closed).append(row)
print(f"== 开门 {len(opened)} ==")
for r in opened:
    print("  ✅ " + r)
print(f"== 关门 {len(closed)} ==")
for r in closed[:4]:
    print("     " + r)

# 把宇宙改成"训练器实际在算的成交活跃币"(门只对它们有意义)
from backend.services import lane_registry as reg  # noqa: E402
lane = reg.get_lane("mm_asterdex") or {}
meta = dict(lane.get("meta") or {})
coins = [k for k in (g.get("gates") or {}).keys()]
print(f"\n训练器覆盖的币({len(coins)}):{coins}")
uni = list(meta.get("symbols") or [])
print(f"当前宇宙:{uni}")
add = [c for c in coins if c not in uni]
if add:
    new_uni = list(dict.fromkeys(uni + add))
    meta["symbols"] = new_uni
    meta["flow_universe_sync"] = {"at": g.get("as_of"), "added": add}
    reg.upsert_lane("mm_asterdex", meta=meta) if hasattr(reg, "upsert_lane") else None
    print(f"⇒ 已把 {add} 加入宇宙(合计 {len(new_uni)})")
else:
    print("⇒ 宇宙已包含全部")
