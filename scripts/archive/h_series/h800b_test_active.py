# -*- coding: utf-8 -*-
"""[h800b] 直接测试 active_flow_decision(找裸 except 吞掉的异常)。"""
import sys
import traceback
from types import SimpleNamespace

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")

from backend.services.market_maker.active_flow import active_flow_decision
from backend.services.market_maker.runner import PlannedFill

state = SimpleNamespace(
    symbol="NEAR", qty=0.0, avg_px=0.0, opened_ts=0.0, last_ts=0.0, mid_hist=[1.0]*20)
dec = SimpleNamespace(fills=[], skip="", action="", exit_path="")

class Book:
    def __init__(self):
        self.positions = {}
    def qty(self, s):
        return self.positions.get(s, SimpleNamespace(qty=0.0)).qty
    def apply_fill(self, **kw):
        pos = self.positions.setdefault(kw["symbol"], SimpleNamespace(qty=0.0, avg_px=0.0, opened_ts=0.0))
        pos.qty += kw["qty"] if kw["side"] == "buy" else -kw["qty"]
        pos.avg_px = kw["fill_px"]
        return {"spread_usd": 0.0, "price_usd": 0.0, "fee_usd": 0.0, "position_id": "mm:TEST:1"}

try:
    r = active_flow_decision(
        state=state, mid=4.8, ofi=0.5, trend_bp=5.0, bb=4.79, ba=4.81,
        now_ts=1000.0, fill_notional=30.0, taker_fee_bp=4.0,
        sl_bp=40.0, tp_bp=60.0, max_hold_sec=300.0, flow_thresh=0.3,
        local_book=Book(), dec=dec, PlannedFill=PlannedFill, maker_fee_bp=0.0)
    print("返回:", r)
    print("fills:", [(f.symbol, f.side, f.qty, f.px, f.is_flatten, f.exit_path) for f in dec.fills])
    print("skip:", dec.skip, "| action:", dec.action, "| exit_path:", dec.exit_path)
except Exception:
    traceback.print_exc()
