# -*- coding: utf-8 -*-
"""[h823b] 用正确 API 把"成交活跃 + 门开着"的币写进车道宇宙。"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
from backend.services import lane_registry as reg  # noqa: E402

g = json.loads((ROOT / "data" / "flow_gate_last.json").read_text(encoding="utf-8"))
coins = [k for k in (g.get("gates") or {}).keys()]
lane = reg.get_lane("mm_asterdex") or {}
meta = dict(lane.get("meta") or {})
uni = list(meta.get("symbols") or [])
add = [c for c in coins if c not in uni]
if add:
    new_uni = list(dict.fromkeys(uni + add))
    meta["symbols"] = new_uni
    meta["flow_universe_sync"] = {"at": g.get("as_of"), "added": add, "reason": "trade_rich"}
    ok = reg.update_meta("mm_asterdex", meta)
    print(f"  update_meta ⇒ {ok};宇宙 {len(uni)} → {len(new_uni)}")
    print(f"  新增: {add}")
else:
    print("  宇宙已包含全部训练币")
print("  校验:", (reg.get_lane("mm_asterdex") or {}).get("meta", {}).get("symbols"))
