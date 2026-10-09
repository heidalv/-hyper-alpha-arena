# -*- coding: utf-8 -*-
"""[h799e] 全链路检查:报价在不在、成交窗口有没有成交。"""
import importlib.util
import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
d = json.loads(open(r"D:\001Alpha\Hyper-Alpha-Arena\logs\mm_lane_status.json", encoding="utf-8").read())
print(f"ticks={d.get('ticks')} fills_ph={d.get('fills_per_hour')} side={d.get('side_counts')}")
print(f"pause={d.get('lane_pause_counts')}")
sk = d.get("skip_counts") or {}
print("skip 计数(前 14):")
for k, v in sorted(sk.items(), key=lambda x: -x[1])[:14]:
    print(f"  {k} = {v}")
print("各币状态(quote_bid/ask, qty):")
for sym, s in (d.get("states") or {}).items():
    print(f"  {sym:<10} qty={float(s.get('qty') or 0):>10.4f} "
          f"quote=({float(s.get('quote_bid') or 0):.6g},{float(s.get('quote_ask') or 0):.6g})")
