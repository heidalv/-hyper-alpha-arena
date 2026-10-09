# -*- coding: utf-8 -*-
"""[h856] 复现 active_flow 的 NoneType 异常(探索门参数:mu=None, side=buy)。"""
import io
import sys
import traceback
from pathlib import Path

ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)
from backend.services.market_maker.runner import SymbolState, PlannedFill  # noqa: E402
from backend.services.market_maker.active_flow import active_flow_decision  # noqa: E402
from backend.services.market_maker.core import InventoryBook, Position  # noqa: E402


def dec():
    d = type("D", (), {})()
    d.bid = d.ask = 0.0
    d.fills = []
    d.skip = ""
    d.exit_path = ""
    d.action = ""
    d.regime = ""
    return d


# ① 空仓进场(mu=None + explore_entry=True)
st = SymbolState("BTC")
bk = InventoryBook()
d1 = dec()
try:
    active_flow_decision(
        state=st, mid=100, ofi=0.5, trend_bp=0, bb=99.9, ba=100.1, now_ts=1000.0,
        fill_notional=0, taker_fee_bp=4, sl_bp=40, tp_bp=60, max_hold_sec=90,
        flow_thresh=0.15, local_book=bk, dec=d1, PlannedFill=PlannedFill,
        maker_fee_bp=0, allow_entry=True, model_side="buy", model_mu=None,
        regime="R1", equity=10000, vol_300s_bp=8, explore_entry=True,
        roundtrip_root=Path(r"D:\001Alpha\Hyper-Alpha-Arena"))
    print(f"① 进场 OK:skip={d1.skip} bid={d1.bid}")
except Exception:
    print("① 进场异常:")
    traceback.print_exc(limit=4)

# ② 有仓 + mu=None(离场阶梯)
st2 = SymbolState("BTC")
bk2 = InventoryBook()
bk2.positions["BTC"] = Position(qty=0.01, avg_px=100.0, avg_mid=100.0,
                                opened_ts=900.0, last_ts=900.0)
d2 = dec()
try:
    active_flow_decision(
        state=st2, mid=100, ofi=0.5, trend_bp=0, bb=99.9, ba=100.1, now_ts=1100.0,
        fill_notional=0, taker_fee_bp=4, sl_bp=40, tp_bp=60, max_hold_sec=90,
        flow_thresh=0.15, local_book=bk2, dec=d2, PlannedFill=PlannedFill,
        maker_fee_bp=0, allow_entry=True, model_side="buy", model_mu=None,
        regime="R1", equity=10000, vol_300s_bp=8, explore_entry=True,
        roundtrip_root=Path(r"D:\001Alpha\Hyper-Alpha-Arena"))
    print(f"② 离场 OK:skip={d2.skip} ask={d2.ask}")
except Exception:
    print("② 离场异常:")
    traceback.print_exc(limit=4)
