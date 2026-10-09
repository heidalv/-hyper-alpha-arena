# -*- coding: utf-8 -*-
"""[h760] 用选择器自己的 _roll_l1 逐币诊断:谁因为"没有滚动窗口"被丢,谁因为占比不足。"""
import importlib.util
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
spec = importlib.util.spec_from_file_location(
    "v5", r"D:\001Alpha\Hyper-Alpha-Arena\scripts\h329_selector_v5.py")
v5 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v5)

WIDE = ["SEI", "1000SHIB", "PENDLE", "VIRTUAL", "PUMP", "WLD", "ADA", "AAVE",
        "NEAR", "ARB", "LIT", "ENA", "UNI", "XMR", "ONDO", "AVAX"]
print(f"MIN_BUCKETS={v5.MIN_BUCKETS} SPREAD_OK_SHARE_MIN={v5.SPREAD_OK_SHARE_MIN} "
      f"SPREAD_MIN_BP={v5.SPREAD_MIN_BP} ROLL_HOURS={v5.ROLL_HOURS}")
nodata, lowshare, ok = [], [], []
for s in WIDE:
    roll = v5._roll_l1(s, v5.ROLL_HOURS)
    if not roll:
        nodata.append(s)
        print(f"  {s:<10} ✗ 无滚动窗口(<{v5.MIN_BUCKETS} 桶)")
        continue
    share = v5.spread_ok_share(roll["rels"])
    if share < v5.SPREAD_OK_SHARE_MIN:
        lowshare.append(s)
        print(f"  {s:<10} △ 桶={roll['n_buckets']:>4} 占比={share:.2f}(<{v5.SPREAD_OK_SHARE_MIN})")
    else:
        ok.append(s)
        print(f"  {s:<10} ✓ 桶={roll['n_buckets']:>4} 占比={share:.2f}")
print(f"\n无数据 {len(nodata)} | 占比不足 {len(lowshare)} | 合格 {len(ok)}")
print(f"合格: {ok}")
