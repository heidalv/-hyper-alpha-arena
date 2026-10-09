# -*- coding: utf-8 -*-
"""[桥 17:58] 新协议(流交易 + 三段门)复核。"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")


def _j(name):
    p = ROOT / "data" / name
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


g = _j("flow_gate_last.json")
print("== 门(新格式)== ")
print(f"  ts={g.get('as_of')} 协议={g.get('protocol')}")
for k, v in (g.get("gates") or {}).items():
    oos = v.get("oos") or {}
    print(f"  {k:<9} allow={v.get('allow')} side={v.get('side')} mu={v.get('mu')} "
          f"oos.mean_y={oos.get('mean_y')} n_eff={oos.get('n_eff')} "
          f"reason={v.get('reason') or ''}")

d = json.loads((ROOT / "logs/mm_lane_status.json").read_text(encoding="utf-8"))
print("== 车道 ==")
print(f"  ticks={d.get('ticks')} fills/h={d.get('fills_per_hour')} "
      f"side={d.get('side_counts')}")
print(f"  skip 前 4: {dict(sorted((d.get('skip_counts') or {}).items(), key=lambda x: -x[1])[:4])}")

rt = ROOT / "data" / "flow_roundtrip_log.jsonl"
if rt.exists():
    rows = [json.loads(x) for x in rt.read_text(encoding="utf-8").splitlines() if x.strip()]
    print(f"== 往返日志(新)== {len(rows)} 条")
    for r in rows[-5:]:
        print(f"  {r.get('symbol')} {r.get('why')} y={r.get('y_bp')} fee={r.get('fee_bp')} "
              f"maker={r.get('maker')} era={r.get('era')}")
else:
    print("== 往返日志:尚无(门全关,没有往返)== ")
lp = _j("flow_learn_params.json")
print(f"== 学习参数(流白名单)== {lp}")
