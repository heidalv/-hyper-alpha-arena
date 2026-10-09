# -*- coding: utf-8 -*-
"""[h799b] OFI 分布测量:校准 ofi_require 阈值(0.3 太严,15 分钟 0 成交)。"""
import importlib.util
import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
_spec = importlib.util.spec_from_file_location(
    "h425_trial", r"D:\001Alpha\Hyper-Alpha-Arena\scripts\h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)
import psycopg

# OFI 60s 值来自哪个表/字段?跑一下现存的 OFI 存储
with psycopg.connect(h.read_env_dsn(), autocommit=True) as c, c.cursor() as cur:
    # 车道状态里的每币 ofi
    d = json.loads(open(r"D:\001Alpha\Hyper-Alpha-Arena\logs\mm_lane_status.json", encoding="utf-8").read())
    states = d.get("states") or {}
    ofis = []
    for sym, s in states.items():
        v = s.get("ofi")
        if v is not None:
            ofis.append((sym, float(v)))
    if ofis:
        vals = sorted(x[1] for x in ofis)
        n = len(vals)
        print(f"当前各币 OFI 值(n={n}):")
        for sym, v in sorted(ofis, key=lambda x: abs(x[1]), reverse=True)[:8]:
            print(f"  {sym:<10} ofi={v:+.3f}")
        print(f"  |ofi| 分位: p50={sorted(abs(v) for v in vals)[n//2]:.3f} "
              f"p75={sorted(abs(v) for v in vals)[int(n*0.75)]:.3f} "
              f"p90={sorted(abs(v) for v in vals)[int(n*0.9)]:.3f}")
        ge03 = sum(1 for v in vals if abs(v) >= 0.3)
        print(f"  |ofi|≥0.3 的币: {ge03}/{n} = {ge03/max(1,n)*100:.0f}%")
        print(f"  ⇒ 若阈值 0.3:只有 {ge03}/{n} 的币能建仓;若阈值 0.1: {sum(1 for v in vals if abs(v)>=0.1)}/{n}")
    else:
        print("状态里没有 ofi 字段")
