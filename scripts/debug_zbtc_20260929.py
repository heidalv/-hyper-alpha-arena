# -*- coding: utf-8 -*-
"""Debug: z_btc_mom 为什么返回 None。"""
import sys
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
import time
from backend.services.full_auto import edge_gate as eg

now = time.time()
rows = eg._btc_1d(now)
print("rows n =", len(rows), "first =", rows[:2] if rows else None, "last =", rows[-2:] if rows else None)
print("now =", now, "max_ts =", max(t for t, _ in rows) if rows else None)
print("z =", eg.z_btc_mom(now))
