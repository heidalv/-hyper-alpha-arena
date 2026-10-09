# -*- coding: utf-8 -*-
"""[桥 15:58] 流交易范式复核(新版桥)。"""
import importlib.util
import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
ROOT = r"D:\001Alpha\Hyper-Alpha-Arena"


def _read(name):
    p = ROOT + "/data/" + name
    try:
        return json.loads(open(p, encoding="utf-8").read())
    except Exception:
        return {}


rt = _read("flow_roundtrip_last.json")
print(f"== C 往返 KPI ==")
print(f"  n={rt.get('n')} 胜率 {rt.get('win_rate', 0)*100 if rt.get('win_rate') is not None else 0:.0f}% "
      f"平均往返净 {rt.get('avg_net_bp', 0):+.2f}bp 平均持仓 {rt.get('avg_hold_sec', 0):.0f}s")
for k, v in (rt.get("by_exit") or {}).items():
    print(f"    {k:<16} n={v['n']:>3} 往返净 {v['avg_net_bp']:+.2f}bp")
fe = _read("flow_edge_last.json")
print(f"== B 流边 ==")
print(f"  可交易币: {fe.get('flow_tradeable')} (as_of {fe.get('as_of')})")
d = json.loads(open(ROOT + "/logs/mm_lane_status.json", encoding="utf-8").read())
print(f"== F 车道 ==")
print(f"  fills_ph={d.get('fills_per_hour')} day={d.get('day_pnl_usd')} eq={d.get('equity')}")
print(f"  side={d.get('side_counts')} pause={d.get('lane_pause_counts')}")
sk = d.get("skip_counts") or {}
print(f"  skip 前 6: {dict(sorted(sk.items(), key=lambda x: -x[1])[:6])}")
