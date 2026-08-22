# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from backend.core.tenant import set_system_identity
from backend.database.connection import ScopedSession
from backend.services.ev_governor import audit_and_write

set_system_identity()
s = ScopedSession()
try:
    st = audit_and_write(s)
    print("\n=== 当前簇级资金分配 ===")
    for k, v in st["clusters"].items():
        print(f"  {k:<20} n={v['n']:>4} 均净EV={v['avg_net']:+.4f} net={v['net_pnl']:+8.2f} "
              f"→ {v['decision']} (mult={v['mult']})")
finally:
    s.close()
