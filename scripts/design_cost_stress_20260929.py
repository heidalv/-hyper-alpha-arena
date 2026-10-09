# -*- coding: utf-8 -*-
"""成本压力测试：在样本外窗口 B 上扫描总成本（taker+滑点）水平。"""
import sys
import zoneinfo
from datetime import datetime

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena\scripts")
import design_feasibility_sim_20260929 as S

TZ = zoneinfo.ZoneInfo("Asia/Shanghai")
t0 = int(datetime(2026, 7, 1, 0, 0, tzinfo=TZ).timestamp())
t1 = int(datetime(2026, 8, 30, 0, 0, tzinfo=TZ).timestamp())
mk, btc = S.prep_market(t0, t1)

print("cost_bp_per_side | ret% | maxDD% | net_total")
for cost in (0, 5, 10, 15, 20, 25, 30):
    S.FEE_BPS = cost / 2.0
    S.SLIP_BPS = cost / 2.0
    r = S.simulate(mk, btc, t0, t1, label="full")
    eq = r["eq"]
    dd = (eq.equity.cummax() - eq.equity).max() / eq.equity.cummax().max() * 100
    print(f"{cost:6.0f} | {r['ret']:+6.2f} | {dd:5.1f} | {r['net_total']:+8.1f}")
