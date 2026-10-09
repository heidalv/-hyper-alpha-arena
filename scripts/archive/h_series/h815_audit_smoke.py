# -*- coding: utf-8 -*-
"""[审计] 冒烟测试:新状态(无 flow_hold_sec/flow_mu)+ 持仓 ⇒ 离场阶梯是否可用。

复现路径:worker 重启后从 DB 恢复 state(QPS 里没有 flow_* 字段)⇒
active_flow 读 state.flow_hold_sec ⇒ AttributeError ⇒ runner 捕获 ⇒
`active_flow_error`,不挂单不平仓 ⇒ **持仓失管**(文档要求:报错也不能跳过离场阶梯)。
"""
import sys
import traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")

from backend.services.market_maker.runner import SymbolState, PlannedFill  # noqa: E402
from backend.services.market_maker.active_flow import active_flow_decision  # noqa: E402
from backend.services.market_maker.core import InventoryBook  # noqa: E402
from types import SimpleNamespace  # noqa: E402

print("SymbolState 是否有 flow_hold_sec:", hasattr(SymbolState("T"), "flow_hold_sec"))
print("SymbolState 是否有 flow_mu     :", hasattr(SymbolState("T"), "flow_mu"))

# 模拟"重启后恢复的持仓":有仓,但没有 flow_* 字段
st = SymbolState("NEAR")
st.qty = 2.0
st.avg_px = 4.80
st.opened_ts = 1000.0
book = InventoryBook()
book.positions["NEAR"] = type(book.positions.get("X") or SimpleNamespace())()
from backend.services.market_maker.core import Position  # noqa: E402
book.positions["NEAR"] = Position(qty=2.0, avg_px=4.80, avg_mid=4.80, opened_ts=1000.0)
dec = SimpleNamespace(fills=[], skip="", action="", exit_path="", bid=0.0, ask=0.0,
                      regime="", sigma_norm=0.0)

try:
    active_flow_decision(
        state=st, mid=4.81, ofi=0.2, trend_bp=5.0, bb=4.805, ba=4.815,
        now_ts=1100.0, fill_notional=0.0, taker_fee_bp=4.0, sl_bp=40.0, tp_bp=60.0,
        max_hold_sec=90.0, flow_thresh=0.15, local_book=book, dec=dec,
        PlannedFill=PlannedFill, maker_fee_bp=0.0, regime="R1", book_stale=False,
        equity=150.0, vol_300s_bp=8.0, seg_low=4.80, seg_high=4.82, seg_sell=1000.0,
        seg_buy=1000.0, same_side_n=1, roundtrip_root=None,
        allow_entry=False, model_side=None, model_mu=None,
    )
    print(f"✓ 未抛错 | skip={dec.skip} bid={dec.bid} ask={dec.ask} action={dec.action}")
except Exception as e:
    print(f"✗ 抛错:{type(e).__name__}: {e}")
    traceback.print_exc(limit=3)
